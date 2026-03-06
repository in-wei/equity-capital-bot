from fastapi import FastAPI, Request, HTTPException
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageSendMessage
import uvicorn
import os
from datetime import datetime
from zoneinfo import ZoneInfo
import yfinance as yf  # 新增：抓股票數據
import ollama  # 新增：AI 分析
from threading import Thread  # 背景 reply
from apscheduler.schedulers.background import BackgroundScheduler  # 定時
from apscheduler.triggers.cron import CronTrigger
from openai import OpenAI
from sklearn.linear_model import LinearRegression  # 新加：簡單預測
import numpy as np
import matplotlib.pyplot as plt
import io
import pandas as pd  # 確保有
import requests

print("=== 程式啟動開始 ===")
print("Python 版本檢查：import sys; print(sys.version)")

# --- 1. 設定你的 LINE Bot 資訊 (請替換為你的實際值) ---
# 建議使用環境變數來儲存這些敏感資訊
YOUR_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
YOUR_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
OLLAMA_HOST = os.getenv("OLLAMA_HOST")  # Ollama 伺服器 URL

print(f"讀取環境變數 - TOKEN: {'有值' if YOUR_CHANNEL_ACCESS_TOKEN else '無'}")
print(f"讀取環境變數 - SECRET: {'有值' if YOUR_CHANNEL_SECRET else '無'}")
print(f"OLLAMA_HOST: {OLLAMA_HOST}")

if not YOUR_CHANNEL_ACCESS_TOKEN or not YOUR_CHANNEL_SECRET:
    raise ValueError("缺少 LINE_CHANNEL_ACCESS_TOKEN 或 LINE_CHANNEL_SECRET 環境變數")

# --- 2. 應用程式初始化 ---
app = FastAPI()
line_bot_api = LineBotApi(YOUR_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(YOUR_CHANNEL_SECRET)

# --- 3. 模擬參數設定 (在記憶體中儲存，實際應用中應使用資料庫) ---
# 這些參數可以在聊天室中被修改
CONFIG = {
    "response_prefix": "bot",
    "mode": "normal",
    "rate_limit": 5,  # 未實作，可加
    "is_active": True,
    "tracked_stocks": [],  # 跟進股票清單
    "user_id": ""  # 暫存用戶 ID（生產需存 DB，每用戶不同）
}

stock_trend = {}
startService = datetime.now(ZoneInfo("Asia/Taipei"))
# --- 4. Webhook 接收點 (處理所有來自 LINE 的請求) ---

# 根路徑：用來確認伺服器是否活著
@app.get("/")
async def root():
    print("有人訪問 / 根路徑")
    now = datetime.now(ZoneInfo("Asia/Taipei"))
    serviceAgo = now - startService
    return {
        "time": now.strftime("%Y/%m/%d %H:%M:%S"),
        "Ago":serviceAgo,
        "status": "online",
        "message": "✅ LINE Bot server is running!"
    }

@app.get("/debug-secret")
async def debug():
    print("有人訪問 /debug-secret")
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

#測試ollama連線
@app.get("/test-ollama")
async def test_ollama():
    try:
        response = ollama.list()  # 列出模型，測試連線
        return {"status": "ok", "models": response}
    except Exception as e:
        return {"status": "error", "message": str(e)}

#測試Grod連線
@app.get("/test-groq")
async def test_groq():
    try:
        client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=os.getenv("GROQ_API_KEY"))
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": "你好"}]
        )
        return {"status": "ok", "reply": response.choices[0].message.content}
    except Exception as e:
        return {"status": "error", "message": str(e)}

#Line Bot 使用
@app.post("/callback")
async def callback(request: Request):
    print("收到 webhook 請求")
    signature = request.headers.get("X-Line-Signature")
    body_bytes = await request.body()
    body = body_bytes.decode("utf-8")
    print(f"Webhook body preview: {body[:200]}...")

    try:
        handler.handle(body, signature)
        print("handler.handle 執行完成")
    except InvalidSignatureError:
        print("InvalidSignatureError 發生")
        return {"detail": "Invalid signature"}, 400
    except Exception as e:
        print(f"Webhook 其他錯誤: {str(e)}")
        return {"detail": "Server error"}, 500

    return {"status": "ok"}

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event: MessageEvent):
    text = event.message.text
    print(f"收到 user_id: {event.source.user_id} | 文字訊息: '{text}'")

    CONFIG["user_id"] = event.source.user_id  # 存用戶 ID，用於 push

    if not CONFIG["is_active"] or not text:
        print("忽略無效事件")
        return
        
    def background_reply():
        print("進入背景 reply 執行緒")
        try:
            if text.startswith("/分析 "):
                parts = text.split(" ")
                if len(parts) < 2:
                    reply_text = "格式錯誤，請輸入 '分析 [股票代碼] [期間]' 如 '分析 2330 1y'"
                else:
                    stock_code = parts[1].strip().upper() + ".TW"
                    period = parts[2].strip() if len(parts) > 2 else "1y"  # 預設 1y
                    print(f"解析股票代碼: {stock_code} | 期間: {period}")
                    CONFIG["tracked_stocks"].append(stock_code)  # 加回跟踪
                    #analysis = analyze_stock_trend(stock_code)
                    #reply_text = f"{CONFIG['response_prefix']}：\n{analysis}\n\n免責聲明：本分析僅供參考，非投資建議。"
                    if period not in ["1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max"]:
                        reply_text = "期間格式錯誤，請用 '1y'、'5y' 等有效值。"
                    else:
                        analysis = analyze_stock_trend(stock_code, period)
                        reply_text = f"{CONFIG['response_prefix']}：\n{analysis}"
            elif text.startswitch("/幫助"): 
                reply_text = "/分析 [股票代碼] [1d|5d|1mo|3mo|6mo|1y|2y|5y|10y|ytd|max]\nex:/分析 2330 1y"
            else:
                reply_text = f"{CONFIG['response_prefix']}：你想對 {text} 做什麼呢? Ex: 分析 2330 1y"

            print(f"準備回覆: {reply_text[:100]}...")
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=reply_text)
            )
        except Exception as e:
            print(f"Reply 失敗: {str(e)} - 嘗試 push")
            # fallback 用 push (token 失效時)
            if CONFIG["user_id"]:
                try:
                    line_bot_api.push_message(CONFIG["user_id"], TextSendMessage(text="分析出錯，請稍後重試。"))
                    print("push_message 成功")
                except Exception as push_e:
                    print(f"Push 也失敗: {str(push_e)}")

    Thread(target=background_reply).start()  # 背景執行

# 新增：股票趨勢分析函式
def analyze_stock_trend_old(stock_code: str) -> str:
    print(f"開始分析股票: {stock_code}")
    try:
        stock = yf.Ticker(stock_code)
        print(f"yfinance Ticker 建立成功: {stock}")
        #end = datetime.now()        #應該是抓錯時間
        #start = end - timedelta(days=30)
        #hist = stock.history(start=start, end=end)
        stock = yf.Ticker(stock_code)
        hist = stock.history(period="6mo")  # 歷史數據
        print(f"抓到歷史數據筆數: {len(hist)}")

        if hist.empty:
            return f"無法抓取 {stock_code} 數據，請檢查代碼或網路。"

        close_prices = hist['Close'].tolist()
        avg_close = sum(close_prices) / len(close_prices)
        trend = "上升" if close_prices[-1] > avg_close else "下降"
        ma5 = sum(close_prices[-5:]) / 5 if len(close_prices) >= 5 else avg_close

        client = OpenAI(
            base_url="https://api.groq.com/openai/v1",
            api_key=os.getenv("GROQ_API_KEY")  # 在 Railway Variables 加這個
        )
        
        prompt = f"""
        分析台灣股票 {stock_code} 最近1個月收盤價：{close_prices}。
        - 整體趨勢：{trend}
        - 5 日均線：{ma5}
        - 建議進出場時機（考慮下次開盤前）。
        簡短專業總結。
        """
        
        print(f"prompt 長度: {len(prompt)} 字元")

        #Ollama 最小容量至少1GB，改用線上AI
        #response = ollama.chat(
        #    model="llama3.2",
        #    messages=[{"role": "user", "content": prompt}],
        #    options={"host": OLLAMA_HOST}
        #)
        #ai_analysis = response["message"]["content"]

        response = client.chat.completions.create(
            model="llama-3.1-8b-instant", 
            # model="llama-3.1-70b-versatile",  # 如果想用更強的（額度夠再換）
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=300
        )
        ai_analysis = response.choices[0].message.content
        print("分析完成")
        return ai_analysis
    except Exception as e:
        print(f"分析錯誤: {str(e)}")
        return f"分析錯誤：{str(e)}。請檢查網路或 API 金鑰。"

def analyze_stock_trend(stock_code: str, period: str = "1y") -> str:
    try:
        stock = yf.Ticker(stock_code)
        hist = stock.history(period=period)
        if hist.empty or len(hist) < 50:
            return f"無法抓取足夠 {stock_code} 數據（筆數 {len(hist)}），請檢查代碼或期間。"

        df = hist.copy()
        close = df['Close']
        high = df['High']
        low = df['Low']
        volume = df['Volume']

        # 基本趨勢與均線
        avg_close = close.mean()
        trend = "上升" if close.iloc[-1] > avg_close else "下降"
        ma5 = close.rolling(5).mean().iloc[-1]
        ma20 = close.rolling(20).mean().iloc[-1]

        # MACD 完整計算與最近10日
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        histogram = macd - signal
        macd_recent = macd.tail(10).tolist()
        signal_recent = signal.tail(10).tolist()
        crossover_macd = "金叉 (買入訊號)" if macd.iloc[-1] > signal.iloc[-1] and macd.iloc[-2] <= signal.iloc[-2] else \
                         "死叉 (賣出訊號)" if macd.iloc[-1] < signal.iloc[-1] and macd.iloc[-2] >= signal.iloc[-2] else "無明顯訊號"

        # KD
        k = 100 * (close - low.rolling(14).min()) / (high.rolling(14).max() - low.rolling(14).min())
        d = k.rolling(3).mean()
        k_recent = k.tail(10).tolist()
        d_recent = d.tail(10).tolist()
        crossover_kd = "金叉 (買入)" if k.iloc[-1] > d.iloc[-1] and k.iloc[-2] <= d.iloc[-2] else \
                       "死叉 (賣出)" if k.iloc[-1] < d.iloc[-1] and k.iloc[-2] >= d.iloc[-2] else "無訊號"
        kd_signal = "超買 (>80)" if k.iloc[-1] > 80 else "超賣 (<20)" if k.iloc[-1] < 20 else "中性"

        # RSI
        delta = close.diff()
        up = delta.clip(lower=0).rolling(14).mean()
        down = -delta.clip(upper=0).rolling(14).mean()
        rs = up / down
        rsi = 100 - (100 / (1 + rs))

        # Bollinger Bands
        bb_mid = close.rolling(20).mean()
        bb_std = close.rolling(20).std()
        bb_upper = bb_mid + 2 * bb_std
        bb_lower = bb_mid - 2 * bb_std

        # OBV
        obv = (np.sign(close.diff()) * volume).cumsum().iloc[-1]

        # 成交量變化率
        vol_ma5 = volume.rolling(5).mean().iloc[-1]
        vol_change = (volume.iloc[-1] / vol_ma5 - 1) * 100 if vol_ma5 > 0 else 0

        # 盤內外盤推估
        daily_hist = stock.history(period="1d", interval="1m")
        inner_ratio = outer_ratio = 50
        ib_ob_signal = "無盤內數據"
        if not daily_hist.empty:
            deltas = np.diff(daily_hist['Close'])
            vol = daily_hist['Volume'].iloc[1:]
            inner_vol = vol[deltas < 0].sum()
            outer_vol = vol[deltas > 0].sum()
            total = inner_vol + outer_vol
            if total > 0:
                inner_ratio = inner_vol / total * 100
                outer_ratio = 100 - inner_ratio
                ib_ob_signal = "外盤強 (買力主導)" if outer_ratio > 50 else "內盤強 (賣力主導)" if inner_ratio > 50 else "平衡"

        # 線性回歸預測
        X = np.arange(len(close)).reshape(-1, 1)
        y = close.values
        model = LinearRegression().fit(X, y)
        future_days = 5
        future_x = np.arange(len(close), len(close) + future_days).reshape(-1, 1)
        predicted = model.predict(future_x).tolist()
        future_trend = "預測上漲" if predicted[-1] > close.iloc[-1] else "預測下跌"

        # 完整 prompt（所有原始資料都保留）
        prompt = f"""
        分析台灣股票 {stock_code} 最近 {period} 技術指標（收盤價最後20日：{close.tail(20).tolist()}）：
        - 整體趨勢：{trend}
        - 5日均線：{ma5:.2f} | 20日均線：{ma20:.2f}
        - RSI(14)：{rsi.iloc[-1]:.2f}（>70超買，<30超賣）
        - MACD 最近10日：{macd_recent}
        - Signal 線最近10日：{signal_recent}
        - MACD 訊號：{crossover_macd}
        - KD %K 最近10日：{k_recent}
        - KD %D 最近10日：{d_recent}
        - KD 訊號：{crossover_kd} / {kd_signal}
        - Bollinger Bands：中軌 {bb_mid.iloc[-1]:.2f} / 上軌 {bb_upper.iloc[-1]:.2f} / 下軌 {bb_lower.iloc[-1]:.2f}
        - OBV 最新值：{obv:,.0f}
        - 成交量較5日均量變化：{vol_change:.1f}%
        - 盤內外盤推估：內盤 {inner_ratio:.2f}% / 外盤 {outer_ratio:.2f}% → {ib_ob_signal}
        - 未來5日簡單預測價格：{predicted}
        - 未來趨勢：{future_trend}

        綜合以上所有指標，給出明確的短期與中期進出場建議。
        用自然語言總結，簡短專業。
        """

        print(f"Ollama prompt 長度: {len(prompt)} 字元")

        client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=os.getenv("GROQ_API_KEY"))
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=500  # 加長一點，容納所有資料
        )

        ai_analysis = response.choices[0].message.content
        return ai_analysis + "\n\n免責聲明：本分析僅供參考，非投資建議。"

    except Exception as e:
        print(f"analyze_stock_trend 錯誤: {str(e)}")
        return f"分析錯誤：{str(e)}。請檢查股票代碼或網路。"










def upload_image_to_imgur(buf):
    buf.seek(0)
    response = requests.post(
        'https://api.imgur.com/3/image',
        headers={'Authorization': 'Client-ID 你的Imgur Client ID'},  # 註冊 Imgur API
        files={'image': buf.read()}
    )
    return response.json()['data']['link'] if response.status_code == 200 else None

# 定時分析（每天晚上 18:00 跑）
def daily_analysis():
    print("=== 定時分析任務觸發 ===")
    print(f"目前追蹤股票: {CONFIG['tracked_stocks']}")
    print(f"目前 user_id: {CONFIG['user_id']}")
    # ... 原分析邏輯 ...




scheduler = BackgroundScheduler()
print("BackgroundScheduler 已建立")
scheduler.add_job(daily_analysis, CronTrigger(hour=18, minute=0, timezone='Asia/Taipei'))
print("每日 18:00 分析任務已排程")
scheduler.start()
print("Scheduler 已啟動")

print("=== 程式啟動完成 ===")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))  # Railway 會設 PORT，本地 fallback 8000
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
