"""
╔══════════════════════════════════════════════════════════════╗
║     SMONKIE TRADER — ML SIGNAL ENGINE                       ║
║     Integrates lerabyte/trading-model features              ║
║     + Histogram Gradient Boosting classifier                ║
╠══════════════════════════════════════════════════════════════╣
║  HOW IT WORKS:                                              ║
║  1. Downloads real price data for any instrument            ║
║  2. Computes 15 engineered features (from the GitHub repo)  ║
║  3. Trains a HGB model on historical data                   ║
║  4. Predicts BUY/SELL/HOLD for the current candle           ║
║  5. Returns ML signal + confidence + feature breakdown      ║
║  6. SMONKIE combines this with RSI/MACD for confluence      ║
║                                                             ║
║  INSTALL:                                                   ║
║  pip install scikit-learn yfinance pandas numpy ta          ║
║                                                             ║
║  USE:                                                       ║
║  python ml_signal_engine.py                                 ║
╚══════════════════════════════════════════════════════════════╝
"""

import numpy as np
import pandas as pd
import yfinance as yf
import warnings
import logging
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, classification_report
import ta

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

# ── INSTRUMENTS SUPPORTED ─────────────────────────────────────
# Maps SMONKIE display names to Yahoo Finance symbols
YAHOO_MAP = {
    "EUR/USD":  "EURUSD=X",
    "GBP/USD":  "GBPUSD=X",
    "USD/ZAR":  "USDZAR=X",
    "USD/JPY":  "USDJPY=X",
    "AUD/USD":  "AUDUSD=X",
    "XAU/USD":  "XAUUSD=X",
    "SPY":      "SPY",
    "LIT":      "LIT",
    "BTC/USD":  "BTC-USD",
    "ETH/USD":  "ETH-USD",
}

# ── WILDER SMOOTHING (from GitHub repo) ──────────────────────
# More accurate than simple EMA for RSI calculation
# Uses Wilder's original smoothing method

def wilder_avg(series: pd.Series, n: int) -> pd.Series:
    """
    Wilder's smoothing average.
    More accurate than standard EMA for RSI calculation.
    Used in the original trading-model repo.
    """
    out = pd.Series(np.nan, index=series.index, dtype=float)
    if len(series) < n:
        return out
    # Seed with simple average of first n values
    out.iloc[n - 1] = series.iloc[:n].mean()
    alpha = 1.0 / n
    for i in range(n, len(series)):
        out.iloc[i] = out.iloc[i - 1] + alpha * (series.iloc[i] - out.iloc[i - 1])
    return out


def compute_rsi_wilder(close: pd.Series, n: int = 14) -> pd.Series:
    """
    RSI using Wilder's original smoothing.
    More stable than basic RSI — fewer false signals.
    """
    delta = close.diff()
    gain  = wilder_avg(delta.clip(lower=0).fillna(0), n)
    loss  = wilder_avg((-delta.clip(upper=0)).fillna(0), n)
    rs    = gain / loss.replace(0, np.nan)
    rsi   = 100 - (100 / (1 + rs))
    # Edge cases
    rsi[(gain == 0) & (loss == 0)] = 50.0
    rsi[(gain > 0)  & (loss == 0)] = 100.0
    return rsi


def compute_atr_wilder(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """ATR using Wilder's smoothing."""
    prev = df["close"].shift(1)
    tr   = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev).abs(),
        (df["low"]  - prev).abs(),
    ], axis=1).max(axis=1)
    return wilder_avg(tr, n)


# ── FEATURE ENGINEERING (adapted from GitHub repo) ───────────
# This is the core contribution from lerabyte/trading-model.
# We extend it to work on any instrument, not just SPY.

def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Computes all 15+ features from the trading-model repo
    plus additional features for Smonkie's use case.

    INPUT:  DataFrame with columns [date, open, high, low, close, volume]
    OUTPUT: DataFrame with all original columns + feature columns
    """
    df = df.copy()
    close  = df["close"]
    high   = df["high"]
    low    = df["low"]

    # ── FEATURE GROUP 1: Returns (from repo) ──────────────────
    # How much has price moved over different lookback periods?
    # Positive = upward momentum, Negative = downward momentum
    for n in [1, 2, 3, 5, 10, 20]:
        df[f"ret{n}"] = close.pct_change(n)

    # ── FEATURE GROUP 2: Moving Average Gaps (from repo) ──────
    # How far is price from its own average?
    # Positive = price above average (bullish)
    # Negative = price below average (bearish)
    for n in [10, 20, 50, 200]:
        ma = close.rolling(n).mean()
        df[f"sma{n}_gap"] = (close / ma) - 1

    # ── FEATURE GROUP 3: MA Relationships (from repo) ─────────
    # Are shorter MAs above longer MAs?
    # Positive = uptrend, Negative = downtrend
    df["sma10_sma20_gap"]  = (close.rolling(10).mean() / close.rolling(20).mean()) - 1
    df["sma20_sma50_gap"]  = (close.rolling(20).mean() / close.rolling(50).mean()) - 1
    df["sma50_sma200_gap"] = (close.rolling(50).mean() / close.rolling(200).mean()) - 1

    # ── FEATURE GROUP 4: Volatility (from repo) ───────────────
    # How much is price fluctuating?
    # High volatility = bigger moves incoming
    for n in [5, 10, 20]:
        df[f"vol{n}"] = df["ret1"].rolling(n).std()

    df["vol_ratio"] = df["vol5"] / df["vol20"]  # Short vs long vol

    # ── FEATURE GROUP 5: ATR (from repo) ──────────────────────
    # ATR as a fraction of price — normalised measure of range
    df["atr14_ratio"] = compute_atr_wilder(df, 14) / close

    # ── FEATURE GROUP 6: RSI with Wilder (from repo) ──────────
    df["rsi14_wilder"] = compute_rsi_wilder(close, 14)

    # ── FEATURE GROUP 7: Volume Z-Score (from repo) ───────────
    # How unusual is today's volume vs last 20 days?
    # High z-score = unusual activity, often precedes big moves
    if "volume" in df.columns and df["volume"].sum() > 0:
        df["volume_z20"] = (
            (df["volume"] - df["volume"].rolling(20).mean()) /
            df["volume"].rolling(20).std()
        )
    else:
        df["volume_z20"] = 0.0

    # ── FEATURE GROUP 8: Regime Filter (from repo) ────────────
    # Is price in an uptrend or downtrend?
    # 1 = above 200 SMA = long trend is up = prefer BUY signals
    # 0 = below 200 SMA = long trend is down = prefer SELL signals
    df["regime_long_ok"] = (close > close.rolling(200).mean()).astype(int)

    # ── FEATURE GROUP 9: HL Position (from repo) ──────────────
    # Where is price within its recent 20-day range?
    # 0.0 = at the low (oversold territory)
    # 1.0 = at the high (overbought territory)
    df["hl_position_20"] = (
        (close - low.rolling(20).min()) /
        (high.rolling(20).max() - low.rolling(20).min() + 1e-9)
    )

    # ── FEATURE GROUP 10: Additional SMONKIE features ─────────
    # These go beyond the GitHub repo

    # MACD signal (trend change detector)
    ema12 = close.ewm(span=12).mean()
    ema26 = close.ewm(span=26).mean()
    macd  = ema12 - ema26
    macd_signal = macd.ewm(span=9).mean()
    df["macd_gap"]     = macd - macd_signal  # Positive = bullish
    df["macd_hist_dir"] = np.sign(macd - macd_signal)  # +1 or -1

    # Bollinger Band position
    bb_mid = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    df["bb_position"] = (close - (bb_mid - 2*bb_std)) / (4*bb_std + 1e-9)

    # RSI momentum (is RSI rising or falling?)
    df["rsi_momentum"] = df["rsi14_wilder"].diff(3)

    # Volume momentum
    df["vol_momentum"] = df["volume_z20"].diff(3) if "volume_z20" in df else 0

    return df


# ── TARGET VARIABLE ───────────────────────────────────────────
def create_target(df: pd.DataFrame, lookahead: int = 3,
                  threshold: float = 0.002) -> pd.DataFrame:
    """
    Creates the target variable for the ML model.

    WHAT IT ASKS: "Will price be significantly higher or lower
    in `lookahead` candles from now?"

    Classes:
    -1 = SELL (price will fall more than threshold%)
     0 = HOLD (price will stay flat)
    +1 = BUY  (price will rise more than threshold%)

    WHY THRESHOLD?
    Without a threshold we'd try to predict tiny moves that
    are basically noise. We only predict meaningful moves.
    """
    df = df.copy()
    future_return = df["close"].shift(-lookahead) / df["close"] - 1
    df["target"] = 0
    df.loc[future_return >  threshold, "target"] =  1
    df.loc[future_return < -threshold, "target"] = -1
    return df.dropna()


# ── ML MODEL ──────────────────────────────────────────────────
class SmonkieMLModel:
    """
    Machine Learning signal generator for SMONKIE Trader.

    Uses Histogram Gradient Boosting (same algorithm as the
    GitHub repo) trained on the engineered features.

    WHY HISTOGRAM GRADIENT BOOSTING?
    - Handles missing values natively (no need for imputation)
    - Very fast to train — can retrain daily
    - Works well on tabular financial data
    - Less prone to overfitting than regular gradient boosting
    - Used by many professional quant funds
    """

    FEATURE_COLS = [
        "ret1", "ret2", "ret3", "ret5", "ret10", "ret20",
        "sma10_gap", "sma20_gap", "sma50_gap", "sma200_gap",
        "sma10_sma20_gap", "sma20_sma50_gap", "sma50_sma200_gap",
        "vol5", "vol10", "vol20", "vol_ratio",
        "atr14_ratio", "rsi14_wilder", "volume_z20",
        "regime_long_ok", "hl_position_20",
        "macd_gap", "macd_hist_dir", "bb_position",
        "rsi_momentum",
    ]

    def __init__(self):
        self.model   = HistGradientBoostingClassifier(
            max_iter=200,
            max_depth=5,
            learning_rate=0.05,
            min_samples_leaf=20,
            random_state=42,
        )
        self.scaler   = StandardScaler()
        self.trained  = False
        self.accuracy = None
        self.symbol   = None

    def train(self, symbol: str, yahoo_symbol: str,
              period: str = "2y", interval: str = "1h") -> dict:
        """
        Downloads data, engineers features, trains the model.
        Returns training report.
        """
        log.info(f"Training ML model for {symbol}...")

        # Download data
        df = yf.download(yahoo_symbol, period=period,
                         interval=interval, progress=False,
                         auto_adjust=True)
        if df.empty or len(df) < 300:
            return {"error": f"Insufficient data for {symbol}"}

        # Flatten columns
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.columns = [c.lower() for c in df.columns]
        df = df.reset_index()

        # Engineer features
        df = engineer_features(df)
        df = create_target(df, lookahead=3, threshold=0.001)
        df = df.dropna()

        if len(df) < 200:
            return {"error": "Not enough data after feature engineering"}

        # Prepare features and target
        available = [c for c in self.FEATURE_COLS if c in df.columns]
        X = df[available].values
        y = df["target"].values

        # Train / test split — keep time order
        split = int(len(X) * 0.8)
        X_train, X_test = X[:split], X[split:]
        y_train, y_test = y[:split], y[split:]

        # Scale features
        X_train = self.scaler.fit_transform(X_train)
        X_test  = self.scaler.transform(X_test)

        # Train model
        self.model.fit(X_train, y_train)
        self.trained  = True
        self.symbol   = symbol
        self.feature_names = available

        # Evaluate
        y_pred = self.model.predict(X_test)
        self.accuracy = accuracy_score(y_test, y_pred)

        # Class distribution
        unique, counts = np.unique(y, return_counts=True)
        dist = dict(zip(unique.tolist(), counts.tolist()))

        log.info(f"Model trained for {symbol} — Accuracy: {self.accuracy:.1%}")

        return {
            "symbol":       symbol,
            "accuracy":     round(self.accuracy * 100, 1),
            "samples":      len(df),
            "train_size":   len(X_train),
            "test_size":    len(X_test),
            "class_dist":   dist,
            "features_used": len(available),
        }

    def predict(self, symbol: str, yahoo_symbol: str,
                interval: str = "1h") -> dict:
        """
        Predicts signal for the CURRENT candle.
        Returns structured signal for SMONKIE to display.
        """
        if not self.trained:
            return {"error": "Model not trained. Call train() first."}

        # Get latest data
        df = yf.download(yahoo_symbol, period="3mo",
                         interval=interval, progress=False,
                         auto_adjust=True)
        if df.empty:
            return {"error": "Could not fetch data"}

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.columns = [c.lower() for c in df.columns]
        df = df.reset_index()
        df = engineer_features(df)
        df = df.dropna()

        if len(df) < 1:
            return {"error": "Not enough data for prediction"}

        # Use latest row
        latest = df.iloc[-1]
        available = [c for c in self.feature_names if c in df.columns]
        X = latest[available].values.reshape(1, -1)
        X_scaled = self.scaler.transform(X)

        # Predict
        prediction   = self.model.predict(X_scaled)[0]
        probabilities = self.model.predict_proba(X_scaled)[0]
        classes      = self.model.classes_

        # Map prediction to signal
        prob_map = dict(zip(classes.tolist(), probabilities.tolist()))
        buy_prob  = prob_map.get(1,  0)
        hold_prob = prob_map.get(0,  0)
        sell_prob = prob_map.get(-1, 0)

        # Determine signal type and strength
        confidence = max(buy_prob, hold_prob, sell_prob)

        if prediction == 1:
            if confidence >= 0.65:
                sig_type, strength = "BUY", "STRONG"
            elif confidence >= 0.55:
                sig_type, strength = "BUY", "MODERATE"
            else:
                sig_type, strength = "BUY", "WEAK"
        elif prediction == -1:
            if confidence >= 0.65:
                sig_type, strength = "SELL", "STRONG"
            elif confidence >= 0.55:
                sig_type, strength = "SELL", "MODERATE"
            else:
                sig_type, strength = "SELL", "WEAK"
        else:
            sig_type, strength = "HOLD", "NEUTRAL"

        # Key feature values for explanation
        price     = round(float(latest["close"]), 5)
        rsi       = round(float(latest.get("rsi14_wilder", 50)), 1)
        regime    = int(latest.get("regime_long_ok", 0))
        hl_pos    = round(float(latest.get("hl_position_20", 0.5)), 2)
        vol_ratio = round(float(latest.get("vol_ratio", 1)), 2)
        bb_pos    = round(float(latest.get("bb_position", 0.5)), 2)

        # Build explanation
        reasons = []
        if rsi < 35:
            reasons.append(f"RSI oversold ({rsi}) — Wilder method")
        if rsi > 65:
            reasons.append(f"RSI overbought ({rsi}) — Wilder method")
        if regime == 1:
            reasons.append("Price above 200 SMA — bullish regime")
        elif regime == 0:
            reasons.append("Price below 200 SMA — bearish regime")
        if hl_pos < 0.2:
            reasons.append(f"Price at 20-day low range ({hl_pos:.0%})")
        if hl_pos > 0.8:
            reasons.append(f"Price at 20-day high range ({hl_pos:.0%})")
        if vol_ratio > 1.5:
            reasons.append(f"Volatility expanding ({vol_ratio:.1f}x)")
        if not reasons:
            reasons.append("ML pattern detected in feature set")

        return {
            "symbol":      symbol,
            "price":       price,
            "ml_signal":   sig_type,
            "strength":    strength,
            "confidence":  round(confidence * 100, 1),
            "buy_prob":    round(buy_prob * 100, 1),
            "hold_prob":   round(hold_prob * 100, 1),
            "sell_prob":   round(sell_prob * 100, 1),
            "rsi_wilder":  rsi,
            "regime":      "BULLISH" if regime else "BEARISH",
            "hl_position": hl_pos,
            "vol_ratio":   vol_ratio,
            "bb_position": bb_pos,
            "reasons":     reasons,
            "model_accuracy": round(self.accuracy * 100, 1) if self.accuracy else None,
            "source":      "ML — HistGradientBoosting",
        }


# ── MULTI-INSTRUMENT MANAGER ──────────────────────────────────
class SmonkieMLManager:
    """
    Manages ML models for all SMONKIE instruments.
    Trains one model per instrument and serves predictions.
    """

    def __init__(self):
        self.models = {}

    def train_all(self, symbols: dict = None) -> dict:
        """Train models for all instruments."""
        symbols = symbols or YAHOO_MAP
        results = {}
        for display, yahoo in symbols.items():
            model = SmonkieMLModel()
            result = model.train(display, yahoo)
            if "error" not in result:
                self.models[display] = model
                results[display] = result
                log.info(f"✓ {display}: {result['accuracy']}% accuracy")
            else:
                log.warning(f"✗ {display}: {result['error']}")
        return results

    def predict_all(self) -> dict:
        """Get ML predictions for all trained instruments."""
        predictions = {}
        for symbol, model in self.models.items():
            yahoo = YAHOO_MAP.get(symbol)
            if yahoo:
                pred = model.predict(symbol, yahoo)
                if "error" not in pred:
                    predictions[symbol] = pred
        return predictions

    def get_signal(self, symbol: str) -> dict:
        """Get ML signal for one instrument."""
        if symbol not in self.models:
            return {"error": f"No model trained for {symbol}"}
        yahoo = YAHOO_MAP.get(symbol)
        if not yahoo:
            return {"error": f"No Yahoo symbol for {symbol}"}
        return self.models[symbol].predict(symbol, yahoo)


# ── FASTAPI INTEGRATION ───────────────────────────────────────
# Add these endpoints to your SMONKIE main.py backend

"""
To integrate into main.py, add:

from ml_signal_engine import SmonkieMLManager

ml_manager = SmonkieMLManager()

@app.on_event("startup")
async def startup():
    # Train all models on startup (runs in background)
    import threading
    def train_models():
        log.info("Training ML models...")
        results = ml_manager.train_all()
        log.info(f"ML training complete: {len(results)} models ready")
    t = threading.Thread(target=train_models, daemon=True)
    t.start()

@app.get("/ml/signals")
def get_ml_signals():
    return ml_manager.predict_all()

@app.get("/ml/signal/{symbol}")
def get_ml_signal(symbol: str):
    sym = symbol.replace("+", " ")
    return ml_manager.get_signal(sym)

@app.get("/ml/status")
def ml_status():
    return {
        "models_trained": list(ml_manager.models.keys()),
        "count": len(ml_manager.models)
    }
"""


# ── CONFLUENCE COMBINER ───────────────────────────────────────
def combine_signals(technical_signal: str, ml_signal: str,
                    technical_rsi: float, ml_confidence: float,
                    ml_regime: str) -> dict:
    """
    Combines traditional technical signals with ML prediction.

    CONFLUENCE LOGIC:
    - If both technical AND ML agree = STRONG signal
    - If only one agrees = MODERATE signal
    - If they conflict = HOLD (wait for confirmation)

    This is the key value add — two independent methods
    confirming the same direction.
    """
    agree = technical_signal == ml_signal

    if agree and ml_signal == "BUY" and ml_confidence >= 60:
        final = "STRONG BUY"
        score = 90
        note  = f"Technical + ML both BUY ({ml_confidence:.0f}% confidence)"
    elif agree and ml_signal == "SELL" and ml_confidence >= 60:
        final = "STRONG SELL"
        score = 90
        note  = f"Technical + ML both SELL ({ml_confidence:.0f}% confidence)"
    elif agree:
        final = ml_signal
        score = 70
        note  = f"Both methods agree — {ml_signal}"
    elif ml_signal == "HOLD" and technical_signal in ["BUY","SELL"]:
        final = technical_signal
        score = 55
        note  = f"Technical says {technical_signal}, ML neutral"
    elif technical_signal == "HOLD" and ml_signal in ["BUY","SELL"]:
        final = ml_signal
        score = 55
        note  = f"ML says {ml_signal} ({ml_confidence:.0f}%), technical neutral"
    else:
        final = "HOLD"
        score = 30
        note  = f"Conflict: Technical={technical_signal}, ML={ml_signal} — wait"

    # Regime filter from the GitHub repo
    if ml_regime == "BEARISH" and final == "BUY":
        score -= 15
        note += " ⚠ Against bearish regime"
    if ml_regime == "BULLISH" and final == "SELL":
        score -= 15
        note += " ⚠ Against bullish regime"

    return {
        "final_signal":  final,
        "confluence_score": score,
        "note":          note,
        "ml_agrees":     agree,
    }


# ── MAIN — TEST THE ENGINE ─────────────────────────────────────
if __name__ == "__main__":
    print("=" * 55)
    print("  SMONKIE ML SIGNAL ENGINE")
    print("  Testing on EUR/USD and SPY")
    print("=" * 55)

    # Test single instrument
    model = SmonkieMLModel()

    print("\n[1] Training on EUR/USD...")
    result = model.train("EUR/USD", "EURUSD=X", period="2y", interval="1h")
    print(f"    Accuracy:  {result.get('accuracy')}%")
    print(f"    Samples:   {result.get('samples')}")
    print(f"    Features:  {result.get('features_used')}")

    print("\n[2] Predicting current signal...")
    signal = model.predict("EUR/USD", "EURUSD=X")
    print(f"    Signal:    {signal.get('ml_signal')} ({signal.get('strength')})")
    print(f"    Confidence:{signal.get('confidence')}%")
    print(f"    BUY prob:  {signal.get('buy_prob')}%")
    print(f"    SELL prob: {signal.get('sell_prob')}%")
    print(f"    RSI:       {signal.get('rsi_wilder')}")
    print(f"    Regime:    {signal.get('regime')}")
    for reason in signal.get("reasons", []):
        print(f"    → {reason}")

    print("\n[3] Testing confluence combiner...")
    combined = combine_signals(
        technical_signal="BUY",
        ml_signal=signal.get("ml_signal","HOLD"),
        technical_rsi=signal.get("rsi_wilder",50),
        ml_confidence=signal.get("confidence",50),
        ml_regime=signal.get("regime","NEUTRAL"),
    )
    print(f"    Final:     {combined['final_signal']}")
    print(f"    Score:     {combined['confluence_score']}/100")
    print(f"    Note:      {combined['note']}")
    print("\n✓ ML Signal Engine ready for SMONKIE integration")
    print("=" * 55)
