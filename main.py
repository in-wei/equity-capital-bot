from fastapi import FastAPI, Request, HTTPException
from linebot.v3.messaging import MessagingApi, Configuration, ApiClient
from linebot.v3.webhooks import MessageEvent, TextMessageContent
import os

app = FastAPI()
configuration = Configuration(access_token=os.getenv('Line_Channel_Token'))
line_bot_api = MessagingApi(ApiClient(configuration))

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
