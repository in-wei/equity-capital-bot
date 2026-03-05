from fastapi import FastAPI, Request, HTTPException
from linebot.v3.messaging import MessagingApi, Configuration, ApiClient
from linebot.v3.webhooks import MessageEvent, TextMessageContent
import os

app = FastAPI()
configuration = Configuration(access_token=os.getenv('Line_Channel_Token'))
line_bot_api = MessagingApi(ApiClient(configuration))

@app.post("/callback")
async def callback(request: Request):
    # 這裡驗證 signature（用 line-bot-sdk 內建 middleware 更簡單）
    body = await request.body()
    signature = request.headers.get('X-Line-Signature')
    # ... 驗證邏輯 ...
    
    events = ...  # 解析 body
    for event in events:
        if isinstance(event, MessageEvent) and isinstance(event.message, TextMessageContent):
            line_bot_api.reply_message(
                reply_token=event.reply_token,
                messages=[{"type": "text", "text": event.message.text}]
            )
    return "OK"
