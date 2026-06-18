"""
╔══════════════════════════════════════════════════════════════╗
║         SMONKIE TRADER — COMPLETE LIVE BACKEND v4.0         ║
╠══════════════════════════════════════════════════════════════╣
║  DATA SOURCES:                                              ║
║  1. Yahoo Finance  → Forex, Gold, ETFs (free)               ║
║  2. Deriv WebSocket → Boom/Crash/Volatility (free)          ║
║  3. Binance API    → BTC/ETH/XRP/BNB (free)                 ║
║  4. KuCoin API     → Crypto fallback for Zimbabwe           ║
╠══════════════════════════════════════════════════════════════╣
║  INSTALL:                                                   ║
║  pip install fastapi uvicorn yfinance pandas ta             ║
║           websockets requests python-binance                ║
║                                                             ║
║  OPTIONAL (for KuCoin fallback):                            ║
║  pip install python-kucoin                                  ║
║                                                             ║
║  SET ENVIRONMENT VARIABLES:                                 ║
║  BINANCE_API_KEY    = your_binance_api_key                  ║
║  BINANCE_API_SECRET = your_binance_api_secret               ║
║                                                             ║
║  RUN:                                                       ║
║  python main.py                                             ║
║                                                             ║
║  TEST:                                                      ║
║  http://localhost:8000/prices                               ║
║  http://localhost:8000/health                               ║
║  http://localhost:8000/docs                                 ║
╚══════════════════════════════════════════════════════════════╝
"""

# ── IMPORTS ───────────────────────────────────────────────────
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf
import pandas as pd
import numpy as np
import ta
import asyncio
import websockets
import json
import threading
import logging
import os
import requests
import uvicorn
from datetime import datetime

# ── LOGGING ───────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("smonkie.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ── APP SETUP ─────────────────────────────────────────────────
app = FastAPI(
    title="SMONKIE TRADER API v4.0",
    description="Live data from Yahoo Finance + Deriv + Binance",
    version="4.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── ENVIRONMENT VARIABLES ─────────────────────────────────────
# Set these before running:
# export BINANCE_API_KEY=your_key
# export BINANCE_API_SECRET=your_secret
BINANCE_KEY    = os.getenv("BINANCE_API_KEY", "")
BINANCE_SECRET = os.getenv("BINANCE_API_SECRET", "")
USE_BINANCE    = bool(BINANCE_KEY and BINANCE_SECRET)

# ── INSTRUMENT REGISTRY ───────────────────────────────────────

YAHOO_INSTRUMENTS = {
    # Forex
    "EUR/USD":  {"yahoo": "EURUSD=X", "type": "FOREX",    "decimals": 4},
    "GBP/USD":  {"yahoo": "GBPUSD=X", "type": "FOREX",    "decimals": 4},
    "USD/ZAR":  {"yahoo": "USDZAR=X", "type": "FOREX",    "decimals": 4},
    "USD/JPY":  {"yahoo": "USDJPY=X", "type": "FOREX",    "decimals": 2},
    "AUD/USD":  {"yahoo": "AUDUSD=X", "type": "FOREX",    "decimals": 4},
    "GBP/ZAR":  {"yahoo": "GBPZAR=X", "type": "FOREX",    "decimals": 4},
    "EUR/ZAR":  {"yahoo": "EURZAR=X", "type": "FOREX",    "decimals": 4},
    "USD/NGN":  {"yahoo": "USDNGN=X", "type": "FOREX",    "decimals": 2},
    "USD/KES":  {"yahoo": "USDKES=X", "type": "FOREX",    "decimals": 2},
    # Commodity
    "XAU/USD":  {"yahoo": "XAUUSD=X", "type": "COMMODITY","decimals": 2},
    # ETFs
    "SPY":      {"yahoo": "SPY",      "type": "ETF",       "decimals": 2},
    "LIT":      {"yahoo": "LIT",      "type": "ETF",       "decimals": 2},
    "COPX":     {"yahoo": "COPX",     "type": "ETF",       "decimals": 2},
}

DERIV_INSTRUMENTS = {
    # Boom indices
    "Boom 300":    {"deriv": "boom_300",  "type": "SYNTHETIC", "spike": "UP"},
    "Boom 500":    {"deriv": "boom_500",  "type": "SYNTHETIC", "spike": "UP"},
    "Boom 1000":   {"deriv": "boom_1000", "type": "SYNTHETIC", "spike": "UP"},
    # Crash indices
    "Crash 300":   {"deriv": "crash_300",  "type": "SYNTHETIC", "spike": "DOWN"},
    "Crash 500":   {"deriv": "crash_500",  "type": "SYNTHETIC", "spike": "DOWN"},
    "Crash 1000":  {"deriv": "crash_1000", "type": "SYNTHETIC", "spike": "DOWN"},
    # Volatility indices
    "Vol 10":      {"deriv": "R_10",  "type": "SYNTHETIC", "spike": None},
    "Vol 25":      {"deriv": "R_25",  "type": "SYNTHETIC", "spike": None},
    "Vol 50":      {"deriv": "R_50",  "type": "SYNTHETIC", "spike": None},
    "Vol 75":      {"deriv": "R_75",  "type": "SYNTHETIC", "spike": None},
    "Vol 100":     {"deriv": "R_100", "type": "SYNTHETIC", "spike": None},
}

BINANCE_INSTRUMENTS = {
    "BTC/USD":  {"binance": "BTCUSDT",  "type": "CRYPTO", "decimals": 0},
    "ETH/USD":  {"binance": "ETHUSDT",  "type": "CRYPTO", "decimals": 2},
    "XRP/USD":  {"binance": "XRPUSDT",  "type": "CRYPTO", "decimals": 4},
    "BNB/USD":  {"binance": "BNBUSDT",  "type": "CRYPTO", "decimals": 2},
    "SOL/USD":  {"binance": "SOLUSDT",  "type": "CRYPTO", "decimals": 2},
    "ADA/USD":  {"binance": "ADAUSDT",  "type": "CRYPTO", "decimals": 4},
}

# ── IN-MEMORY PRICE STORES ────────────────────────────────────
DERIV_STORE   = {}   # Updated by WebSocket thread
BINANCE_STORE = {}   # Updated by polling thread

# ══════════════════════════════════════════════════════════════
#  SECTION 1: INDICATORS
# ══════════════════════════════════════════════════════════════

def calc_rsi(close: pd.Series, period: int = 14) -> float:
    try:
        v = ta.momentum.rsi(close, window=period).iloc[-1]
        return round(float(v), 1)
    except:
        return 50.0

def calc_macd(close: pd.Series) -> dict:
    try:
        return {
            "macd":       round(float(ta.trend.macd(close).iloc[-1]), 6),
            "macdSignal": round(float(ta.trend.macd_signal(close).iloc[-1]), 6),
        }
    except:
        return {"macd": 0, "macdSignal": 0}

def calc_ema(close: pd.Series, period: int) -> float:
    try:
        return round(float(ta.trend.ema_indicator(close, window=period).iloc[-1]), 6)
    except:
        return round(float(close.iloc[-1]), 6)

def calc_atr(high, low, close, period: int = 14) -> float:
    try:
        v = ta.volatility.average_true_range(high, low, close, window=period).iloc[-1]
        return round(float(v), 6)
    except:
        return 0.0

def calc_bollinger(close: pd.Series) -> dict:
    try:
        return {
            "bbUpper":  round(float(ta.volatility.bollinger_hband(close).iloc[-1]), 6),
            "bbMiddle": round(float(ta.volatility.bollinger_mavg(close).iloc[-1]), 6),
            "bbLower":  round(float(ta.volatility.bollinger_lband(close).iloc[-1]), 6),
        }
    except:
        p = float(close.iloc[-1])
        return {"bbUpper": p, "bbMiddle": p, "bbLower": p}

def detect_spike(history: list, price: float) -> bool:
    if len(history) < 5:
        return False
    recent = history[-5:]
    avg_move = sum(abs(recent[i] - recent[i-1]) for i in range(1, len(recent))) / 4
    last_move = abs(price - history[-2]) if len(history) > 1 else 0
    return last_move > avg_move * 4 and last_move > 1

def build_signal(rsi: float, macd: float, macd_sig: float,
                 ema9: float, ema21: float, price: float,
                 bb_lower: float, bb_upper: float,
                 spiked: bool = False, spike_dir: str = None) -> dict:
    """Unified signal engine for all instrument types."""

    # Special logic for Boom/Crash
    if spike_dir == "UP":
        if spiked:
            return {"signal": "SELL", "strength": "STRONG",
                    "note": "Spike detected — fade it"}
        if rsi <= 35:
            return {"signal": "BUY", "strength": "STRONG",
                    "note": "Oversold — spike incoming"}
        return {"signal": "HOLD", "strength": "NEUTRAL", "note": "Waiting for setup"}

    if spike_dir == "DOWN":
        if spiked:
            return {"signal": "BUY", "strength": "STRONG",
                    "note": "Crash detected — bounce play"}
        if rsi >= 65:
            return {"signal": "SELL", "strength": "STRONG",
                    "note": "Overbought — crash incoming"}
        return {"signal": "HOLD", "strength": "NEUTRAL", "note": "Waiting for setup"}

    # Standard signal logic
    buy_reasons, sell_reasons = [], []

    if rsi <= 30:
        buy_reasons.append(f"RSI Oversold ({rsi})")
    elif rsi >= 70:
        sell_reasons.append(f"RSI Overbought ({rsi})")

    if macd > macd_sig:
        buy_reasons.append("MACD Bullish")
    else:
        sell_reasons.append("MACD Bearish")

    if ema9 > ema21:
        buy_reasons.append("EMA9 > EMA21")
    else:
        sell_reasons.append("EMA9 < EMA21")

    if price <= bb_lower * 1.002:
        buy_reasons.append("At Bollinger Lower")
    elif price >= bb_upper * 0.998:
        sell_reasons.append("At Bollinger Upper")

    if len(buy_reasons) >= 3:
        return {"signal": "BUY",  "strength": "STRONG",
                "note": " + ".join(buy_reasons[:2])}
    if len(sell_reasons) >= 3:
        return {"signal": "SELL", "strength": "STRONG",
                "note": " + ".join(sell_reasons[:2])}
    if len(buy_reasons) >= 2:
        return {"signal": "BUY",  "strength": "MODERATE",
                "note": " + ".join(buy_reasons[:2])}
    if len(sell_reasons) >= 2:
        return {"signal": "SELL", "strength": "MODERATE",
                "note": " + ".join(sell_reasons[:2])}
    if buy_reasons:
        return {"signal": "BUY",  "strength": "WEAK",   "note": buy_reasons[0]}
    if sell_reasons:
        return {"signal": "SELL", "strength": "WEAK",   "note": sell_reasons[0]}
    return {"signal": "HOLD", "strength": "NEUTRAL", "note": "No clear signal"}

# ══════════════════════════════════════════════════════════════
#  SECTION 2: YAHOO FINANCE
# ══════════════════════════════════════════════════════════════

def fetch_yahoo(name: str, cfg: dict) -> dict | None:
    """Fetch OHLCV data and compute all indicators via Yahoo Finance."""
    try:
        df = yf.download(
            cfg["yahoo"], period="5d",
            interval="1h", progress=False, auto_adjust=True
        )
        if df.empty or len(df) < 20:
            return None

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.dropna(inplace=True)

        close  = df["Close"].squeeze()
        high   = df["High"].squeeze()
        low    = df["Low"].squeeze()

        price    = round(float(close.iloc[-1]), cfg["decimals"])
        history  = [round(float(v), cfg["decimals"]) for v in close.tail(40).tolist()]
        candles  = [{"o": round(float(df["Open"].iloc[i]), cfg["decimals"]),
                     "h": round(float(high.iloc[i]), cfg["decimals"]),
                     "l": round(float(low.iloc[i]),  cfg["decimals"]),
                     "c": round(float(close.iloc[i]), cfg["decimals"])}
                    for i in range(max(0, len(df)-80), len(df))]

        open_p   = history[0]
        change   = round(price - open_p, cfg["decimals"])
        chg_pct  = round((change / open_p) * 100, 3)

        rsi    = calc_rsi(close)
        macd_d = calc_macd(close)
        ema9   = calc_ema(close, 9)
        ema21  = calc_ema(close, 21)
        ema50  = calc_ema(close, 50)
        atr    = calc_atr(high, low, close)
        boll   = calc_bollinger(close)

        sig = build_signal(
            rsi, macd_d["macd"], macd_d["macdSignal"],
            ema9, ema21, price,
            boll["bbLower"], boll["bbUpper"]
        )

        # Auto stop loss and take profit using ATR
        sl = round(price - 2*atr, cfg["decimals"]) if sig["signal"]=="BUY"  else round(price + 2*atr, cfg["decimals"])
        tp = round(price + 3*atr, cfg["decimals"]) if sig["signal"]=="BUY"  else round(price - 3*atr, cfg["decimals"])

        return {
            "symbol":     name,
            "price":      price,
            "change":     change,
            "changePct":  chg_pct,
            "rsi":        rsi,
            "ema9":       ema9,
            "ema21":      ema21,
            "ema50":      ema50,
            "atr":        atr,
            **macd_d,
            **boll,
            "signal":     sig["signal"],
            "strength":   sig["strength"],
            "note":       sig["note"],
            "stopLoss":   sl,
            "takeProfit": tp,
            "history":    history,
            "candles":    candles,
            "type":       cfg["type"],
            "spiked":     False,
            "source":     "Yahoo Finance",
            "updated":    datetime.now().isoformat(),
        }

    except Exception as e:
        log.error(f"Yahoo error [{name}]: {e}")
        return None

# ══════════════════════════════════════════════════════════════
#  SECTION 3: DERIV WEBSOCKET
# ══════════════════════════════════════════════════════════════

async def stream_deriv():
    """
    Persistent WebSocket connection to Deriv.
    Streams real-time tick data for Boom, Crash, and Volatility indices.
    Auto-reconnects on disconnect.
    """
    url = "wss://ws.binaryws.com/websockets/v3?app_id=1089"
    log.info("Connecting to Deriv WebSocket...")

    while True:
        try:
            async with websockets.connect(url, ping_interval=20) as ws:
                log.info("✓ Deriv WebSocket connected")

                # Subscribe to all Deriv symbols
                for name, cfg in DERIV_INSTRUMENTS.items():
                    await ws.send(json.dumps({"ticks": cfg["deriv"], "subscribe": 1}))

                async for raw in ws:
                    data = json.loads(raw)
                    if "error" in data:
                        continue
                    if "tick" not in data:
                        continue

                    tick   = data["tick"]
                    sym_id = tick["symbol"]
                    price  = float(tick["quote"])

                    # Find display name
                    dname = next(
                        (k for k, v in DERIV_INSTRUMENTS.items() if v["deriv"] == sym_id),
                        sym_id
                    )
                    cfg = DERIV_INSTRUMENTS.get(dname, {})

                    # Update rolling history and candles
                    prev    = DERIV_STORE.get(dname, {})
                    history = prev.get("history", [price] * 40)
                    history = history[-39:] + [price]

                    prev_candles = prev.get("candles", [])
                    if prev_candles:
                        lc = prev_candles[-1]
                        nc = {"o": lc["c"], "h": max(lc["c"], price),
                              "l": min(lc["c"], price), "c": price}
                        candles = prev_candles[-79:] + [nc]
                    else:
                        candles = [{"o":price,"h":price,"l":price,"c":price}] * 40

                    open_p   = history[0]
                    change   = round(price - open_p, 2)
                    chg_pct  = round((change / open_p) * 100, 3) if open_p else 0
                    spiked   = detect_spike(history, price)

                    # RSI from history
                    if len(history) >= 15:
                        s  = pd.Series(history)
                        rsi = calc_rsi(s)
                    else:
                        rsi = 50.0

                    spike_dir = cfg.get("spike")
                    sig = build_signal(rsi, 0, 0, 0, 0, price, 0, 999,
                                       spiked, spike_dir)

                    DERIV_STORE[dname] = {
                        "symbol":     dname,
                        "price":      round(price, 2),
                        "change":     change,
                        "changePct":  chg_pct,
                        "rsi":        rsi,
                        "signal":     sig["signal"],
                        "strength":   sig["strength"],
                        "note":       sig["note"],
                        "history":    history,
                        "candles":    candles,
                        "type":       "SYNTHETIC",
                        "spiked":     spiked,
                        "source":     "Deriv WebSocket",
                        "updated":    datetime.now().isoformat(),
                    }

        except websockets.exceptions.ConnectionClosed:
            log.warning("Deriv WS closed — reconnecting in 5s")
            await asyncio.sleep(5)
        except Exception as e:
            log.error(f"Deriv WS error: {e} — reconnecting in 10s")
            await asyncio.sleep(10)

def run_deriv_stream():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(stream_deriv())

# ══════════════════════════════════════════════════════════════
#  SECTION 4: BINANCE API
# ══════════════════════════════════════════════════════════════

def fetch_binance_public(symbol: str) -> dict | None:
    """
    Fetch crypto data from Binance public API.
    No API key needed for market data — only for trading.
    """
    try:
        base = "https://api.binance.com"

        # 24hr ticker
        ticker_url = f"{base}/api/v3/ticker/24hr?symbol={symbol}"
        ticker_r   = requests.get(ticker_url, timeout=5)
        if ticker_r.status_code != 200:
            # Try Binance.US for Zimbabwe
            base = "https://api.binance.us"
            ticker_r = requests.get(
                f"{base}/api/v3/ticker/24hr?symbol={symbol}", timeout=5
            )
        ticker = ticker_r.json()

        price    = float(ticker["lastPrice"])
        open_p   = float(ticker["openPrice"])
        change   = round(price - open_p, 4)
        chg_pct  = round(float(ticker["priceChangePercent"]), 3)
        high_24  = float(ticker["highPrice"])
        low_24   = float(ticker["lowPrice"])
        volume   = float(ticker["volume"])

        # Hourly candles for chart (last 80)
        klines_url = f"{base}/api/v3/klines?symbol={symbol}&interval=1h&limit=80"
        klines_r   = requests.get(klines_url, timeout=8)
        klines     = klines_r.json()

        candles = []
        history = []
        for k in klines:
            o = float(k[1]), 
            h = float(k[2])
            l = float(k[3])
            c = float(k[4])
            candles.append({"o": float(k[1]), "h": h, "l": l, "c": c})
            history.append(c)

        # Compute indicators from candle history
        if len(history) >= 20:
            closes = pd.Series(history)
            highs  = pd.Series([c["h"] for c in candles])
            lows   = pd.Series([c["l"] for c in candles])
            rsi    = calc_rsi(closes)
            macd_d = calc_macd(closes)
            ema9   = calc_ema(closes, 9)
            ema21  = calc_ema(closes, 21)
            atr    = calc_atr(highs, lows, closes)
            boll   = calc_bollinger(closes)
            sig    = build_signal(rsi, macd_d["macd"], macd_d["macdSignal"],
                                  ema9, ema21, price,
                                  boll["bbLower"], boll["bbUpper"])
            sl = round(price - 2*atr, 2) if sig["signal"]=="BUY" else round(price + 2*atr, 2)
            tp = round(price + 3*atr, 2) if sig["signal"]=="BUY" else round(price - 3*atr, 2)
        else:
            rsi    = 50.0
            macd_d = {"macd": 0, "macdSignal": 0}
            ema9 = ema21 = atr = price
            boll   = {"bbUpper": price, "bbMiddle": price, "bbLower": price}
            sig    = {"signal": "HOLD", "strength": "NEUTRAL", "note": "Loading"}
            sl = tp = price

        display_name = symbol.replace("USDT", "/USD")

        return {
            "symbol":     display_name,
            "price":      round(price, 2),
            "change":     change,
            "changePct":  chg_pct,
            "high24h":    high_24,
            "low24h":     low_24,
            "volume24h":  volume,
            "rsi":        rsi,
            "ema9":       ema9,
            "ema21":      ema21,
            "atr":        atr,
            **macd_d,
            **boll,
            "signal":     sig["signal"],
            "strength":   sig["strength"],
            "note":       sig["note"],
            "stopLoss":   sl,
            "takeProfit": tp,
            "history":    history[-40:],
            "candles":    candles,
            "type":       "CRYPTO",
            "spiked":     False,
            "source":     "Binance",
            "updated":    datetime.now().isoformat(),
        }

    except Exception as e:
        log.error(f"Binance error [{symbol}]: {e}")
        return None

def fetch_kucoin_fallback(symbol: str, display: str) -> dict | None:
    """
    KuCoin fallback for Zimbabwe where Binance may be restricted.
    Uses KuCoin public API — no account needed for market data.
    symbol format: BTC-USDT
    """
    try:
        base = "https://api.kucoin.com"

        # Ticker
        ticker_r = requests.get(
            f"{base}/api/v1/market/stats?symbol={symbol}", timeout=5
        )
        ticker   = ticker_r.json()["data"]
        price    = float(ticker["last"])
        chg_pct  = round(float(ticker["changeRate"]) * 100, 3)

        # Candles (1 hour, last 80)
        klines_r = requests.get(
            f"{base}/api/v1/market/candles?symbol={symbol}&type=1hour", timeout=8
        )
        klines   = klines_r.json().get("data", [])[:80]

        candles = [{"o": float(k[1]), "h": float(k[3]),
                    "l": float(k[4]), "c": float(k[2])} for k in klines]
        history = [c["c"] for c in candles]

        rsi = calc_rsi(pd.Series(history)) if len(history) >= 15 else 50.0

        return {
            "symbol":    display,
            "price":     round(price, 2),
            "changePct": chg_pct,
            "rsi":       rsi,
            "signal":    "HOLD",
            "strength":  "NEUTRAL",
            "note":      "KuCoin data",
            "history":   history[-40:],
            "candles":   candles,
            "type":      "CRYPTO",
            "spiked":    False,
            "source":    "KuCoin",
            "updated":   datetime.now().isoformat(),
        }
    except Exception as e:
        log.error(f"KuCoin error [{symbol}]: {e}")
        return None

KUCOIN_MAP = {
    "BTC/USD": "BTC-USDT",
    "ETH/USD": "ETH-USDT",
    "XRP/USD": "XRP-USDT",
    "BNB/USD": "BNB-USDT",
    "SOL/USD": "SOL-USDT",
    "ADA/USD": "ADA-USDT",
}

def poll_binance():
    """Poll Binance every 30 seconds for all crypto prices."""
    import time
    while True:
        for name, cfg in BINANCE_INSTRUMENTS.items():
            data = fetch_binance_public(cfg["binance"])
            if data:
                BINANCE_STORE[name] = data
                log.info(f"✓ Binance [{name}]: {data['price']}")
            else:
                # Try KuCoin fallback
                kucoin_sym = KUCOIN_MAP.get(name)
                if kucoin_sym:
                    data = fetch_kucoin_fallback(kucoin_sym, name)
                    if data:
                        BINANCE_STORE[name] = data
                        log.info(f"✓ KuCoin fallback [{name}]: {data['price']}")
        time.sleep(30)

# ══════════════════════════════════════════════════════════════
#  SECTION 5: STARTUP
# ══════════════════════════════════════════════════════════════

@app.on_event("startup")
async def startup():
    log.info("🐒 SMONKIE TRADER API v4.0 starting...")

    # Start Deriv WebSocket in background thread
    deriv_thread = threading.Thread(target=run_deriv_stream, daemon=True)
    deriv_thread.start()
    log.info("✓ Deriv WebSocket thread started")

    # Start Binance polling in background thread
    binance_thread = threading.Thread(target=poll_binance, daemon=True)
    binance_thread.start()
    log.info("✓ Binance polling thread started")

    log.info(f"✓ Yahoo Finance: {len(YAHOO_INSTRUMENTS)} instruments")
    log.info(f"✓ Deriv: {len(DERIV_INSTRUMENTS)} instruments")
    log.info(f"✓ Binance/KuCoin: {len(BINANCE_INSTRUMENTS)} instruments")
    log.info(f"✓ Total: {len(YAHOO_INSTRUMENTS)+len(DERIV_INSTRUMENTS)+len(BINANCE_INSTRUMENTS)} instruments")

# ══════════════════════════════════════════════════════════════
#  SECTION 6: API ENDPOINTS
# ══════════════════════════════════════════════════════════════

@app.get("/")
def root():
    return {
        "name":     "SMONKIE TRADER API",
        "version":  "4.0.0",
        "sources":  ["Yahoo Finance", "Deriv WebSocket", "Binance", "KuCoin"],
        "endpoints": ["/prices", "/prices/forex", "/prices/crypto",
                       "/prices/synthetic", "/health", "/docs"],
    }

@app.get("/health")
def health():
    return {
        "status":           "ok",
        "yahoo_instruments": len(YAHOO_INSTRUMENTS),
        "deriv_live":        len(DERIV_STORE),
        "binance_live":      len(BINANCE_STORE),
        "total_instruments": (len(YAHOO_INSTRUMENTS) +
                              len(DERIV_INSTRUMENTS) +
                              len(BINANCE_INSTRUMENTS)),
        "timestamp": datetime.now().isoformat(),
    }

@app.get("/prices")
def get_all_prices():
    """
    Returns ALL instruments from all three data sources.
    Yahoo Finance + Deriv WebSocket + Binance/KuCoin
    """
    result = {}

    # 1. Yahoo Finance — Forex, Gold, ETFs
    log.info("Fetching Yahoo Finance instruments...")
    for name, cfg in YAHOO_INSTRUMENTS.items():
        data = fetch_yahoo(name, cfg)
        if data:
            result[name] = data

    # 2. Deriv WebSocket — Boom, Crash, Volatility
    result.update(DERIV_STORE)

    # 3. Binance/KuCoin — Crypto
    result.update(BINANCE_STORE)

    # Fill missing Deriv instruments with placeholder
    for name in DERIV_INSTRUMENTS:
        if name not in result:
            result[name] = {
                "symbol":    name,
                "price":     0,
                "changePct": 0,
                "rsi":       50,
                "signal":    "HOLD",
                "strength":  "NEUTRAL",
                "note":      "Connecting to Deriv...",
                "history":   [],
                "candles":   [],
                "type":      "SYNTHETIC",
                "spiked":    False,
                "source":    "Connecting...",
            }

    log.info(f"Returning {len(result)} instruments")
    return result

@app.get("/prices/forex")
def get_forex():
    """Returns only Forex and Commodity instruments."""
    result = {}
    for name, cfg in YAHOO_INSTRUMENTS.items():
        data = fetch_yahoo(name, cfg)
        if data:
            result[name] = data
    return result

@app.get("/prices/crypto")
def get_crypto():
    """Returns live crypto prices from Binance or KuCoin."""
    if BINANCE_STORE:
        return BINANCE_STORE
    # Fetch fresh if store is empty
    result = {}
    for name, cfg in BINANCE_INSTRUMENTS.items():
        data = fetch_binance_public(cfg["binance"])
        if data:
            result[name] = data
        else:
            kucoin_sym = KUCOIN_MAP.get(name)
            if kucoin_sym:
                data = fetch_kucoin_fallback(kucoin_sym, name)
                if data:
                    result[name] = data
    return result

@app.get("/prices/synthetic")
def get_synthetic():
    """Returns live Boom, Crash, and Volatility indices from Deriv."""
    if DERIV_STORE:
        return DERIV_STORE
    return {name: {"symbol": name, "source": "Connecting to Deriv..."}
            for name in DERIV_INSTRUMENTS}

@app.get("/price/{symbol}")
def get_one(symbol: str):
    """Get single instrument. Use + for spaces: Boom+1000"""
    sym = symbol.replace("+", " ")
    all_prices = get_all_prices()
    if sym in all_prices:
        return all_prices[sym]
    raise HTTPException(status_code=404, detail=f"Symbol '{sym}' not found")

@app.get("/signals")
def get_signals():
    """Returns only instruments with BUY or SELL signals."""
    all_prices = get_all_prices()
    return {
        k: v for k, v in all_prices.items()
        if v.get("signal") in ["BUY", "SELL"]
    }

# ══════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 58)
    print("  🐒 SMONKIE TRADER BACKEND v4.0")
    print("=" * 58)
    print(f"  Yahoo Finance : {len(YAHOO_INSTRUMENTS)} instruments")
    print(f"  Deriv WS      : {len(DERIV_INSTRUMENTS)} instruments")
    print(f"  Binance/KuCoin: {len(BINANCE_INSTRUMENTS)} instruments")
    print(f"  Total         : {len(YAHOO_INSTRUMENTS)+len(DERIV_INSTRUMENTS)+len(BINANCE_INSTRUMENTS)} instruments")
    print("=" * 58)
    print("  Local  : http://localhost:8000")
    print("  Docs   : http://localhost:8000/docs")
    print("  Prices : http://localhost:8000/prices")
    print("  Crypto : http://localhost:8000/prices/crypto")
    print("  Deriv  : http://localhost:8000/prices/synthetic")
    print("  Signals: http://localhost:8000/signals")
    print("=" * 58)
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
