"""Scans assets for high payout and manages switching."""

import asyncio
import logging
from typing import List, Dict, Any, Optional
from collections import deque

from ..config import settings
from .client import PocketOptionClient
from .events import EventBus

logger = logging.getLogger(__name__)

class AssetScanner:
    """Continuously scans assets and switches after wins."""

    def __init__(self, client: PocketOptionClient, event_bus: EventBus):
        self.client = client
        self.event_bus = event_bus
        self.min_payout = settings.bot.min_payout
        self._eligible_assets: List[Dict] = []
        self._recently_traded: deque = deque(maxlen=10)  # track last assets
        self._current_asset: Optional[str] = None
        self._scan_task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self):
        self._running = True
        await self.scan()
        # Subscribe to trade result events to switch after win
        self.event_bus.subscribe("trade_closed", self._on_trade_closed)
        # Periodic scan every 60 seconds
        self._scan_task = asyncio.create_task(self._periodic_scan())

    async def stop(self):
        self._running = False
        if self._scan_task:
            self._scan_task.cancel()
        self.event_bus.unsubscribe("trade_closed", self._on_trade_closed)

    async def scan(self):
        """Fetch assets and filter by payout >= min_payout, sort descending."""
        assets = await self.client.get_assets()
        eligible = []
        for symbol, info in assets.items():
            payout = info.get("payout", 0)
            if payout >= self.min_payout:
                eligible.append({
                    "symbol": symbol,
                    "payout": payout,
                    **info
                })
        eligible.sort(key=lambda x: x["payout"], reverse=True)
        self._eligible_assets = eligible
        logger.info(f"Found {len(eligible)} eligible assets")
        return eligible

    async def select_next_asset(self) -> Optional[str]:
        """Pick the highest payout asset not recently traded."""
        if not self._eligible_assets:
            await self.scan()
            if not self._eligible_assets:
                return None
        # Filter out recently traded
        for asset_info in self._eligible_assets:
            symbol = asset_info["symbol"]
            if symbol not in self._recently_traded:
                return symbol
        # If all have been traded recently, pick the first (highest payout) anyway
        return self._eligible_assets[0]["symbol"]

    async def switch_asset(self) -> Optional[str]:
        """Switch to a new asset and unsubscribe from old."""
        new_asset = await self.select_next_asset()
        if new_asset is None:
            return None
        if self._current_asset and self._current_asset != new_asset:
            await self.client.unsubscribe_candles(self._current_asset)
        self._current_asset = new_asset
        await self.client.subscribe_candles(new_asset)
        # Emit event
        await self.event_bus.emit("asset_switched", {
            "asset": new_asset,
            "payout": next((a["payout"] for a in self._eligible_assets if a["symbol"] == new_asset), 0)
        })
        logger.info(f"Switched to asset: {new_asset}")
        return new_asset

    async def force_switch(self):
        """Public method to force immediate switch."""
        await self.switch_asset()

    async def _periodic_scan(self):
        while self._running:
            await asyncio.sleep(60)
            await self.scan()

    async def _on_trade_closed(self, data: Dict):
        """After a winning trade, switch asset."""
        if data.get("result") == "WIN":
            # Switch to a different asset
            await self.switch_asset()
            # Record this asset as recently traded
            if self._current_asset:
                self._recently_traded.append(self._current_asset)