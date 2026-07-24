"""Wrapper for PocketOptionAsync client using the real BinaryOptionsToolsV2 API."""

import asyncio
import logging
from typing import Optional, Dict, Any, Callable, Awaitable

from BinaryOptionsToolsV2 import PocketOptionAsync

from ..config import settings
from .events import EventBus

logger = logging.getLogger(__name__)


class PocketOptionClient:
    """
    Async client that wraps BinaryOptionsToolsV2.PocketOptionAsync.
    Provides connection, balance, trading, and asset info.
    """

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self._client: Optional[PocketOptionAsync] = None
        self._running = False
        self._connected = False

    async def connect(self) -> None:
        """Establish connection to Pocket Option."""
        self._running = True
        await self._connect_internal()

    async def _connect_internal(self) -> None:
        try:
            self._client = PocketOptionAsync(
                ssid=settings.pocket_option.ssid,
                demo=settings.pocket_option.is_demo
            )
            await self._client.connect()
            self._connected = True
            await self.event_bus.emit("connection_status", {"status": "connected"})
            logger.info("Connected to Pocket Option (demo=%s)", settings.pocket_option.is_demo)
        except Exception as e:
            logger.error("Connection failed: %s", e)
            self._connected = False
            await self.event_bus.emit("connection_status", {"status": "disconnected"})
            if self._running:
                await asyncio.sleep(5)
                asyncio.create_task(self._connect_internal())

    async def disconnect(self) -> None:
        """Close the connection."""
        self._running = False
        if self._client:
            await self._client.disconnect()
        self._connected = False
        logger.info("Disconnected from Pocket Option")

    async def get_balance(self) -> float:
        """
        Retrieve current account balance.

        Returns:
            float: balance in USD (or account currency).
        """
        if self._client:
            # balance is an async method
            balance = await self._client.balance()
            return float(balance)
        return 0.0

    async def get_assets(self) -> Dict[str, Any]:
        """
        Fetch all tradable assets with their details (payout, etc.).

        Returns:
            dict: mapping of asset symbols to asset info.
        """
        if self._client:
            # active_assets is either a property or a method – try both.
            try:
                return self._client.active_assets
            except TypeError:
                # If it's a coroutine, await it
                return await self._client.active_assets()
        return {}

    async def subscribe_candles(self, asset: str, callback: Optional[Callable[[Dict], Awaitable[None]]] = None) -> None:
        """
        Subscribe to 1‑minute candles for an asset.

        The library uses subscribe_symbol(asset, interval, callback).
        For now, we only log; the callback logic must be implemented later.

        Args:
            asset: symbol to subscribe to.
            callback: async function to receive candle data.
        """
        if self._client:
            # Example: await self._client.subscribe_symbol(asset, interval=60, callback=callback)
            logger.info("Subscribing to %s candles (implementation pending)", asset)
            # We'll need to adapt the library's callback system here.
            # For now, keep as placeholder.
        else:
            logger.warning("Cannot subscribe: client not connected")

    async def unsubscribe_candles(self, asset: str) -> None:
        """
        Unsubscribe from candles.

        Args:
            asset: symbol to unsubscribe from.
        """
        if self._client:
            await self._client.unsubscribe(asset)
            logger.info("Unsubscribed from %s candles", asset)

    async def place_trade(self, asset: str, direction: str, amount: float, duration: int) -> Optional[str]:
        """
        Place a binary option trade.

        Args:
            asset: symbol to trade.
            direction: 'call' or 'put' (case‑insensitive).
            amount: stake in USD.
            duration: trade duration in seconds.

        Returns:
            Optional[str]: trade ID if successful, else None.
        """
        if not self._client:
            return None
        try:
            result = await self._client.buy(
                asset=asset,
                direction=direction.lower(),
                amount=amount,
                duration=duration
            )
            trade_id = result.get("id")
            logger.info("Trade placed: %s %s for $%.2f (ID: %s)", asset, direction, amount, trade_id)
            return trade_id
        except Exception as e:
            logger.error("Trade failed: %s", e)
            return None

    @property
    def is_connected(self) -> bool:
        """Return current connection status."""
        return self._connected