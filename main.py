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
YOUR_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
YOUR_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")

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
    loc_dt = datetime.datetime.now()
    time_del = datetime.timedelta(hours=8)
    new_dt = loc_dt + time_del
    return {"time":new_dt.strftime("%Y/%m/%d %H:%M:%S"),"status": "online", "message": "✅ LINE Bot server is running!"}

@app.route("/callback", methods=['POST'])
async def callback():
    print("POST /callback hit - minimal version, no Request object")
    return {"status": "ok"}  # 立刻回 200，什麼都不做

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
