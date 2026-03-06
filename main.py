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

# --- 1. 設定你的 LINE Bot 資訊 (請替換為你的實際值) ---
# 建議使用環境變數來儲存這些敏感資訊
YOUR_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
YOUR_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")  # Ollama 伺服器 URL

if not YOUR_CHANNEL_ACCESS_TOKEN or not YOUR_CHANNEL_SECRET:
    raise ValueError("缺少 LINE_CHANNEL_ACCESS_TOKEN 或 LINE_CHANNEL_SECRET 環境變數")

# --- 2. 應用程式初始化 ---
app = FastAPI()
line_bot_api = LineBotApi(YOUR_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(YOUR_CHANNEL_SECRET)
#configuration = Configuration(access_token=YOUR_CHANNEL_ACCESS_TOKEN)
#line_bot_api = MessagingApi(ApiClient(configuration))
#handler = WebhookHandler(YOUR_CHANNEL_SECRET)

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

# --- 4. Webhook 接收點 (處理所有來自 LINE 的請求) ---

# 根路徑：用來確認伺服器是否活著
@app.get("/")
async def root():
    now = datetime.now(ZoneInfo("Asia/Taipei"))
    return {
        "time": now.strftime("%Y/%m/%d %H:%M:%S"),
        "status": "online",
        "message": "✅ LINE Bot server is running!"
    }

@app.get("/debug-secret")
async def debug():
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
    signature = request.headers.get("X-Line-Signature")
    body_bytes = await request.body()
    body = body_bytes.decode("utf-8")

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        return {"detail": "Invalid signature"}, 400
    except Exception as e:
        print(f"Error: {str(e)}")
        return {"detail": "Server error"}, 500

    return {"status": "ok"}

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event: MessageEvent):
    text = event.message.text
    print("get message" + text)

    CONFIG["user_id"] = event.source.user_id  # 存用戶 ID，用於 push

    if not CONFIG["is_active"] or not text:
        print("忽略無效事件")
        return
        
    def background_reply():
        try:
            if text.startswith("分析 "):
                parts = text.split(" ", 1)
                if len(parts) < 2:
                    reply_text = "請輸入 '分析 [股票代碼]' 如 '分析 2330'"
                else:
                    stock_code = parts[1].strip().upper() + ".TW"
                    CONFIG["tracked_stocks"].append(stock_code)  # 加回跟踪
                    analysis = analyze_stock_trend(stock_code)
                    reply_text = f"{CONFIG['response_prefix']}：\n{analysis}\n\n免責聲明：本分析僅供參考，非投資建議。"
            else:
                reply_text = f"{CONFIG['response_prefix']}：你想對 {text} 做什麼呢? Ex: 分析 2330"

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=reply_text)
            )
        except Exception as e:
            print(f"Reply 失敗: {str(e)} - 嘗試 push")
            # fallback 用 push (token 失效時)
            line_bot_api.push_message(CONFIG["user_id"], TextSendMessage(text="分析出錯，請重試。"))

    Thread(target=background_reply).start()  # 背景執行

# 新增：股票趨勢分析函式
def analyze_stock_trend(stock_code: str) -> str:
    try:
        stock = yf.Ticker(stock_code)
        end = datetime.now()
        start = end - timedelta(days=30)
        hist = stock.history(start=start, end=end)
        if hist.empty:
            return f"無法抓取 {stock_code} 數據，請檢查代碼。"

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

        print(f"請求 Ollama: {prompt}")
        response = ollama.chat(
            model="llama3.2",
            messages=[{"role": "user", "content": prompt}],
            options={"host": OLLAMA_HOST}
        )
        return response["message"]["content"]
    except Exception as e:
        return f"分析錯誤：{str(e)}"

# 定時分析（每天晚上 18:00 跑）
def daily_analysis():
    if not CONFIG["tracked_stocks"]:
        return

    for code in set(CONFIG["tracked_stocks"]):  # 去重
        analysis = analyze_stock_trend(code)
        msg = f"每日跟進 {code}：\n{analysis}\n\n免責聲明：僅供參考。"
        if CONFIG["user_id"]:  # push 給最後用戶（生產需存多用戶）
            line_bot_api.push_message(CONFIG["user_id"], TextSendMessage(text=msg))

scheduler = BackgroundScheduler()
scheduler.add_job(daily_analysis, CronTrigger(hour=18, minute=0, timezone='Asia/Taipei'))
scheduler.start()




if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))  # Railway 會設 PORT，本地 fallback 8000
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
