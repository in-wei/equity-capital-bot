# =============================================================================
# 股票追蹤 LINE Bot（使用 yfinance + Groq + Google Sheets / 記憶體模式）
# =============================================================================

import sys
import os
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from threading import Thread
import requests

import yfinance as yf
import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression

from fastapi import FastAPI, Request
import uvicorn
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

from openai import OpenAI
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from google.oauth2 import service_account
import gspread

# ─── 1. 全域設定與常數 ──────────────────────────────────────────────────────

COMMAND_ALIASES = {
    "help":     ["/help", "幫助", "指令", "功能", "menu", "commands"],
    "add":      ["/add", "/新增", "/添加", "/加入", "add", "新增", "添加", "加入"],
    "remove":   ["/remove", "/del", "/刪除", "/移除", "remove", "del", "刪除", "移除"],
    "list":     ["/list", "/清單", "/我的清單", "list", "清單", "tracked", "我的追蹤"],
    "push_on":  ["/push on", "/推播開", "/開啟推播", "push on", "push 开", "開推播"],
    "push_off": ["/push off", "/推播關", "/關閉推播", "push off", "push 关", "關推播"],
    "analyze":  ["/分析", "/analyze", "分析", "查", "stock", "trend", "檢視"],
}

# 後綴嘗試順序（影響股票代碼自動補完的優先級）
SUFFIX_PRIORITY = [
    "",      # 美股、歐股等無後綴優先
    ".TW",   # 台股主板
    ".TWO",  # 台股上櫃
    ".HK",   # 港股
    ".T",    # 日本東證
    ".NS", ".BO", ".SS", ".SZ", ".AX", ".TO", ".L", ".F",
    # 可自行擴充
]

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive"
]

# 執行時全域狀態
CONFIG = {
    "response_prefix": "bot",
    "rate_limit": 5,
    "is_active": True,
}

USER_SETTINGS = {}               # 記憶體模式下暫存使用者資料
M_Local_Memorry = False          # 是否使用記憶體模式（Google Sheets 失敗時）

# ─── 2. 環境變數讀取與檢查 ─────────────────────────────────────────────────

YOUR_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
YOUR_CHANNEL_SECRET      = os.getenv("LINE_CHANNEL_SECRET")
GROQ_API_KEY             = os.getenv("GROQ_API_KEY")
SHEET_ID                 = os.getenv("SHEET_ID")
GOOGLE_CREDENTIALS_JSON  = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")

if not YOUR_CHANNEL_ACCESS_TOKEN or not YOUR_CHANNEL_SECRET:
    raise ValueError("缺少 LINE_CHANNEL_ACCESS_TOKEN 或 LINE_CHANNEL_SECRET")

# ─── 3. 初始化 FastAPI、LineBot、Google Sheets ──────────────────────────────

app = FastAPI()
line_bot_api = LineBotApi(YOUR_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(YOUR_CHANNEL_SECRET)

start_service = datetime.now(ZoneInfo("Asia/Taipei"))

# Google Sheets 相關全域變數
gc = None
worksheet_stocks   = None
worksheet_settings = None

def init_google_sheets() -> bool:
    """嘗試連線 Google Sheets，若失敗則切換到記憶體模式"""
    global gc, worksheet_stocks, worksheet_settings

    if not SHEET_ID or not GOOGLE_CREDENTIALS_JSON:
        print("缺少 SHEET_ID 或 GOOGLE_CREDENTIALS_JSON → 使用記憶體模式")
        return False

    try:
        try:
            creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
            print("JSON 解析成功，client_email:", creds_dict.get("client_email"))
            print("private_key 前幾個字元:", creds_dict.get("private_key", "")[:50])
        except json.JSONDecodeError as e:
            print("JSON 格式完全錯誤！", e)
        creds = service_account.Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        gc = gspread.authorize(creds)
        spreadsheet = gc.open_by_key(SHEET_ID)

        # 追蹤股票工作表
        try:
            worksheet_stocks = spreadsheet.worksheet("tracked_stocks")
        except gspread.exceptions.WorksheetNotFound:
            worksheet_stocks = spreadsheet.add_worksheet(title="tracked_stocks", rows=1000, cols=4)
            worksheet_stocks.append_row(["user_id", "stock_code", "added_at", "memo"])

        # 使用者設定工作表（推播開關等）
        try:
            worksheet_settings = spreadsheet.worksheet("user_settings")
        except gspread.exceptions.WorksheetNotFound:
            worksheet_settings = spreadsheet.add_worksheet(title="user_settings", rows=1000, cols=4)
            worksheet_settings.append_row(["user_id", "push_enabled", "last_updated", "notes"])

        print("Google Sheets 初始化成功")
        return True

    except Exception as e:
        print(f"Google Sheets 初始化失敗: {e}")
        return False


M_Local_Memorry = not init_google_sheets()

# ─── 4. 健康檢查與 Debug 路由 ──────────────────────────────────────────────

@app.get("/")
async def root():
    now = datetime.now(ZoneInfo("Asia/Taipei"))
    return {
        "time": now.strftime("%Y/%m/%d %H:%M:%S"),
        "ago": str(now - start_service),
        "status": "online",
        "message": "✅ LINE Bot server is running!"
    }

@app.get("/debug-secret")
async def debug_secret():
    token  = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "未設定")
    secret = os.getenv("LINE_CHANNEL_SECRET",      "未設定")
    return {
        "token_length":  len(token),
        "secret_length": len(secret),
        "note": "secret 通常 32 字元"
    }

# ─── 5. LINE Webhook 核心 ──────────────────────────────────────────────────

@app.post("/callback")
async def callback(request: Request):
    signature = request.headers.get("X-Line-Signature")
    body_bytes = await request.body()
    body = body_bytes.decode("utf-8")

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        return {"detail": "Invalid signature"}, 400
    except Exception as e:
        print(f"Webhook 錯誤: {e}")
        return {"detail": "Server error"}, 500

    return {"status": "ok"}


@handler.add(MessageEvent, message=TextMessage)
def handle_message(event: MessageEvent):
    text = event.message.text.strip()
    if not text:
        return

    text_lower = text.lower()
    matched_cmd = None
    arg_part = ""

    # 尋找匹配的指令
    for cmd, aliases in COMMAND_ALIASES.items():
        for alias in aliases:
            alias_lower = alias.lower()
            if text_lower.startswith(alias_lower) or text_lower == alias_lower:
                matched_cmd = cmd
                arg_part = text[len(alias):].strip() if len(text) > len(alias) else ""
                break
        if matched_cmd:
            break

    # 沒匹配到指令，但看起來像股票代碼 → 當作分析
    if matched_cmd is None and len(text.split()) <= 2 and (text.isalnum() or '.' in text):
        matched_cmd = "analyze"
        arg_part = text

    user_id = event.source.user_id

    # 確保使用者資料存在（記憶體模式）
    if user_id not in USER_SETTINGS:
        USER_SETTINGS[user_id] = {"tracked_stocks": set(), "push_enabled": True}

    def background_reply():
        try:
            if matched_cmd == "help":
                reply_text = (
                    "可用指令（中英文皆可）：\n"
                    "• 幫助 /help\n"
                    "• 新增追蹤 /add /新增 [代碼]\n"
                    "• 移除追蹤 /del /移除 [代碼]\n"
                    "• 查看清單 /list /清單\n"
                    "• 開啟推播 /push on /推播開\n"
                    "• 關閉推播 /push off /推播關\n"
                    "• 分析股票 /分析 [代碼] [期間]（預設1y）\n"
                    "\n範例：/分析 TSLA 6mo 或 直接輸入 2330"
                )

            elif matched_cmd == "add":
                reply_text = _handle_add(user_id, arg_part)

            elif matched_cmd == "remove":
                reply_text = _handle_remove(user_id, arg_part)

            elif matched_cmd == "list":
                reply_text = _handle_list(user_id)

            elif matched_cmd in ("push_on", "push_off"):
                reply_text = _handle_push_toggle(user_id, matched_cmd == "push_on")

            elif matched_cmd == "analyze":
                reply_text = _handle_analyze(arg_part)

            else:
                reply_text = f"{CONFIG['response_prefix']}：你說「{text}」… 要分析股票嗎？試試：/分析 2330 或 直接輸入代碼"

            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

        except Exception as e:
            print(f"指令處理失敗: {e}")
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="處理時發生錯誤，請稍後再試～")
            )

    Thread(target=background_reply, daemon=True).start()


# ─── 指令處理小函式（保持 handle_message 乾淨） ─────────────────────────────

def _handle_add(user_id: str, arg: str) -> str:
    if not arg:
        return "請提供股票代碼，例如：/新增 2330 或 /add AAPL"
    code, info = resolve_stock_code(arg.upper())
    if code is None:
        return info

    if M_Local_Memorry:
        USER_SETTINGS[user_id]["tracked_stocks"].add(code)
        count = len(USER_SETTINGS[user_id]["tracked_stocks"])
    else:
        if is_stock_tracked(user_id, code):
            return f"你已經在追蹤 {code} 了～"
        add_tracked_stock(user_id, code)
        count = len(get_user_tracked_stocks(user_id))

    return f"已新增追蹤：{code}（{info}）\n目前共 {count} 檔"


def _handle_remove(user_id: str, arg: str) -> str:
    if not arg:
        return "請提供要移除的代碼，例如：/移除 2330"
    code = arg.strip().upper()
    target_code, _ = resolve_stock_code(code)
    target_code = target_code or code

    if M_Local_Memorry:
        s = USER_SETTINGS[user_id]["tracked_stocks"]
        if target_code in s:
            s.remove(target_code)
            return f"已移除 {target_code}（剩餘 {len(s)} 檔）"
        return f"你的清單中沒有 {code}"
    else:
        if remove_tracked_stock(user_id, target_code):
            count = len(get_user_tracked_stocks(user_id))
            return f"已移除 {target_code}（剩餘 {count} 檔）"
        return f"你的清單中沒有 {code}"


def _handle_list(user_id: str) -> str:
    if M_Local_Memorry:
        stocks = sorted(USER_SETTINGS[user_id]["tracked_stocks"])
        push_on = USER_SETTINGS[user_id]["push_enabled"]
    else:
        stocks = sorted(get_user_tracked_stocks(user_id))
        push_on = get_push_enabled(user_id)

    status = "已開啟" if push_on else "已關閉"
    if not stocks:
        return f"你目前沒有追蹤任何股票\n每日推播：{status}"
    return f"追蹤清單（{len(stocks)}檔）：\n" + "\n".join(stocks) + f"\n\n每日推播：{status}"


def _handle_push_toggle(user_id: str, enable: bool) -> str:
    if M_Local_Memorry:
        USER_SETTINGS[user_id]["push_enabled"] = enable
    else:
        set_push_enabled(user_id, enable)
    status = "開啟" if enable else "關閉"
    return f"每日推播已{status}（晚上18:00更新）"


def _handle_analyze(arg: str) -> str:
    parts = arg.split(maxsplit=1)
    code_str = parts[0].strip().upper() if parts else ""
    period = parts[1].strip() if len(parts) > 1 else "1y"

    if not code_str:
        return "請提供股票代碼，例如：/分析 2330 或 分析 AAPL 6mo"

    code, info = resolve_stock_code(code_str)
    if code is None:
        return info

    analysis = analyze_stock_trend(code, period)
    return f"{CONFIG['response_prefix']}：\n{analysis}\n（{code} {info}）"

# ─── 6. 股票代碼解析與技術分析核心 ─────────────────────────────────────────

def resolve_stock_code(raw_code: str) -> tuple[str | None, str]:
    """嘗試自動補上常見後綴，找出可用的 yfinance 代碼"""
    raw_code = raw_code.strip().upper()
    if '.' in raw_code:
        return raw_code, f"已指定後綴 .{raw_code.split('.')[-1]}"

    for suffix in SUFFIX_PRIORITY:
        test_code = raw_code + suffix
        try:
            if not yf.Ticker(test_code).history(period="1mo").empty:
                return test_code, suffix if suffix else "美股/無後綴"
        except:
            continue

    msg = (
        f"無法辨識 {raw_code}，建議嘗試：\n"
        "- 台股：2330 或 2330.TW / 8081.TWO\n"
        "- 美股：AAPL\n"
        "- 港股：9988 或 9988.HK\n"
        "- 日股：7203 或 7203.T"
    )
    return None, msg


def analyze_stock_trend(stock_code: str, period: str = "1y") -> str:
    """核心技術分析函式：抓資料 → 計算指標 → 產生 Prompt → 呼叫 Groq LLM"""
    try:
        stock = yf.Ticker(stock_code)
        hist = stock.history(period=period)
        if hist.empty or len(hist) < 50:
            return f"資料不足（僅 {len(hist)} 筆），請檢查代碼或期間。"

        df = hist.copy()
        close = df['Close']
        high  = df['High']
        low   = df['Low']
        volume = df['Volume']

        # 基本統計與均線
        avg_close = close.mean()
        trend = "上升" if close.iloc[-1] > avg_close else "下降"
        ma5  = close.rolling(5).mean().iloc[-1]
        ma20 = close.rolling(20).mean().iloc[-1]

        # MACD
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        crossover_macd = (
            "金叉 (買入訊號)" if macd.iloc[-1] > signal.iloc[-1] and macd.iloc[-2] <= signal.iloc[-2] else
            "死叉 (賣出訊號)" if macd.iloc[-1] < signal.iloc[-1] and macd.iloc[-2] >= signal.iloc[-2] else
            "無明顯訊號"
        )

        # KD
        k = 100 * (close - low.rolling(14).min()) / (high.rolling(14).max() - low.rolling(14).min())
        d = k.rolling(3).mean()
        crossover_kd = (
            "金叉 (買入)" if k.iloc[-1] > d.iloc[-1] and k.iloc[-2] <= d.iloc[-2] else
            "死叉 (賣出)" if k.iloc[-1] < d.iloc[-1] and k.iloc[-2] >= d.iloc[-2] else
            "無訊號"
        )
        kd_signal = "超買 (>80)" if k.iloc[-1] > 80 else "超賣 (<20)" if k.iloc[-1] < 20 else "中性"

        # RSI
        delta = close.diff()
        up = delta.clip(lower=0).rolling(14).mean()
        down = -delta.clip(upper=0).rolling(14).mean()
        rs = up / down
        rsi = 100 - (100 / (1 + rs))

        # Bollinger Bands
        bb_mid = close.rolling(20).mean()
        bb_std = close.rolling(20).std()
        bb_upper = bb_mid + 2 * bb_std
        bb_lower = bb_mid - 2 * bb_std

        # 成交量變化
        vol_ma5 = volume.rolling(5).mean().iloc[-1]
        vol_change = (volume.iloc[-1] / vol_ma5 - 1) * 100 if vol_ma5 > 0 else 0

        # 盤內外盤（當日分鐘資料）
        daily = stock.history(period="1d", interval="1m")
        ib_ob_signal = "無盤內數據"
        inner_ratio = outer_ratio = 50.0
        if not daily.empty:
            deltas = np.diff(daily['Close'])
            vol = daily['Volume'].iloc[1:]
            inner_vol = vol[deltas < 0].sum()
            outer_vol = vol[deltas > 0].sum()
            total = inner_vol + outer_vol
            if total > 0:
                inner_ratio = inner_vol / total * 100
                outer_ratio = 100 - inner_ratio
                ib_ob_signal = (
                    "外盤強 (買力主導)" if outer_ratio > 50 else
                    "內盤強 (賣力主導)" if inner_ratio > 50 else
                    "平衡"
                )

        # 簡單線性回歸預測（未來5天）
        X = np.arange(len(close)).reshape(-1, 1)
        model = LinearRegression().fit(X, close.values)
        future_x = np.arange(len(close), len(close) + 5).reshape(-1, 1)
        predicted = model.predict(future_x)
        future_trend = "預測上漲" if predicted[-1] > close.iloc[-1] else "預測下跌"

        # ── 產生給 LLM 的嚴格格式 Prompt ───────────────────────────────────
        prompt = f"""
你是一位專業台股技術分析師，請嚴格遵守以下格式回覆，總長度控制在 250 字以內，不要改變任何標題或結構：
**股票代碼**：{stock_code}（{period}）
**整體趨勢**：上升 / 下降 / 盤整
**進場時機**：短期 / 中期 / 無（附1句理由）
**退場時機**：短期 / 中期 / 無（附1句理由）
**關鍵訊號摘要**：
• MACD：{crossover_macd}
• KD：{crossover_kd} / {kd_signal}
• RSI：{rsi.iloc[-1]:.2f}
• 布林通道：中軌 {bb_mid.iloc[-1]:.2f} / 價格位置
• 內盤外盤：{ib_ob_signal} ({inner_ratio:.1f}% / {outer_ratio:.1f}%)
• 成交量變化：{vol_change:+.1f}%
**短期預測**：{future_trend}（約 {predicted[-1]:.0f}）
**綜合建議**：一句話總結
免責聲明：本分析僅供參考，非投資建議。
"""

        client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=GROQ_API_KEY)
        resp = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=300,
            top_p=0.9
        )

        return resp.choices[0].message.content.strip()

    except Exception as e:
        print(f"analyze_stock_trend 錯誤: {e}")
        return f"分析錯誤：{str(e)}。請檢查股票代碼或網路。"

# ─── 7. Google Sheets 輔助函式 ──────────────────────────────────────────────

def get_user_tracked_stocks(user_id: str) -> set:
    if not worksheet_stocks:
        return set()
    try:
        return {r["stock_code"] for r in worksheet_stocks.get_all_records() if r["user_id"] == user_id}
    except:
        return set()


def is_stock_tracked(user_id: str, stock_code: str) -> bool:
    if not worksheet_stocks:
        return False
    try:
        return any(r["user_id"] == user_id and r["stock_code"] == stock_code
                   for r in worksheet_stocks.get_all_records())
    except:
        return False


def add_tracked_stock(user_id: str, stock_code: str, memo: str = ""):
    if not worksheet_stocks:
        return
    now = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M:%S")
    worksheet_stocks.append_row([user_id, stock_code, now, memo])


def remove_tracked_stock(user_id: str, stock_code: str) -> bool:
    if not worksheet_stocks:
        return False
    try:
        records = worksheet_stocks.get_all_records()
        for i, row in enumerate(records, start=2):
            if row["user_id"] == user_id and row["stock_code"] == stock_code:
                worksheet_stocks.delete_rows(i)
                return True
        return False
    except:
        return False


def get_push_enabled(user_id: str) -> bool:
    if not worksheet_settings:
        return True
    try:
        for row in worksheet_settings.get_all_records():
            if row["user_id"] == user_id:
                return row.get("push_enabled", "TRUE").upper() == "TRUE"
        # 沒找到 → 預設開啟並寫入
        set_push_enabled(user_id, True)
        return True
    except:
        return True


def set_push_enabled(user_id: str, enabled: bool):
    if not worksheet_settings:
        return
    try:
        records = worksheet_settings.get_all_records()
        row_index = None
        for i, row in enumerate(records, start=2):
            if row["user_id"] == user_id:
                row_index = i
                break

        now = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M:%S")
        value = "TRUE" if enabled else "FALSE"

        if row_index:
            worksheet_settings.update(f"B{row_index}", value)
            worksheet_settings.update(f"C{row_index}", now)
        else:
            worksheet_settings.append_row([user_id, value, now, ""])
    except Exception as e:
        print(f"set_push_enabled 失敗: {e}")

# ─── 8. 定時推播任務 ──────────────────────────────────────────────────────

def daily_analysis():
    """每天 18:00 對所有開啟推播的使用者，推播其追蹤清單的分析"""
    print("=== 定時分析開始 ===")
    tz = ZoneInfo("Asia/Taipei")
    if datetime.now(tz).weekday() in (5, 6):
        print("六、日不傳送")
        return

    if M_Local_Memorry:
        for uid, setting in USER_SETTINGS.items():
            if not setting["push_enabled"] or not setting["tracked_stocks"]:
                continue
            for code in sorted(setting["tracked_stocks"]):
                try:
                    analysis = analyze_stock_trend(code, "1y")
                    line_bot_api.push_message(uid, TextSendMessage(
                        text=f"每日跟進 {code}：\n{analysis}"
                    ))
                except Exception as e:
                    print(f"推播失敗 {uid} {code}: {e}")
    else:
        try:
            settings = worksheet_settings.get_all_records()
            active_users = {
                r["user_id"] for r in settings
                if r.get("push_enabled", "FALSE").upper() == "TRUE"
            }

            from collections import defaultdict
            user_stocks = defaultdict(set)
            for r in worksheet_stocks.get_all_records():
                if r["user_id"] in active_users:
                    user_stocks[r["user_id"]].add(r["stock_code"])

            for uid, stocks in user_stocks.items():
                if not stocks:
                    continue
                for code in sorted(stocks):
                    try:
                        analysis = analyze_stock_trend(code, "1y")
                        line_bot_api.push_message(uid, TextSendMessage(
                            text=f"每日跟進 {code}：\n{analysis}"
                        ))
                    except Exception as e:
                        print(f"推播失敗 {uid} {code}: {e}")
        except Exception as e:
            print(f"定時推播整體錯誤: {e}")

    print("=== 定時分析結束 ===")


# ─── 9. 排程與啟動 ─────────────────────────────────────────────────────────

scheduler = BackgroundScheduler()
scheduler.add_job(daily_analysis, CronTrigger(hour=18, minute=0, timezone='Asia/Taipei'))
scheduler.start()
print("每日 18:00 推播任務已排程")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    print(f"啟動 uvicorn @ port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
