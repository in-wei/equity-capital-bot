from fastapi import FastAPI, Request, HTTPException
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import uvicorn
import os

app = FastAPI()

# 根路徑：用來確認伺服器是否活著
@app.get("/")
async def root():
    return {"status": "online", "message": "LINE Bot server is running!"}

if __name__ == "__main__":

    port = int(os.getenv("PORT", 8000))  # Railway/Render 會設 PORT，fallback 8000 給本地測試
    uvicorn.run(
        "main:app",               # "檔案名:app"，如果你的檔案叫 app.py 就改成 "app:app"
        host="0.0.0.0",           # 一定要 0.0.0.0！
        port=port,
        log_level="info"          # 方便看 log
    )
