import pytest
from datetime import datetime, timezone
from pocket_option_bot.core.strategy import CandleColorStrategy

def test_strategy_call():
    candle = {"asset": "EURUSD", "open": 1.10, "close": 1.11, "time": datetime.now(timezone.utc)}
    signal = CandleColorStrategy.generate_signal(candle)
    assert signal.direction == "CALL"

def test_strategy_put():
    candle = {"asset": "EURUSD", "open": 1.11, "close": 1.10, "time": datetime.now(timezone.utc)}
    signal = CandleColorStrategy.generate_signal(candle)
    assert signal.direction == "PUT"

def test_strategy_equal():
    candle = {"asset": "EURUSD", "open": 1.10, "close": 1.10, "time": datetime.now(timezone.utc)}
    signal = CandleColorStrategy.generate_signal(candle)
    assert signal.direction == "PUT"  # close <= open