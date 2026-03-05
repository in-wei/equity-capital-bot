from fastapi import FastAPI, Request, HTTPException
from linebot.v3.messaging import MessagingApi, Configuration, ApiClient
from linebot.v3.webhooks import MessageEvent, TextMessageContent
import os

# 從環境變數拿取（Railway/Render 要在 Variables 設定）
CHANNEL_SECRET = os.getenv("Chanal_Secert","Chanal_Secert")       # ← 一定要設這個！
CHANNEL_ACCESS_TOKEN = os.getenv("Line_Channel_Token","Line_Channel_Token")

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
line_bot_api = MessagingApi(ApiClient(configuration))

handler = WebhookHandler(CHANNEL_SECRET)   # ← 關鍵！這裡初始化 handler


@app.route('/')
def index():
    """首頁：顯示產品或歡迎訊息"""
    return redirect("hello world")

