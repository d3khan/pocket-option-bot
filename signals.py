# signals.py
import talib
import numpy as np
import pandas as pd

def generate_loose_signals(df: pd.DataFrame):
    """
    Ultra‑loose signal generator for 1m scalping.

    Rules:
      - EMA 3 > EMA 8 → CALL bias; EMA 3 < EMA 8 → PUT bias.
      - Stochastic %K > %D → CALL; %K < %D → PUT.
      - Bollinger Bands only as squeeze filter: width > 10‑period median (not squeezed).
      - No thresholds on Stochastic, no price/EMA filters.

    Returns a dict with:
      - signal: 'CALL', 'PUT', or 'NONE'
      - strength: a value between 0 and 1 indicating signal quality.
      - indicators: close, ema3, ema8, slowk, slowd, bb_width
    """
    if df.empty or len(df) < 30:
        return {
            "signal": "NONE",
            "strength": 0.0,
            "close": None,
            "ema3": None,
            "ema8": None,
            "slowk": None,
            "slowd": None,
            "bb_width": None,
        }

    df = df.copy().sort_index()

    df["ema3"] = talib.EMA(df["close"], timeperiod=3)
    df["ema8"] = talib.EMA(df["close"], timeperiod=8)

    df["slowk"], df["slowd"] = talib.STOCH(
        df["high"], df["low"], df["close"],
        fastk_period=5,
        slowk_period=3,
        slowk_matype=0,
        slowd_period=3,
        slowd_matype=0
    )

    upper, middle, lower = talib.BBANDS(
        df["close"],
        timeperiod=20,
        nbdevup=2,
        nbdevdn=2,
        matype=0
    )
    df["bb_width"] = (upper - lower) / middle

    df["not_squeezed"] = df["bb_width"] > df["bb_width"].rolling(10).median()

    df["buy_signal"] = (
        (df["ema3"] > df["ema8"])
        & (df["slowk"] > df["slowd"])
        & df["not_squeezed"]
    )

    df["sell_signal"] = (
        (df["ema3"] < df["ema8"])
        & (df["slowk"] < df["slowd"])
        & df["not_squeezed"]
    )

    latest = df.iloc[-1]
    signal = "NONE"
    strength = 0.0

    if latest["buy_signal"]:
        signal = "PUT"
        # Strength: how far %K is above %D (normalized)
        diff = latest["slowk"] - latest["slowd"]
        strength = diff / 100 if diff > 0 else 0.0
        # Add bonus from EMA3-EMA8 difference (scaled to 0-0.5)
        ema_diff = (latest["ema3"] - latest["ema8"]) / latest["close"] if latest["close"] else 0
        strength += max(0, min(ema_diff * 50, 0.5))
        strength = min(strength, 1.0)

    elif latest["sell_signal"]:
        signal = "CALL"
        diff = latest["slowd"] - latest["slowk"]
        strength = diff / 100 if diff > 0 else 0.0
        ema_diff = (latest["ema8"] - latest["ema3"]) / latest["close"] if latest["close"] else 0
        strength += max(0, min(ema_diff * 50, 0.5))
        strength = min(strength, 1.0)

    return {
        "signal": signal,
        "strength": round(strength, 3),
        "close": float(latest["close"]) if not np.isnan(latest["close"]) else None,
        "ema3": float(latest["ema3"]) if not np.isnan(latest["ema3"]) else None,
        "ema8": float(latest["ema8"]) if not np.isnan(latest["ema8"]) else None,
        "slowk": float(latest["slowk"]) if not np.isnan(latest["slowk"]) else None,
        "slowd": float(latest["slowd"]) if not np.isnan(latest["slowd"]) else None,
        "bb_width": float(latest["bb_width"]) if not np.isnan(latest["bb_width"]) else None,
    }