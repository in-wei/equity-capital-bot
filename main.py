from fastapi import FastAPI, Request, HTTPException
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import uvicorn
import os

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
    return {"status": "online", "message": "✅ LINE Bot server is running!"}

@app.route("/callback", methods=['POST'])
def callback():
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

# --- 5. 訊息處理器 (處理所有文字訊息事件) ---
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    """
    處理接收到的文字訊息，並解析指令以修改或顯示參數。
    """
    text = event.message.text.strip()
    reply_text = None
    
    # 檢查是否為參數修改指令：/set key=value
    # 這裡使用正則表達式來匹配並擷取 key 和 value
    set_match = re.match(r"^/set\s+(\w+)\s*=\s*([^\s]+)", text)
    
    if set_match:
        # 匹配成功，取得 key 和 value
        key = set_match.group(1).lower()
        value_str = set_match.group(2)
        
        if key in CONFIG:
            # 嘗試轉換數值型別 (如果原本是數字)
            try:
                # 判斷原始值的型別並嘗試轉換
                
                # 【修正點 1】：處理布林型別 (True/False)
                if isinstance(CONFIG[key], bool):
                    lower_str = value_str.lower()
                    if lower_str in ('true', 't', '1'):
                        value = True
                    elif lower_str in ('false', 'f', '0'):
                        value = False
                    else:
                        # 如果輸入的不是有效的布林字串，則視為無效值
                        raise ValueError(f"'{value_str}' 不是有效的布林值。請使用 True/False/t/f/1/0。")
                        
                # 【原始邏輯】：處理整數型別 (int)
                elif isinstance(CONFIG[key], int):
                    value = int(value_str)
                    
                # 【原始邏輯】：處理浮點數型別 (float)
                elif isinstance(CONFIG[key], float):
                    value = float(value_str)
                    
                # 【原始邏輯】：處理字串型別 (str) 或其他無法識別的型別
                else:
                    value = value_str # 保持字串型別
                    
            except ValueError as e:
                # 轉換失敗 (例如輸入 'abc' 給 int 或無效的布林字串)
                reply_text = f"❌ 錯誤：無法將 '{value_str}' 轉換為參數 '{key}' 所需的型別 ({type(CONFIG[key]).__name__})。\n詳細錯誤：{e}"
                # 如果轉換失敗，我們直接回覆錯誤並結束處理，不更新 CONFIG
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
                return

            # 更新參數
            CONFIG[key] = value
            
            # 回覆確認訊息
            reply_text = f"參數更新成功！\n參數：{key}\n新值：{value} (型別: {type(value).__name__})\n當前設定：{CONFIG}"
            
        else:
            reply_text = f"錯誤：找不到參數 '{key}'。可用參數有: {', '.join(CONFIG.keys())}"

    # 檢查是否為顯示設定指令：/show config
    elif text.lower() == "/show config":
        config_items = "\n".join([f"- {k}: {v} (型別: {type(v).__name__})" for k, v in CONFIG.items()])
        reply_text = f"當前機器人參數設定：\n{config_items}"
    
    # 一般訊息回應 (使用當前的 response_prefix)
    elif reply_text is None:
        prefix = CONFIG.get("response_prefix", "🤖")
        # 示範使用布林參數
        status = "啟動中" if CONFIG.get("is_active") else "已停用"
        reply_text = f"{prefix} 您好，我收到您的訊息了：『{text}』\n當前服務狀態: {status}\n\n您可以使用以下指令：\n- /show config 顯示所有參數\n- /set is_active=False 修改布林參數"

    # 發送回應
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )

if __name__ == "__main__":

    port = int(os.getenv("PORT", 8000))  # Railway/Render 會設 PORT，fallback 8000 給本地測試
    uvicorn.run(
        "main:app",               # "檔案名:app"，如果你的檔案叫 app.py 就改成 "app:app"
        host="0.0.0.0",           # 一定要 0.0.0.0！
        port=port,
        log_level="info"          # 方便看 log
    )
