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
        self.balance = 0.0

    async def connect(self, max_retries: int = 3) -> bool:
        for attempt in range(max_retries):
            try:
                self._client = PocketOptionAsync(ssid=self.ssid)
                await asyncio.wait_for(self._client.connect(), timeout=90)
                await asyncio.wait_for(self._client.wait_for_assets(timeout=90), timeout=90)
                self._connected = self._client.is_connected()
                if self._connected:
                    self.balance = float(await self._client.balance())
                    logger.info(f"Connected successfully (attempt {attempt + 1})")
                    return True
            except asyncio.TimeoutError:
                logger.warning(f"Connection attempt {attempt + 1} timed out")
            except Exception as e:
                logger.error(f"Connection attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(5)
        logger.error("All connection attempts failed.")
        self._connected = False
        return False

    async def disconnect(self):
        if self._client:
            try:
                await self._client.shutdown()
            except Exception:
                pass
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected and self._client is not None and self._client.is_connected()

    async def refresh_balance(self):
        if self.is_connected:
            try:
                self.balance = float(await self._client.balance())
            except Exception:
                pass

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

    async def get_all_payouts(self) -> Dict[str, int]:
        """Fetch payout % for all assets at once."""
        if not self.is_connected:
            return {}
        try:
            result = await self._client.payout()
            if isinstance(result, dict):
                return result
            return {}
        except Exception as e:
            logger.error(f"All payouts error: {e}")
            return {}

    async def get_payout(self, asset: str) -> int:
        if not self.is_connected:
            return 0
        try:
            return await self._client.payout(asset) or 0
        except Exception:
            return 0

    async def place_trade(self, asset: str, direction: str, amount: float, duration: int, check_win: bool = False) -> Optional[Dict]:
        if not self.is_connected:
            return None
        direction = direction.lower()
        try:
            if direction == "call":
                trade_id, result = await self._client.buy(asset, amount, duration, check_win=check_win)
            else:
                trade_id, result = await self._client.sell(asset, amount, duration, check_win=check_win)
            if check_win:
                return {"id": trade_id, "result": result["result"], "profit": result["profit"]}
            else:
                return {"id": trade_id}
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

    async def subscribe_candles(self, asset: str, callback, period: int = 60):
        if not self.is_connected:
            return
        try:
            from datetime import timedelta
            sub = await self._client.subscribe_symbol_time_aligned(asset, timedelta(seconds=period))
            async for candle in sub:
                await callback(candle)
        except Exception as e:
            logger.error(f"Subscription error: {e}")

    async def unsubscribe(self, asset: str):
        if self._client:
            try:
                await self._client.unsubscribe(asset)
            except Exception:
                pass
