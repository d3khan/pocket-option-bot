"""Wrapper for PocketOptionAsync client."""

import asyncio
import logging
from typing import Optional, Dict, Any

from BinaryOptionsToolsV2 import PocketOptionAsync

from ..config import settings
from .events import EventBus

logger = logging.getLogger(__name__)

class PocketOptionClient:
    """Async client with reconnection logic."""

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self._client: Optional[PocketOptionAsync] = None
        self._running = False
        self._reconnect_task: Optional[asyncio.Task] = None
        self._connected = False

    async def connect(self):
        """Establish connection to Pocket Option."""
        self._running = True
        await self._connect_internal()

    async def _connect_internal(self):
        try:
            self._client = PocketOptionAsync(
                ssid=settings.pocket_option.ssid,
                demo=settings.pocket_option.is_demo
            )
            await self._client.connect()
            self._connected = True
            await self.event_bus.emit("connection_status", {"status": "connected"})
            logger.info("Connected to Pocket Option")
            # Subscribe to events
            self._client.on("candle", self._on_candle)
            self._client.on("trade_result", self._on_trade_result)
            self._client.on("balance_update", self._on_balance_update)
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            self._connected = False
            await self.event_bus.emit("connection_status", {"status": "disconnected"})
            # Schedule reconnection
            if self._running:
                await asyncio.sleep(5)
                asyncio.create_task(self._connect_internal())

    async def disconnect(self):
        self._running = False
        if self._client:
            await self._client.disconnect()
        self._connected = False

    async def subscribe_candles(self, asset: str):
        """Subscribe to 1‑minute candles for an asset."""
        if self._client:
            await self._client.subscribe(asset, interval=60)

    async def unsubscribe_candles(self, asset: str):
        if self._client:
            await self._client.unsubscribe(asset, interval=60)

    async def place_trade(self, asset: str, direction: str, amount: float, duration: int) -> Optional[str]:
        """Place a trade and return trade ID."""
        if not self._client:
            return None
        try:
            result = await self._client.buy(
                asset=asset,
                direction=direction.upper(),
                amount=amount,
                duration=duration
            )
            return result.get("id")
        except Exception as e:
            logger.error(f"Trade failed: {e}")
            return None

    async def get_balance(self) -> float:
        if self._client:
            return await self._client.get_balance()
        return 0.0

    async def get_assets(self) -> Dict[str, Any]:
        """Fetch all tradable assets with payout info."""
        if self._client:
            return await self._client.get_assets()
        return {}

    # Event handlers
    async def _on_candle(self, data: Dict):
        await self.event_bus.emit("candle", data)

    async def _on_trade_result(self, data: Dict):
        await self.event_bus.emit("trade_result", data)

    async def _on_balance_update(self, data: Dict):
        await self.event_bus.emit("balance_update", data)