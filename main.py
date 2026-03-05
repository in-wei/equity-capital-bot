from fastapi import FastAPI, Request, HTTPException
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import os

# 根路徑：用來確認伺服器是否活著
@app.get("/")
async def root():
    return {"status": "online", "message": "LINE Bot server is running! ✅"}

# 從環境變數拿取（Railway/Render 要在 Variables 設定）
CHANNEL_SECRET = os.getenv("Chanal_Secert","Chanal_Secert")       # ← 一定要設這個！
CHANNEL_ACCESS_TOKEN = os.getenv("Line_Channel_Token","Line_Channel_Token")

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
line_bot_api = MessagingApi(ApiClient(configuration))

handler = WebhookHandler(CHANNEL_SECRET)   # ← 關鍵！這裡初始化 handler
@app.get("/health")
async def health_check():
    return {"status": "healthy", "uptime": "running"}

@app.post("/callback")
async def callback(request: Request):
    if not handler:
        raise HTTPException(500, detail="Channel secret not set")

    signature = request.headers.get("X-Line-Signature")
    if not signature:
        raise HTTPException(400, detail="Missing signature")

    body_bytes = await request.body()
    body = body_bytes.decode("utf-8")

    print("Webhook received → body:", body[:200])  # 只印前200字避免 log 爆

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        raise HTTPException(400, detail="Invalid signature")
    except Exception as e:
        print("Webhook error:", str(e))
        raise HTTPException(500, detail=str(e))

    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    import os

    port = int(os.getenv("PORT", 8000))  # Railway/Render 會設 PORT，fallback 8000 給本地測試
    uvicorn.run(
        "main:app",               # "檔案名:app"，如果你的檔案叫 app.py 就改成 "app:app"
        host="0.0.0.0",           # 一定要 0.0.0.0！
        port=port,
        log_level="info"          # 方便看 log
    )
