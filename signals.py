# signal.py
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
    """
    if df.empty or len(df) < 30:
        return {
            "signal": "NONE",
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
    if latest["buy_signal"]:
        signal = "CALL"
    elif latest["sell_signal"]:
        signal = "PUT"

    return {
        "signal": signal,
        "close": float(latest["close"]) if not np.isnan(latest["close"]) else None,
        "ema3": float(latest["ema3"]) if not np.isnan(latest["ema3"]) else None,
        "ema8": float(latest["ema8"]) if not np.isnan(latest["ema8"]) else None,
        "slowk": float(latest["slowk"]) if not np.isnan(latest["slowk"]) else None,
        "slowd": float(latest["slowd"]) if not np.isnan(latest["slowd"]) else None,
        "bb_width": float(latest["bb_width"]) if not np.isnan(latest["bb_width"]) else None,
    }