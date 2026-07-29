import asyncio
import logging
import time
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import pocketoptionapi.global_value as global_value
from pocketoptionapi.stable_api import PocketOption

logger = logging.getLogger(__name__)

# Hard-coded SSID for the reverse-engineered candle-data connection
CANDLE_SSID = """42["auth",{"session":"ggh48uvsoq9n3ba3110f2lollh","isDemo":1,"uid":99439287,"platform":2,"isFastHistory":true,"isOptimized":true}]"""


class CandleClient:
    """Async wrapper around the reverse-engineered pocketoptionapi for candle data only."""

    def __init__(self):
        self._po = PocketOption(ssid=CANDLE_SSID, demo=True)
        self._connected = False
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="po_candles")

    async def connect(self, timeout: float = 30.0) -> bool:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(self._executor, self._po.connect)

        start = time.time()
        while not global_value.websocket_is_connected:
            if time.time() - start > timeout:
                logger.error("Candle client WebSocket connection timeout")
                return False
            await asyncio.sleep(0.2)

        await asyncio.sleep(2)  # Let init complete
        self._connected = True
        logger.info("Candle client connected")
        return True

    async def disconnect(self):
        self._connected = False
        if self._po:
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(self._executor, self._po.disconnect)
            except Exception as e:
                logger.warning(f"Candle client disconnect error: {e}")
        self._executor.shutdown(wait=False)
        logger.info("Candle client disconnected")

    async def get_candles(self, symbol: str, period: int = 60) -> pd.DataFrame:
        if not self._connected:
            raise RuntimeError("Candle client not connected")

        loop = asyncio.get_event_loop()

        # Subscribe to symbol
        ok = await loop.run_in_executor(self._executor, self._po.ChangeSymbol, symbol, period)
        if not ok:
            logger.warning(f"ChangeSymbol failed for {symbol}")
        await asyncio.sleep(1.5)

        # Fetch candles (count_request=1 to avoid hammering)
        await loop.run_in_executor(
            self._executor, self._po.get_candles, symbol, period, None, 6000, 1
        )
        await asyncio.sleep(1.0)

        # Extract dataframe from global state
        if symbol not in global_value.pairs:
            logger.warning(f"{symbol} not found in global_value.pairs")
            return pd.DataFrame()

        pair_data = global_value.pairs[symbol]
        if "dataframe" not in pair_data:
            logger.warning(f"No dataframe for {symbol}")
            return pd.DataFrame()

        df = pair_data["dataframe"].copy()
        if df.empty:
            return df

        # Normalise columns to lowercase strings
        df.columns = [str(c).lower() for c in df.columns]
        return df
