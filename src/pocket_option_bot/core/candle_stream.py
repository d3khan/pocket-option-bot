"""Manages 1‑minute candle data and emits signals at 30‑second mark."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from ..config import settings
from .events import EventBus

logger = logging.getLogger(__name__)

class CandleStream:
    """Processes candle ticks and triggers strategy at 30‑second mark."""

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.current_candle: Optional[Dict] = None
        self._running = False
        self._last_signal_time: Optional[datetime] = None

    async def start(self):
        self._running = True
        # Subscribe to candle events
        self.event_bus.subscribe("candle", self._on_candle)
        self.event_bus.subscribe("asset_switched", self._on_asset_switched)
        # We'll also need a timer to check 30‑second mark; we rely on incoming ticks.

    async def stop(self):
        self._running = False
        self.event_bus.unsubscribe("candle", self._on_candle)
        self.event_bus.unsubscribe("asset_switched", self._on_asset_switched)

    async def _on_candle(self, data: Dict):
        """Process incoming candle data."""
        # data structure: {"asset": ..., "open": ..., "close": ..., "high": ..., "low": ..., "time": ...}
        # It represents the current forming candle (index 0)
        self.current_candle = data
        # Check if it's 30‑second mark
        # The timestamp is the start time of the candle. We need current time to be 30 seconds into that minute.
        candle_start = datetime.fromtimestamp(data["time"], tz=timezone.utc)
        now = datetime.now(timezone.utc)
        seconds_into_candle = (now - candle_start).total_seconds()
        # Ensure within the same minute and not already processed
        if 30 <= seconds_into_candle < 31 and (self._last_signal_time is None or now > self._last_signal_time):
            self._last_signal_time = now
            # Emit signal event with the candle data and direction
            await self._emit_signal(data)

    async def _on_asset_switched(self, data: Dict):
        """Reset state on asset switch."""
        self.current_candle = None
        self._last_signal_time = None

    async def _emit_signal(self, candle: Dict):
        """Determine direction based on candle color and emit signal."""
        if candle["close"] > candle["open"]:
            direction = "CALL"
        else:
            direction = "PUT"
        signal = {
            "asset": candle["asset"],
            "direction": direction,
            "open": candle["open"],
            "close": candle["close"],
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        await self.event_bus.emit("signal_generated", signal)
        logger.info(f"Signal generated: {direction} on {candle['asset']} at close {candle['close']}")