import sys
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from threading import Thread

import yfinance as yf
import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
import matplotlib.pyplot as plt
import io
import requests

from fastapi import FastAPI, Request
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

from openai import OpenAI
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import uvicorn

# ────────────────────────────────────────────────
# 全域設定與後綴優先順序（這裡統一管理，易修改）
# ────────────────────────────────────────────────

COMMAND_ALIASES = {
    "help":     ["/help", "幫助", "指令", "功能", "menu", "commands"],
    "add":      ["/add", "/新增", "/添加", "/加入", "add", "新增", "添加", "加入"],
    "remove":   ["/remove", "/del", "/刪除", "/移除", "remove", "del", "刪除", "移除"],
    "list":     ["/list", "/清單", "/我的清單", "list", "清單", "tracked", "我的追蹤"],
    "push_on":  ["/push on", "/推播開", "/開啟推播", "push on", "push 开", "開推播"],
    "push_off": ["/push off", "/推播關", "/關閉推播", "push off", "push 关", "關推播"],
    "analyze":  ["/分析", "/analyze", "分析", "查", "stock", "trend", "檢視"],
}

CONFIG = {
    "response_prefix": "bot",
    "mode": "normal",
    "rate_limit": 5,
    "is_active": True,
    "tracked_stocks": set(),  # 使用 set 自動去重
    "user_id": ""             # 暫存最後使用者 ID（生產建議用 DB）
}

SUFFIX_PRIORITY = [
    "",       # 美股、歐股等無後綴優先
    ".TW",    # 台股主板
    ".TWO",   # 台股上櫃
    ".HK",    # 港股
    ".T",     # 日本東證
    ".NS",    # 印度 NSE
    ".BO",    # 印度 BSE
    ".SS",    # 中國上證（舊寫法，有時用 .SH）
    ".SZ",    # 中國深證
    ".AX",    # 澳洲
    ".TO",    # 加拿大
    ".L",     # 英國
    ".F",     # 德國
    # 新增市場就在這裡加一行，例如 ".SA" 巴西
]

USER_SETTINGS = {}

# ────────────────────────────────────────────────
# 環境變數載入與檢查
# ────────────────────────────────────────────────
print("=== 程式啟動開始 ===")
print(f"Python 版本: {sys.version}")

YOUR_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
YOUR_CHANNEL_SECRET      = os.getenv("LINE_CHANNEL_SECRET")
GROQ_API_KEY             = os.getenv("GROQ_API_KEY")
OLLAMA_HOST              = os.getenv("OLLAMA_HOST")  # 可選

if not YOUR_CHANNEL_ACCESS_TOKEN or not YOUR_CHANNEL_SECRET:
    raise ValueError("缺少 LINE_CHANNEL_ACCESS_TOKEN 或 LINE_CHANNEL_SECRET")

print(f"TOKEN: {'有值' if YOUR_CHANNEL_ACCESS_TOKEN else '無'}")
print(f"SECRET: {'有值' if YOUR_CHANNEL_SECRET else '無'}")
print(f"GROQ_API_KEY: {'有值' if GROQ_API_KEY else '無'}")
print(f"OLLAMA_HOST: {OLLAMA_HOST or '未設定'}")

app = FastAPI()
line_bot_api = LineBotApi(YOUR_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(YOUR_CHANNEL_SECRET)

start_service = datetime.now(ZoneInfo("Asia/Taipei"))

# ────────────────────────────────────────────────
# 路由 - 健康檢查與 debug
# ────────────────────────────────────────────────
@app.get("/")
async def root():
    now = datetime.now(ZoneInfo("Asia/Taipei"))
    service_ago = now - start_service
    return {
        "time": now.strftime("%Y/%m/%d %H:%M:%S"),
        "ago": str(service_ago),
        "status": "online",
        "message": "✅ LINE Bot server is running!"
    }

@app.get("/debug-secret")
async def debug():
    secret = os.getenv("LINE_CHANNEL_SECRET", "未設定")
    token  = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "未設定")
    return {
        "token_length": len(token),
        "token_preview": token[:10] + "..." + token[-10:] if len(token) > 20 else token,
        "secret_length": len(secret),
        "secret_preview": secret[:10] + "..." + secret[-10:] if len(secret) > 20 else secret,
        "note": "secret 通常 32 字元"
    }

@app.get("/test-groq")
async def test_groq():
    try:
        client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=GROQ_API_KEY)
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": "你好"}]
        )
        return {"status": "ok", "reply": response.choices[0].message.content}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# ────────────────────────────────────────────────
# Webhook 核心
# ────────────────────────────────────────────────
@app.post("/callback")
async def callback(request: Request):
    signature = request.headers.get("X-Line-Signature")
    body_bytes = await request.body()
    body = body_bytes.decode("utf-8")
    print(f"Webhook body preview: {body[:200]}...")

    try:
        handler.handle(body, signature)
        print("handler.handle 完成")
    except InvalidSignatureError:
        print("InvalidSignatureError")
        return {"detail": "Invalid signature"}, 400
    except Exception as e:
        print(f"Webhook 錯誤: {str(e)}")
        return {"detail": "Server error"}, 500

    return {"status": "ok"}

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event: MessageEvent):
    text = event.message.text.strip()
    if not text:
        return

    # 轉成小寫來比對（比較保險）
    text_lower = text.lower()

    # 找出匹配的標準指令
    matched_cmd = None
    for cmd, aliases in COMMAND_ALIASES.items():
        for alias in aliases:
            # 精確開頭匹配（避免誤觸）
            if text_lower.startswith(alias.lower()) or text_lower == alias.lower():
                matched_cmd = cmd
                # 取出參數部分
                if len(alias) < len(text):
                    arg_part = text[len(alias):].strip()
                else:
                    arg_part = ""
                break
        if matched_cmd:
            break

    if matched_cmd is None:
        # 沒匹配到任何指令 → 當一般對話或提示
        reply_text = f"{CONFIG['response_prefix']}：你說「{text}」… 要分析股票嗎？試試：/分析 2330 1y 或 直接輸入股票代碼"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        return

    # ─── 以下根據 matched_cmd 處理 ────────────────────────────────
    user_id = event.source.user_id
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
                    "\n範例：/分析 TSLA 6mo   或   分析 2330"
                )

            elif matched_cmd == "add":
                if not arg_part:
                    reply_text = "請提供股票代碼，例如：/新增 2330 或 /add AAPL"
                else:
                    stock_code, suffix_info = resolve_stock_code(arg_part.upper())
                    if stock_code is None:
                        reply_text = suffix_info
                    else:
                        USER_SETTINGS[user_id]["tracked_stocks"].add(stock_code)
                        reply_text = f"已新增追蹤：{stock_code}（{suffix_info}）\n目前共 {len(USER_SETTINGS[user_id]['tracked_stocks'])} 檔"

            elif matched_cmd == "remove":
                if not arg_part:
                    reply_text = "請提供要移除的代碼，例如：/移除 2330"
                else:
                    code = arg_part.strip().upper()
                    if code in USER_SETTINGS[user_id]["tracked_stocks"]:
                        USER_SETTINGS[user_id]["tracked_stocks"].remove(code)
                        reply_text = f"已移除 {code}（剩餘 {len(USER_SETTINGS[user_id]['tracked_stocks'])} 檔）"
                    else:
                        reply_text = f"你的清單中沒有 {code}"

            elif matched_cmd == "list":
                stocks = sorted(USER_SETTINGS[user_id]["tracked_stocks"])
                push_status = "已開啟" if USER_SETTINGS[user_id]["push_enabled"] else "已關閉"
                if not stocks:
                    reply_text = "你目前沒有追蹤任何股票"
                else:
                    reply_text = f"追蹤清單（{len(stocks)}檔）：\n" + "\n".join(stocks) + f"\n\n每日推播：{push_status}"

            elif matched_cmd in ("push_on", "push_off"):
                USER_SETTINGS[user_id]["push_enabled"] = (matched_cmd == "push_on")
                status = "開啟" if USER_SETTINGS[user_id]["push_enabled"] else "關閉"
                reply_text = f"每日推播已{status}（晚上18:00更新）"

            elif matched_cmd == "analyze":
                parts = arg_part.split(maxsplit=1)
                raw_code = parts[0].strip().upper() if parts else ""
                period = parts[1].strip() if len(parts) > 1 else "1y"

                if not raw_code:
                    reply_text = "請提供股票代碼，例如：/分析 2330  或  分析 AAPL 6mo"
                else:
                    stock_code, suffix_info = resolve_stock_code(raw_code)
                    if stock_code is None:
                        reply_text = suffix_info
                    else:
                        analysis = analyze_stock_trend(stock_code, period)
                        reply_text = f"{CONFIG['response_prefix']}：\n{analysis}\n（{stock_code} {suffix_info}）"

            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

        except Exception as e:
            print(f"處理指令失敗: {e}")
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="處理時發生錯誤，請稍後再試～"))

    Thread(target=background_reply, daemon=True).start()

# ────────────────────────────────────────────────
# 核心分析函式
# ────────────────────────────────────────────────
def analyze_stock_trend(stock_code: str, period: str = "1y") -> str:
    try:
        stock = yf.Ticker(stock_code)
        hist = stock.history(period=period)
        if hist.empty or len(hist) < 50:
            return f"資料不足（僅 {len(hist)} 筆），請檢查代碼或期間。"

        df = hist.copy()
        close = df['Close']
        high = df['High']
        low = df['Low']
        volume = df['Volume']

        # 基本趨勢與均線
        avg_close = close.mean()
        trend = "上升" if close.iloc[-1] > avg_close else "下降"
        ma5 = close.rolling(5).mean().iloc[-1]
        ma20 = close.rolling(20).mean().iloc[-1]

        # MACD
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        macd_recent = macd.tail(10).round(4).tolist()
        signal_recent = signal.tail(10).round(4).tolist()
        crossover_macd = "金叉 (買入訊號)" if macd.iloc[-1] > signal.iloc[-1] and macd.iloc[-2] <= signal.iloc[-2] else \
                         "死叉 (賣出訊號)" if macd.iloc[-1] < signal.iloc[-1] and macd.iloc[-2] >= signal.iloc[-2] else "無明顯訊號"

        # KD
        k = 100 * (close - low.rolling(14).min()) / (high.rolling(14).max() - low.rolling(14).min())
        d = k.rolling(3).mean()
        k_recent = k.tail(10).round(2).tolist()
        d_recent = d.tail(10).round(2).tolist()
        crossover_kd = "金叉 (買入)" if k.iloc[-1] > d.iloc[-1] and k.iloc[-2] <= d.iloc[-2] else \
                       "死叉 (賣出)" if k.iloc[-1] < d.iloc[-1] and k.iloc[-2] >= d.iloc[-2] else "無訊號"
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

        # OBV
        obv = (np.sign(close.diff()) * volume).cumsum().iloc[-1]

        # 成交量變化率
        vol_ma5 = volume.rolling(5).mean().iloc[-1]
        vol_change = (volume.iloc[-1] / vol_ma5 - 1) * 100 if vol_ma5 > 0 else 0

        # 盤內外盤推估
        daily_hist = stock.history(period="1d", interval="1m")
        inner_ratio = outer_ratio = 50
        ib_ob_signal = "無盤內數據"
        if not daily_hist.empty:
            deltas = np.diff(daily_hist['Close'])
            vol = daily_hist['Volume'].iloc[1:]
            inner_vol = vol[deltas < 0].sum()
            outer_vol = vol[deltas > 0].sum()
            total = inner_vol + outer_vol
            if total > 0:
                inner_ratio = inner_vol / total * 100
                outer_ratio = 100 - inner_ratio
                ib_ob_signal = "外盤強 (買力主導)" if outer_ratio > 50 else "內盤強 (賣力主導)" if inner_ratio > 50 else "平衡"

        # 線性回歸預測
        X = np.arange(len(close)).reshape(-1, 1)
        y = close.values
        model = LinearRegression().fit(X, y)
        future_days = 5
        future_x = np.arange(len(close), len(close) + future_days).reshape(-1, 1)
        predicted = model.predict(future_x).tolist()
        future_trend = "預測上漲" if predicted[-1] > close.iloc[-1] else "預測下跌"

        # 強制格式化 Prompt
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

資料基礎（供參考，不要輸出）：
- 收盤價最後60日：{close.tail(60).round(2).tolist()}
- MACD 最近10日：{macd.tail(10).round(4).tolist()}
- Signal 最近10日：{signal.tail(10).round(4).tolist()}
- KD %K 最近10日：{k.tail(10).round(2).tolist()}
- KD %D 最近10日：{d.tail(10).round(2).tolist()}
"""

        print(f"prompt 長度: {len(prompt)} 字元")

        client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=os.getenv("GROQ_API_KEY"))
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=300,
            top_p=0.9
        )

        ai_analysis = response.choices[0].message.content
        print("分析完成")
        return ai_analysis

    except Exception as e:
        print(f"analyze_stock_trend 錯誤: {str(e)}")
        return f"分析錯誤：{str(e)}。請檢查股票代碼或網路。"

def resolve_stock_code(raw_code: str) -> tuple[str | None, str]:
    """
    自動解析並補完整股票代碼，返回 (最終代碼, 使用的後綴說明)
    如果無法匹配，返回 (None, 錯誤訊息)
    """
    raw_code = raw_code.strip().upper()

    # 如果已經帶後綴，直接回傳
    if '.' in raw_code:
        suffix = raw_code.split('.')[-1]
        return raw_code, f"已指定後綴 .{suffix}"

    # 依序嘗試後綴
    for suffix in SUFFIX_PRIORITY:
        test_code = raw_code + suffix
        try:
            stock = yf.Ticker(test_code)
            # 用較長期間測試，避免短資料空
            hist_test = stock.history(period="1mo")
            if not hist_test.empty:
                return test_code, suffix if suffix else "美股/無後綴"
        except Exception:
            continue

    # 全部失敗
    return None, (
        f"無法辨識 {raw_code}，建議嘗試：\n"
        "- 台股：2330 或 2330.TW / 8081.TWO\n"
        "- 美股：AAPL\n"
        "- 港股：9988 或 9988.HK\n"
        "- 日股：7203 或 7203.T"
    )

# ────────────────────────────────────────────────
# 工具函式（圖片上傳等）
# ────────────────────────────────────────────────
def upload_image_to_imgur(buf):
    buf.seek(0)
    response = requests.post(
        'https://api.imgur.com/3/image',
        headers={'Authorization': 'Client-ID 你的Imgur Client ID'},  # 註冊 Imgur API
        files={'image': buf.read()}
    )
    return response.json()['data']['link'] if response.status_code == 200 else None

# ────────────────────────────────────────────────
# 定時任務
# ────────────────────────────────────────────────
def daily_analysis():
    print("=== 定時分析開始 ===")
    # 週六日不推
    if datetime.now(ZoneInfo("Asia/Taipei")).weekday() in (5, 6):
        print("六、日不傳送")
        return
    
    for user_id, settings in USER_SETTINGS.items():
        if not settings["push_enabled"] or not settings["tracked_stocks"]:
            continue

        print(f"推播給 {user_id}，追蹤 {len(settings['tracked_stocks'])} 檔")
        for code in sorted(settings["tracked_stocks"]):
            try:
                analysis = analyze_stock_trend(code, "1y")
                line_bot_api.push_message(user_id, TextSendMessage(text=f"每日跟進 {code}：\n{analysis}"))
            except Exception as e:
                print(f"推播 {code} 到 {user_id} 失敗: {e}")
    print("=== 定時分析結束 ===")

scheduler = BackgroundScheduler()
print("BackgroundScheduler 已建立")
scheduler.add_job(daily_analysis, CronTrigger(hour=18, minute=0, timezone='Asia/Taipei'))
print("每日 18:00 分析任務已排程")
scheduler.start()
print("Scheduler 已啟動")

print("=== 程式啟動完成 ===")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
