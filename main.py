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
    "response_prefix": "bot", # 回應前綴
    "mode": "normal",         # 機器人模式 (e.g., normal, debug)
    "rate_limit": 5,          # 每分鐘訊息限制
    "is_active": True,        # 【新增】布林參數範例
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
    
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text="準備分析...")  # echo 回傳相同文字
    )

    
    if text.startswith("分析 "):
        stock_code = text.split(" ")[1].upper() + ".TW"  # e.g., "2330" → "2330.TW"  #這個變動感覺怪怪的
        #CONFIG["tracked_stocks"].append(stock_code)  # 記住股票  #好像是多餘的東西
        analysis = analyze_stock_trend(stock_code)
        reply_text = f"{prefix}：{analysis}"
    else:
        reply_text = f"{prefix}：{text}"  # 原 echo
    
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)  # echo 回傳相同文字
    )


# 新增：股票趨勢分析函式
def analyze_stock_trend(stock_code: str) -> str:
    try:
        # 抓取最近 1 個月數據（可調成每天定時跑）
        stock = yf.Ticker(stock_code)
        print("副程式stock -> " + stock)
        hist = stock.history(period="1mo")  # 歷史數據
        if hist.empty:
            return f"無法抓取 {stock_code} 數據，請檢查代碼。"

        # 簡單計算趨勢 (e.g., 收盤價平均、上升/下降)
        close_prices = hist['Close'].tolist()
        avg_close = sum(close_prices) / len(close_prices)
        trend = "上升" if close_prices[-1] > avg_close else "下降"
        ma5 = sum(close_prices[-5:]) / 5 if len(close_prices) >= 5 else avg_close  # 5 日均線

        # 準備提示給 Ollama
        prompt = f"""
        分析以下台灣股票 {stock_code} 的趨勢數據（最近1個月收盤價：{close_prices}）：
        - 整體趨勢：{trend}
        - 5 日均線：{ma5}
        - 建議進出場時機（考慮下次開盤前）。
        用自然語言總結，簡短專業。
        """

        # 用 Ollama 生成分析
        ollama.client.host = OLLAMA_HOST  # 如果用遠端
        response = ollama.chat(
            model="llama3.2",  # 或你的模型
            messages=[{"role": "user", "content": prompt}]
        )
        ai_analysis = response["message"]["content"]

        return ai_analysis
    except Exception as e:
        return f"分析錯誤：{str(e)}。請檢查股票代碼或網路。"






if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))  # Railway 會設 PORT，本地 fallback 8000
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
