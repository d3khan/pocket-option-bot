"""Trading strategy implementation (candle color)."""

from datetime import datetime, timezone
from ..models.trade import Signal

class CandleColorStrategy:
    """Simple strategy: CALL if close > open else PUT."""

    @staticmethod
    def generate_signal(candle: dict) -> Signal:
        direction = "CALL" if candle["close"] > candle["open"] else "PUT"
        timestamp = candle.get("time")
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
        elif isinstance(timestamp, (int, float)):
            timestamp = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        return Signal(
            asset=candle["asset"],
            direction=direction,
            open=candle["open"],
            close=candle["close"],
            timestamp=timestamp
        )