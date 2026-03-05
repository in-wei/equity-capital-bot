# -*- coding: utf-8 -*-
# 方案C: 進階自適應型台股AI分析程式 (2026年3月版本)
# 功能: 
# 1. 每天抓取指定股票最近資料
# 2. 跑簡單量化回測 (使用vectorbt, 若無請pip install vectorbt)
# 3. 根據回測結果 (勝率/夏普比率) 自動修正LLM prompt或信心閾值
# 4. 用LLM (Ollama本地模型) 做自然語言分析與預測
# 5. 只在最佳時機 (信心高 + 回測好) 發LINE推播
#
# 注意:
# - 需要Ollama安裝 + 下載模型 (e.g., qwen2.5:14b)
# - LINE Messaging API: 需申請Channel Access Token
# - vectorbt: pip install vectorbt (若環境無)
# - 排程: 用cron或Task Scheduler每天執行 (e.g., 晚上8點)
# - 回測簡化為均線交叉策略, 計算最近90日績效
# - 修正邏輯: 若夏普 < 0.3, 則提高信心閾值到0.85並用保守prompt

import ollama
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import requests
import json
from datetime import datetime, timedelta
import vectorbt as vbt  # 回測套件
import numpy as np

# ======================
# 你的設定區
# ======================
STOCKS = ["2330", "2317", "2454", "2303", "2891"]  # 要監控的股票
OLLAMA_MODEL = "qwen2.5:14b"                       # Ollama模型
LINE_CHANNEL_ACCESS_TOKEN = "T0E9VvCy+fZs+SnzDQZS7tw9C3C0GIpc1p6ac5YIjdVdbk18TuVwzyrZH6nDrFDQyMQn5u9up1+83W3BlILFPllBjgdjyOS7fLrBI8JXoDFPz0gEPNKDdMLLLtbUcQ0yksg7RJTabK9oNW0WC+RF0wFIS9xybk1bpjJUhI9NTk0="  # LINE API token
LINE_USER_ID = "U9a9c1056fa76e4973b3aeed30ac1a531"                       # 推播對象
DEFAULT_MIN_CONFIDENCE = 0.75                      # 預設信心閾值
BACKTEST_DAYS = 90                                 # 回測最近天數
SHARPE_THRESHOLD = 0.3                             # 夏普低於此則修正
RISK_FREE_RATE = 0.02                              # 無風險利率 (年化)

# ======================
# 抓最近資料 (歷史用於回測, 最近30天用於LLM)
# ======================
def get_stock_data(symbol, days=BACKTEST_DAYS + 30):
    ticker = f"{symbol}.TW"
    end = datetime.today()
    start = end - timedelta(days=days)
    df = yf.download(ticker, start=start, end=end, auto_adjust=True)
    if df.empty:
        print(f"無法下載 {symbol} 資料")
        return None
    df = df[['Open', 'High', 'Low', 'Close', 'Volume']]
    return df

# ======================
# 計算技術指標
# ======================
def add_indicators(df):
    df['SMA10'] = ta.sma(df['Close'], length=10)
    df['SMA20'] = ta.sma(df['Close'], length=20)
    df['RSI14'] = ta.rsi(df['Close'], length=14)
    macd = ta.macd(df['Close'])
    df = pd.concat([df, macd], axis=1)
    return df

# ======================
# 簡單量化策略回測 (均線交叉)
# ======================
def run_backtest(df):
    df = add_indicators(df)
    # 訊號: 金叉買, 死叉賣
    entries = (df['SMA10'] > df['SMA20']) & (df['SMA10'].shift(1) <= df['SMA20'].shift(1))
    exits = (df['SMA10'] < df['SMA20']) & (df['SMA10'].shift(1) >= df['SMA20'].shift(1))
    
    # 用vectorbt模擬
    pf = vbt.PF.from_signals(
        df['Close'],
        entries=entries,
        exits=exits,
        freq='1D',  # 日頻
        fees=0.0015,  # 手續費
        slippage=0.001  # 滑價
    )
    
    # 計算指標
    total_return = pf.total_return()
    win_rate = pf.win_rate()
    sharpe = pf.sharpe_ratio(risk_free=RISK_FREE_RATE / 252)  # 日化
    
    return {
        'total_return': total_return,
        'win_rate': win_rate,
        'sharpe': sharpe
    }

# ======================
# 轉資料成文字表格給LLM
# ======================
def df_to_prompt_table(df):
    recent = df.tail(10).round(2)
    table = recent.to_string()
    return f"""
最近10天走勢：
{table}

最近收盤價：{df['Close'][-1]:.2f}
10日均線：{df['SMA10'][-1]:.2f}   20日均線：{df['SMA20'][-1]:.2f}
RSI(14)：{df['RSI14'][-1]:.1f}
MACD：{df['MACD_12_26_9'][-1]:.2f}
"""

# ======================
# LLM分析 (支援保守/正常prompt)
# ======================
def ask_llm(symbol, table_text, conservative=False):
    if conservative:
        prompt_template = """你是保守型台股分析師，偏好避險，使用繁體中文回答。
只在非常確定的情況下給看多/看空，否則給中性。"""
    else:
        prompt_template = """你是專業台股分析師，使用繁體中文回答。"""
    
    prompt = f"""{prompt_template}
以下是 {symbol} 最近走勢與指標：

{table_text}

請用以下格式嚴格回答，不要多餘文字：
看多/看空/中性 | 信心度(0.0~1.0) | 簡短理由(20字內) | 預期1-3日目標價區間

範例：
看多 | 0.82 | 均線多頭排列＋RSI低檔反彈 | 1050-1100
"""
    response = ollama.generate(model=OLLAMA_MODEL, prompt=prompt)
    text = response['response'].strip()
    
    try:
        parts = [x.strip() for x in text.split('|')]
        if len(parts) != 4:
            raise ValueError
        direction, conf_str, reason, target = parts
        conf = float(conf_str)
    except:
        return "解析失敗", 0.0, "格式錯誤", "—"
    
    return direction, conf, reason, target

# ======================
# 發送LINE推播
# ======================
def send_line_push(message):
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    payload = {
        "to": LINE_USER_ID,
        "messages": [{"type": "text", "text": message}]
    }
    try:
        r = requests.post(url, headers=headers, data=json.dumps(payload))
        print("LINE推播結果:", r.status_code, r.text)
    except Exception as e:
        print("推播失敗:", e)

# ======================
# 主流程
# ======================
if __name__ == "__main__":
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    messages = [f"台股每日AI快訊 (自適應版) {today}\n"]
    
    for symbol in STOCKS:
        df = get_stock_data(symbol)
        if df is None:
            continue
        
        # 步驟1: 回測最近90日
        backtest_df = df.head(BACKTEST_DAYS)  # 取前90日回測
        metrics = run_backtest(backtest_df)
        print(f"{symbol} 回測: 總報酬 {metrics['total_return']:.2%}, 勝率 {metrics['win_rate']:.2%}, 夏普 {metrics['sharpe']:.2f}")
        
        # 步驟2: 根據回測修正
        min_conf = DEFAULT_MIN_CONFIDENCE
        conservative_prompt = False
        if metrics['sharpe'] < SHARPE_THRESHOLD:
            min_conf = 0.85  # 提高閾值
            conservative_prompt = True
            messages.append(f"{symbol} 回測差, 採用保守模式")
        
        # 步驟3: 加指標並準備prompt
        df = add_indicators(df)
        table = df_to_prompt_table(df)
        
        # 步驟4: LLM預測
        direction, conf, reason, target = ask_llm(symbol, table, conservative=conservative_prompt)
        
        msg = f"{symbol} → {direction} (信心 {conf:.2f})\n理由：{reason}\n目標：{target}\n回測夏普: {metrics['sharpe']:.2f}"
        messages.append(msg)
        
        # 步驟5: 只在最佳時機推播 (信心 >= min_conf 且 非中性)
        if conf >= min_conf and direction != "中性":
            alert = f"【最佳訊號】{symbol} {direction}！\n{reason}\n目標 {target}\n回測OK，請評估風險"
            send_line_push(alert)
    
    # 總結推播 (無論如何都發)
    summary = "\n".join(messages)
    send_line_push(summary)
    print("每日分析完成！")
