from fastapi import FastAPI, Request, HTTPException
from linebot.v3.messaging import MessagingApi, Configuration, ApiClient
from linebot.v3.webhooks import MessageEvent, TextMessageContent
import os

app = FastAPI()
configuration = Configuration(access_token=os.getenv('Line_Channel_Token'))
line_bot_api = MessagingApi(ApiClient(configuration))

@app.post("/callback")
async def callback(request: Request):
    # 取得請求標頭中的 X-Line-Signature
    signature = request.headers['X-Line-Signature']

    # 取得請求主體文字
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    # 處理 webhook 主體
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        print("Invalid signature. Please check your channel access token/secret.")
        abort(400)

    return 'OK'
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    """
    處理接收到的文字訊息，並解析指令以修改或顯示參數。
    """
    text = event.message.text.strip()
    reply_text = text
    
    # 發送回應
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )

if __name__ == "__main__":
    # 在本機運行時，將 debug 設為 True，方便開發
    # 在部署到正式環境時，請將 debug 設為 False
    print("LINE Bot Server 啟動中...")
    # 為了讓 LINE Bot 運作，你需要將這個服務暴露在網路上 (e.g. 使用 ngrok)
    app.run(host='0.0.0.0', port=os.environ.get('PORT', 5000), debug=True)
