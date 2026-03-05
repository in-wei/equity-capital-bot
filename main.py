from fastapi import FastAPI, Request, HTTPException
from linebot.v3.messaging import MessagingApi, Configuration, ApiClient
from linebot.v3.webhooks import MessageEvent, TextMessageContent
import os

# 從環境變數拿取（Railway/Render 要在 Variables 設定）
CHANNEL_SECRET = os.getenv("Line_User_Id")       # ← 一定要設這個！
CHANNEL_ACCESS_TOKEN = os.getenv("Line_Channel_Token")

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
line_bot_api = MessagingApi(ApiClient(configuration))

handler = WebhookHandler(CHANNEL_SECRET)   # ← 關鍵！這裡初始化 handler
@app.post("/callback")
async def callback(request: Request):
    signature = request.headers.get("X-Line-Signature")
    
    # 讀取 body（bytes → str）
    body_bytes = await request.body()
    body = body_bytes.decode("utf-8")
    
    # 印 log 方便 debug（在 Railway logs 會看到）
    print("Request body:", body)
    print("Signature:", signature)
    
    if not signature:
        raise HTTPException(status_code=400, detail="Missing X-Line-Signature")
    
    try:
        # 驗證 signature + 解析 events（但現在不處理，只讓它過）
        handler.handle(body, signature)
    except InvalidSignatureError:
        print("Invalid signature! Check CHANNEL_SECRET 是否正確。")
        raise HTTPException(status_code=400, detail="Invalid signature")
    except Exception as e:
        print("Other error:", str(e))
        raise HTTPException(status_code=500, detail=str(e))
    
    # 一定要回 200 OK（就算沒做事也要回）
    return {"status": "ok"}   # 或直接 return "OK" 也可以，FastAPI 會轉 200
