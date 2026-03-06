from fastapi import FastAPI, Request, HTTPException
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import uvicorn
import os
from datetime import datetime
from zoneinfo import ZoneInfo
import yfinance as yf  # 新增：抓股票數據
import ollama  # 新增：AI 分析
from threading import Thread  # 背景 reply
from apscheduler.schedulers.background import BackgroundScheduler  # 定時
from apscheduler.triggers.cron import CronTrigger

print("=== 程式啟動開始 ===")
print("Python 版本檢查：import sys; print(sys.version)")

# --- 1. 設定你的 LINE Bot 資訊 (請替換為你的實際值) ---
# 建議使用環境變數來儲存這些敏感資訊
YOUR_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
YOUR_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
OLLAMA_HOST = os.getenv("OLLAMA_HOST")  # Ollama 伺服器 URL

print(f"讀取環境變數 - TOKEN: {'有值' if YOUR_CHANNEL_ACCESS_TOKEN else '無'}")
print(f"讀取環境變數 - SECRET: {'有值' if YOUR_CHANNEL_SECRET else '無'}")
print(f"OLLAMA_HOST: {OLLAMA_HOST}")

if not YOUR_CHANNEL_ACCESS_TOKEN or not YOUR_CHANNEL_SECRET:
    raise ValueError("缺少 LINE_CHANNEL_ACCESS_TOKEN 或 LINE_CHANNEL_SECRET 環境變數")

# --- 2. 應用程式初始化 ---
app = FastAPI()
line_bot_api = LineBotApi(YOUR_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(YOUR_CHANNEL_SECRET)

# --- 3. 模擬參數設定 (在記憶體中儲存，實際應用中應使用資料庫) ---
# 這些參數可以在聊天室中被修改
CONFIG = {
    "response_prefix": "bot",
    "mode": "normal",
    "rate_limit": 5,  # 未實作，可加
    "is_active": True,
    "tracked_stocks": [],  # 跟進股票清單
    "user_id": ""  # 暫存用戶 ID（生產需存 DB，每用戶不同）
}

stock_trend = {}
startService = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y/%m/%d %H:%M:%S")
# --- 4. Webhook 接收點 (處理所有來自 LINE 的請求) ---

# 根路徑：用來確認伺服器是否活著
@app.get("/")
async def root():
    print("有人訪問 / 根路徑")
    now = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y/%m/%d %H:%M:%S")
    serviceAgo = now - startService
    return {
        "time": now,
        "Ago":serviceAgo,
        "status": "online",
        "message": "✅ LINE Bot server is running!"
    }

@app.get("/debug-secret")
async def debug():
    print("有人訪問 /debug-secret")
    secret = os.getenv("LINE_CHANNEL_SECRET")
    token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
    return {
        "token_length": len(token),
        "token_preview": token[:10] + "..." + token[-10:] if len(token) > 20 else token,
        "token_note": "",
        "secret_length": len(secret),
        "secret_preview": secret[:10] + "..." + secret[-10:] if len(secret) > 20 else secret,
        "secret_note": "Compare length with LINE console (通常 32 字元)"
    }

#Line Bot 使用
@app.post("/callback")
async def callback(request: Request):
    print("收到 webhook 請求")
    signature = request.headers.get("X-Line-Signature")
    body_bytes = await request.body()
    body = body_bytes.decode("utf-8")
    print(f"Webhook body preview: {body[:200]}...")

    try:
        handler.handle(body, signature)
        print("handler.handle 執行完成")
    except InvalidSignatureError:
        print("InvalidSignatureError 發生")
        return {"detail": "Invalid signature"}, 400
    except Exception as e:
        print(f"Webhook 其他錯誤: {str(e)}")
        return {"detail": "Server error"}, 500

    return {"status": "ok"}

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event: MessageEvent):
    text = event.message.text
    print(f"收到 user_id: {event.source.user_id} | 文字訊息: '{text}'")

    CONFIG["user_id"] = event.source.user_id  # 存用戶 ID，用於 push

    if not CONFIG["is_active"] or not text:
        print("忽略無效事件")
        return
        
    def background_reply():
        print("進入背景 reply 執行緒")
        try:
            if text.startswith("分析 "):
                parts = text.split(" ", 1)
                if len(parts) < 2:
                    reply_text = "格式錯誤，請輸入 '分析 [股票代碼]' 如 '分析 2330'"
                else:
                    stock_code = parts[1].strip().upper() + ".TW"
                    print(f"解析股票代碼: {stock_code}")
                    CONFIG["tracked_stocks"].append(stock_code)  # 加回跟踪
                    analysis = analyze_stock_trend(stock_code)
                    reply_text = f"{CONFIG['response_prefix']}：\n{analysis}\n\n免責聲明：本分析僅供參考，非投資建議。"
            else:
                reply_text = f"{CONFIG['response_prefix']}：你想對 {text} 做什麼呢? Ex: 分析 2330"

            print(f"準備回覆: {reply_text[:100]}...")
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=reply_text)
            )
        except Exception as e:
            print(f"Reply 失敗: {str(e)} - 嘗試 push")
            # fallback 用 push (token 失效時)
            if CONFIG["user_id"]:
                try:
                    line_bot_api.push_message(CONFIG["user_id"], TextSendMessage(text="分析出錯，請稍後重試。"))
                    print("push_message 成功")
                except Exception as push_e:
                    print(f"Push 也失敗: {str(push_e)}")

    Thread(target=background_reply).start()  # 背景執行

# 新增：股票趨勢分析函式
def analyze_stock_trend(stock_code: str) -> str:
    print(f"開始分析股票: {stock_code}")
    try:
        stock = yf.Ticker(stock_code)
        print(f"yfinance Ticker 建立成功: {stock}")
        #end = datetime.now()        #應該是抓錯時間
        #start = end - timedelta(days=30)
        #hist = stock.history(start=start, end=end)
        stock = yf.Ticker(stock_code)
        hist = stock.history(period="6mo")  # 歷史數據
        print(f"抓到歷史數據筆數: {len(hist)}")

        if hist.empty:
            return f"無法抓取 {stock_code} 數據，請檢查代碼或網路。"

        close_prices = hist['Close'].tolist()
        avg_close = sum(close_prices) / len(close_prices)
        trend = "上升" if close_prices[-1] > avg_close else "下降"
        ma5 = sum(close_prices[-5:]) / 5 if len(close_prices) >= 5 else avg_close

        prompt = f"""
        分析台灣股票 {stock_code} 最近1個月收盤價：{close_prices}。
        - 整體趨勢：{trend}
        - 5 日均線：{ma5}
        - 建議進出場時機（考慮下次開盤前）。
        簡短專業總結。
        """

        print(f"Ollama prompt 長度: {len(prompt)} 字元")
        response = ollama.chat(
            model="llama3.2",
            messages=[{"role": "user", "content": prompt}],
            options={"host": OLLAMA_HOST}
        )
        ai_analysis = response["message"]["content"]
        print("Ollama 分析完成")
        return ai_analysis
    except Exception as e:
        print(f"analyze_stock_trend 錯誤: {str(e)}")
        return f"分析錯誤：{str(e)}。請檢查股票代碼或網路。"

# 定時分析（每天晚上 18:00 跑）
def daily_analysis():
    print("=== 定時分析任務觸發 ===")
    print(f"目前追蹤股票: {CONFIG['tracked_stocks']}")
    print(f"目前 user_id: {CONFIG['user_id']}")
    # ... 原分析邏輯 ...




scheduler = BackgroundScheduler()
print("BackgroundScheduler 已建立")
scheduler.add_job(daily_analysis, CronTrigger(hour=18, minute=0, timezone='Asia/Taipei'))
print("每日 18:00 分析任務已排程")
scheduler.start()
print("Scheduler 已啟動")

print("=== 程式啟動完成 ===")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))  # Railway 會設 PORT，本地 fallback 8000
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
