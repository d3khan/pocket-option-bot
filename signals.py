# signals.py
import talib
import numpy as np
import pandas as pd

def signal(df: pd.DataFrame):
    """
    Trend‑following signal generator for 1m scalping.

    Rules:
      - BUY (CALL)  when close > EMA(20) AND RSI(14) > 60
      - SELL (PUT)  when close < EMA(20) AND RSI(14) < 40
      - Otherwise NONE
    """
    if df.empty or len(df) < 30:
        return {
            "signal": "NONE",
            "close": None,
            "ema20": None,
            "rsi": None,
        }

    df = df.copy().sort_index()

    df["ema20"] = talib.EMA(df["close"], timeperiod=20)
    df["rsi"] = talib.RSI(df["close"], timeperiod=14)

    # Ensure latest values are not NaN
    latest = df.iloc[-1]
    close = latest["close"]
    ema20 = latest["ema20"]
    rsi = latest["rsi"]

    if np.isnan(close) or np.isnan(ema20) or np.isnan(rsi):
        return {
            "signal": "NONE",
            "close": float(close) if not np.isnan(close) else None,
            "ema20": float(ema20) if not np.isnan(ema20) else None,
            "rsi": float(rsi) if not np.isnan(rsi) else None,
        }

    signal = "NONE"
    if close > ema20 and rsi > 60:
        signal = "CALL"
    elif close < ema20 and rsi < 40:
        signal = "PUT"

    return {
        "signal": signal,
        "close": float(close),
        "ema20": float(ema20),
        "rsi": float(rsi),
    }