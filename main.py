from fastapi import FastAPI, Request, HTTPException
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import uvicorn
import os
import datetime

app = FastAPI()

# --- 1. 設定你的 LINE Bot 資訊 (請替換為你的實際值) ---
# 建議使用環境變數來儲存這些敏感資訊
YOUR_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "YOUR_CHANNEL_ACCESS_TOKEN_HERE")
YOUR_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "YOUR_CHANNEL_SECRET_HERE")

# --- 2. 應用程式初始化 ---
app = FastAPI()
line_bot_api = LineBotApi(YOUR_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(YOUR_CHANNEL_SECRET)

# --- 3. 模擬參數設定 (在記憶體中儲存，實際應用中應使用資料庫) ---
# 這些參數可以在聊天室中被修改
CONFIG = {
    "response_prefix": "bot", # 回應前綴
    "mode": "normal",         # 機器人模式 (e.g., normal, debug)
    "rate_limit": 5,          # 每分鐘訊息限制
    "is_active": True,        # 【新增】布林參數範例
}

# --- 4. Webhook 接收點 (處理所有來自 LINE 的請求) ---

# 根路徑：用來確認伺服器是否活著
@app.get("/")
async def root():
    return {"time":datetime.datetime.now(),"status": "online", "message": "✅ LINE Bot server is running!"}

@app.route("/callback", methods=['POST'])
async def callback(request: Request):
    if not handler:
        print("Webhook error: Channel secret not set!")
        raise HTTPException(status_code=500, detail="Channel secret not configured")

    signature = request.headers.get("X-Line-Signature")
    if not signature:
        print("Missing X-Line-Signature header!")
        raise HTTPException(status_code=400, detail="Missing signature")

    try:
        body_bytes = await request.body()
        body = body_bytes.decode("utf-8")
        print(f"Webhook received - Signature: {signature[:20]}... Body preview: {body[:100]}...")  # log 看內容

        # 只驗證 signature，不解析事件（Verify 階段 LINE 只發空或測試事件）
        handler.handle(body, signature)

        print("Signature valid, returning 200")
        return {"status": "success"}  # 或直接 return "OK" 也行

    except InvalidSignatureError:
        print("Invalid signature! Check if LINE_CHANNEL_SECRET matches exactly (no extra spaces).")
        raise HTTPException(status_code=400, detail="Invalid signature")
    except Exception as e:
        print(f"Unexpected error in webhook: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Server error: {str(e)}")

if __name__ == "__main__":

    port = int(os.getenv("PORT", 8000))  # Railway/Render 會設 PORT，fallback 8000 給本地測試
    uvicorn.run(
        "main:app",               # "檔案名:app"，如果你的檔案叫 app.py 就改成 "app:app"
        host="0.0.0.0",           # 一定要 0.0.0.0！
        port=port,
        log_level="info"          # 方便看 log
    )
