"""
=============================================================
  TELEGRAM DAY TRADING ALERT BOT
  Powered by Python + yfinance
=============================================================
  SETUP:
  1. pip install yfinance pandas ta requests schedule
  2. Create Telegram bot via @BotFather → get BOT_TOKEN
  3. Message your bot, then visit:
     https://api.telegram.org/bot<TOKEN>/getUpdates
     to get your CHAT_ID
  4. Fill in BOT_TOKEN and CHAT_ID below
  5. Run: python trading_alert_bot.py
=============================================================
"""

import yfinance as yf
import pandas as pd
import ta
import requests
import schedule
import time
import logging
from datetime import datetime

# ─────────────────────────────────────────────
#  YOUR CONFIGURATION — EDIT THESE
# ─────────────────────────────────────────────

BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"
CHAT_ID   = "YOUR_CHAT_ID_HERE"

WATCHLIST = ["SPY", "AAPL", "TSLA", "GLD", "USO"]

CHECK_INTERVAL  = 15    # minutes between scans
RSI_OVERSOLD    = 30
RSI_OVERBOUGHT  = 70
VOLUME_SPIKE    = 1.5   # x above 20-day average volume

# ─────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("trading_bot.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  TELEGRAM
# ─────────────────────────────────────────────

def send_telegram(message: str) -> bool:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        resp = requests.post(url, data=payload, timeout=10)
        if resp.status_code == 200:
            log.info(f"Sent: {message[:60]}...")
            return True
        log.error(f"Telegram {resp.status_code}: {resp.text}")
        return False
    except Exception as e:
        log.error(f"Telegram error: {e}")
        return False


def send_startup_message():
    msg = (
        "🤖 <b>Trading Bot is LIVE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 Watching : {', '.join(WATCHLIST)}\n"
        f"⏱ Interval : Every {CHECK_INTERVAL} minutes\n"
        f"📉 RSI Buy  : ≤ {RSI_OVERSOLD}\n"
        f"📈 RSI Sell : ≥ {RSI_OVERBOUGHT}\n"
        f"🕐 Started  : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Signals will appear below. Good trading! 💹"
    )
    send_telegram(msg)

# ─────────────────────────────────────────────
#  DATA FETCH
# ─────────────────────────────────────────────

def fetch_data(ticker: str, period: str = "3mo", interval: str = "1h") -> pd.DataFrame:
    try:
        df = yf.download(ticker, period=period, interval=interval, progress=False)
        if df.empty:
            log.warning(f"No data for {ticker}")
            return pd.DataFrame()
        df.dropna(inplace=True)
        return df
    except Exception as e:
        log.error(f"Fetch error {ticker}: {e}")
        return pd.DataFrame()

# ─────────────────────────────────────────────
#  INDICATORS
# ─────────────────────────────────────────────

def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    close  = df["Close"].squeeze()
    high   = df["High"].squeeze()
    low    = df["Low"].squeeze()
    volume = df["Volume"].squeeze()

    df["RSI"]        = ta.momentum.rsi(close, window=14)
    df["MACD"]       = ta.trend.macd(close)
    df["MACD_Signal"]= ta.trend.macd_signal(close)
    df["EMA_9"]      = ta.trend.ema_indicator(close, window=9)
    df["EMA_21"]     = ta.trend.ema_indicator(close, window=21)
    df["BB_Upper"]   = ta.volatility.bollinger_hband(close)
    df["BB_Lower"]   = ta.volatility.bollinger_lband(close)
    df["ATR"]        = ta.volatility.average_true_range(high, low, close, window=14)
    df["Vol_MA20"]   = volume.rolling(20).mean()
    df["Vol_Spike"]  = volume / df["Vol_MA20"]
    return df

# ─────────────────────────────────────────────
#  SIGNAL ENGINE
# ─────────────────────────────────────────────

def generate_signals(df: pd.DataFrame, ticker: str) -> list:
    if len(df) < 52:
        return []

    signals = []
    latest  = df.iloc[-1]
    prev    = df.iloc[-2]

    def flt(val):
        v = latest[val]
        return float(v.iloc[0]) if hasattr(v, 'iloc') else float(v)

    def flt_prev(val):
        v = prev[val]
        return float(v.iloc[0]) if hasattr(v, 'iloc') else float(v)

    price  = round(flt("Close"), 4)
    rsi    = round(flt("RSI"), 2)
    macd   = flt("MACD")
    macd_s = flt("MACD_Signal")
    ema9   = flt("EMA_9")
    ema21  = flt("EMA_21")
    vol_sp = round(flt("Vol_Spike"), 2)
    atr    = round(flt("ATR"), 4)
    bb_low = flt("BB_Lower")

    p_macd   = flt_prev("MACD")
    p_macd_s = flt_prev("MACD_Signal")
    p_ema9   = flt_prev("EMA_9")
    p_ema21  = flt_prev("EMA_21")

    def make(sig_type, strength, reason, sl_mult, tp_mult):
        direction = 1 if sig_type == "BUY" else -1
        return {
            "type": sig_type, "strength": strength, "reason": reason,
            "ticker": ticker, "price": price, "rsi": rsi,
            "vol_spike": vol_sp, "atr": atr,
            "stop_loss":   round(price + direction * (-sl_mult) * atr, 4),
            "take_profit": round(price + direction * tp_mult * atr, 4),
        }

    # RSI Oversold → BUY
    if rsi <= RSI_OVERSOLD:
        signals.append(make("BUY", "STRONG", f"RSI Oversold ({rsi})", 2, 3))

    # RSI Overbought → SELL
    if rsi >= RSI_OVERBOUGHT:
        signals.append(make("SELL", "STRONG", f"RSI Overbought ({rsi})", 2, 3))

    # MACD Bullish Crossover → BUY
    if p_macd < p_macd_s and macd > macd_s:
        signals.append(make("BUY", "MODERATE", "MACD Bullish Crossover", 2, 3))

    # MACD Bearish Crossover → SELL
    if p_macd > p_macd_s and macd < macd_s:
        signals.append(make("SELL", "MODERATE", "MACD Bearish Crossover", 2, 3))

    # EMA Golden Cross → BUY
    if p_ema9 < p_ema21 and ema9 > ema21:
        signals.append(make("BUY", "MODERATE", "EMA 9/21 Golden Cross", 2, 3))

    # EMA Death Cross → SELL
    if p_ema9 > p_ema21 and ema9 < ema21:
        signals.append(make("SELL", "MODERATE", "EMA 9/21 Death Cross", 2, 3))

    # Bollinger Lower Band Touch → BUY
    if price <= bb_low * 1.005:
        signals.append(make("BUY", "WEAK", "Price at BB Lower Band", 1.5, 2))

    # Volume Spike → ALERT
    if vol_sp >= VOLUME_SPIKE:
        signals.append({
            "type": "ALERT", "strength": "INFO",
            "reason": f"Volume Spike {vol_sp}x avg",
            "ticker": ticker, "price": price, "rsi": rsi,
            "vol_spike": vol_sp, "atr": atr,
            "stop_loss": None, "take_profit": None,
        })

    return signals

# ─────────────────────────────────────────────
#  MESSAGE FORMATTER
# ─────────────────────────────────────────────

EMOJI = {
    ("BUY",   "STRONG"):   "🟢🟢",
    ("BUY",   "MODERATE"): "🟢",
    ("BUY",   "WEAK"):     "🔵",
    ("SELL",  "STRONG"):   "🔴🔴",
    ("SELL",  "MODERATE"): "🔴",
    ("SELL",  "WEAK"):     "🟠",
    ("ALERT", "INFO"):     "⚡",
}

def format_signal(signal: dict) -> str:
    em  = EMOJI.get((signal["type"], signal["strength"]), "📊")
    now = datetime.now().strftime("%H:%M:%S")

    msg = (
        f"{em} <b>{signal['type']} — {signal['ticker']}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 Reason     : {signal['reason']}\n"
        f"💰 Price      : ${signal['price']}\n"
        f"📊 RSI        : {signal['rsi']}\n"
        f"📦 Vol Spike  : {signal['vol_spike']}x\n"
    )
    if signal["stop_loss"]:
        msg += f"🛑 Stop Loss  : ${signal['stop_loss']}\n"
    if signal["take_profit"]:
        msg += f"🎯 Take Profit: ${signal['take_profit']}\n"

    msg += (
        f"💡 Strength   : {signal['strength']}\n"
        f"🕐 Time       : {now}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ <i>Not financial advice. Manage your risk.</i>"
    )
    return msg

# ─────────────────────────────────────────────
#  DAILY SUMMARY
# ─────────────────────────────────────────────

def send_daily_summary():
    lines = ["📰 <b>Daily Market Summary</b>\n━━━━━━━━━━━━━━━━━━━━"]
    for ticker in WATCHLIST:
        df = fetch_data(ticker, period="5d", interval="1d")
        if df.empty:
            continue
        df = calculate_indicators(df)
        try:
            latest = df.iloc[-1]
            price  = round(float(latest["Close"]), 2)
            rsi    = round(float(latest["RSI"]), 1)
            mood   = "🟢 Bullish" if rsi < 45 else ("🔴 Bearish" if rsi > 60 else "⚪ Neutral")
            lines.append(f"  {ticker}: ${price} | RSI {rsi} | {mood}")
        except Exception:
            continue
    lines.append(f"\n🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    send_telegram("\n".join(lines))

# ─────────────────────────────────────────────
#  MAIN SCAN
# ─────────────────────────────────────────────

def scan_all():
    log.info(f"--- Scan at {datetime.now().strftime('%H:%M:%S')} ---")
    count = 0
    for ticker in WATCHLIST:
        log.info(f"Scanning {ticker}...")
        df = fetch_data(ticker)
        if df.empty:
            continue
        df      = calculate_indicators(df)
        signals = generate_signals(df, ticker)
        for sig in signals:
            send_telegram(format_signal(sig))
            count += 1
            time.sleep(1)
    log.info(f"Done. {count} signal(s) sent." if count else "No signals this cycle.")

# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────

def main():
    log.info("🚀 Bot starting...")
    send_startup_message()

    schedule.every(CHECK_INTERVAL).minutes.do(scan_all)
    schedule.every().day.at("09:00").do(send_daily_summary)

    scan_all()  # Run immediately on startup

    log.info(f"Scheduler live. Scanning every {CHECK_INTERVAL} min.")
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
