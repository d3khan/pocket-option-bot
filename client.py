import asyncio
import logging
from typing import Optional, Dict, Any

from BinaryOptionsToolsV2 import PocketOptionAsync

logger = logging.getLogger(__name__)

class POClient:
    def __init__(self, ssid: str):
        self.ssid = ssid
        self._client: Optional[PocketOptionAsync] = None
        self._connected = False

    async def connect(self) -> bool:
        try:
            self._client = PocketOptionAsync(ssid=self.ssid)
            await self._client.connect()
            await self._client.wait_for_assets(timeout=60)
            self._connected = self._client.is_connected()
            return self._connected
        except Exception as e:
            logger.error(f"Connection error: {e}")
            return False

    async def disconnect(self):
        if self._client:
            await self._client.shutdown()
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected and self._client is not None and self._client.is_connected()

    async def get_balance(self) -> float:
        if not self.is_connected:
            return 0.0
        try:
            return float(await self._client.balance())
        except Exception as e:
            logger.error(f"Balance error: {e}")
            return 0.0

    async def get_assets(self) -> Dict[str, Any]:
        if not self.is_connected:
            return {}
        try:
            result = await self._client.active_assets()
            if isinstance(result, list):
                return {item.get("symbol", str(i)): item for i, item in enumerate(result)}
            return result
        except Exception as e:
            logger.error(f"Assets error: {e}")
            return {}

    async def get_payout(self, asset: str) -> int:
        if not self.is_connected:
            return 0
        try:
            return await self._client.payout(asset) or 0
        except Exception:
            return 0

    async def place_trade(self, asset: str, direction: str, amount: float, duration: int) -> Optional[str]:
        if not self.is_connected:
            return None
        direction = direction.lower()
        try:
            if direction == "call":
                trade_id, _ = await self._client.buy(asset, amount, duration, check_win=False)
            else:
                trade_id, _ = await self._client.sell(asset, amount, duration, check_win=False)
            return trade_id
        except Exception as e:
            logger.error(f"Trade error: {e}")
            return None

    async def check_trade_result(self, trade_id: str) -> Optional[Dict]:
        if not self.is_connected:
            return None
        try:
            return await self._client.check_win(trade_id)
        except Exception as e:
            logger.error(f"Check result error: {e}")
            return None

    async def get_candles(self, asset: str, period: int = 60, offset: int = 0):
        if not self.is_connected:
            return []
        try:
            return await self._client.get_candles(asset, period, offset)
        except Exception as e:
            logger.error(f"Candles error: {e}")
            return []

    async def subscribe_candles(self, asset: str, callback):
        """Subscribe to real‑time candles and call callback on each."""
        if not self.is_connected:
            return
        try:
            sub = await self._client.subscribe_symbol_time_aligned(asset, 60)
            async for candle in sub:
                await callback({**candle, "asset": asset})
        except Exception as e:
            logger.error(f"Subscription error: {e}")

    async def unsubscribe(self, asset: str):
        if self._client:
            await self._client.unsubscribe(asset)
