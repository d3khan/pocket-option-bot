import asyncio
import logging
import time
from concurrent.futures import ProcessPoolExecutor
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

CANDLE_SSID = """42["auth",{"session":"ggh48uvsoq9n3ba3110f2lollh","isDemo":1,"uid":99439287,"platform":2,"isFastHistory":true,"isOptimized":true}]"""


def _fetch_signals_for_batch(symbols: List[str], period: int, ssid: str) -> Dict[str, Any]:
    """
    Worker function – runs in an isolated OS process.
    Uses the raw PO api (bypassing stable_api wrappers) for speed.
    """
    # Create a fresh event loop for this process
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())

    # Stub optional deps
    import sys
    from types import ModuleType
    for mod_name in ("webview", "tzlocal"):
        if mod_name not in sys.modules:
            stub = ModuleType(mod_name)
            if mod_name == "webview":
                stub.create_window = lambda *a, **k: None
                stub.start = lambda *a, **k: None
            else:
                class _Tz:
                    def __str__(self): return "UTC"
                stub.get_localzone = lambda: _Tz()
            sys.modules[mod_name] = stub

    import pandas as pd
    from pocketoptionapi.stable_api import PocketOption
    import pocketoptionapi.global_value as gv
    from signals import signal

    # Reset global state to force a fresh connection
    gv.websocket_is_connected = False

    po = PocketOption(ssid=ssid, demo=True)
    connected = po.connect()
    if not connected:
        logger.warning("Worker PO connection failed")
        try:
            po.disconnect()
        except Exception:
            pass
        return {}

    # Wait for websocket handshake (max 15 s)
    t0 = time.time()
    while not gv.websocket_is_connected and time.time() - t0 < 15:
        time.sleep(0.05)

    if not gv.websocket_is_connected:
        logger.warning("Worker PO websocket failed to connect")
        try:
            po.disconnect()
        except Exception:
            pass
        return {}

    results = {}
    for symbol in symbols:
        try:
            po.api.history_new = None
            po.api.change_symbol(symbol, period)

            t0 = time.time()
            while po.api.history_new is None and time.time() - t0 < 5:
                time.sleep(0.05)

            if po.api.history_new is None:
                continue

            his = po.api.history_new
            candles = []

            if isinstance(his, dict):
                if "candles" in his and his["candles"]:
                    for c in his["candles"]:
                        candles.append({
                            "time": c[0], "open": c[1], "high": c[3],
                            "low": c[4], "close": c[2],
                        })
                elif "history" in his and his["history"]:
                    for h in his["history"]:
                        candles.append({
                            "time": h["time"], "open": h["open"],
                            "high": h["high"], "low": h["low"],
                            "close": h["close"],
                        })

            if len(candles) < 30:
                continue

            df = pd.DataFrame(candles)
            df.columns = [str(c).lower() for c in df.columns]
            if not {"open", "high", "low", "close"}.issubset(df.columns):
                continue

            sig = signal(df)
            if sig["signal"] != "NONE":
                sig["symbol"] = symbol
                results[symbol] = sig

        except Exception as exc:
            logger.debug(f"Worker error on {symbol}: {exc}")
            continue

    # Clean disconnection and reset global state
    try:
        po.disconnect()
    except Exception:
        pass
    gv.websocket_is_connected = False

    return results


class MultiCandleClient:
    def __init__(self, num_workers: int = 5, ssid: str = CANDLE_SSID):
        self.num_workers = num_workers
        self.ssid = ssid
        self._executor: ProcessPoolExecutor | None = None

    async def connect(self) -> bool:
        self._executor = ProcessPoolExecutor(max_workers=self.num_workers)
        logger.info(
            f"MultiCandleClient ready ({self.num_workers} workers, "
            f"~{self.num_workers} isolated PO connections)"
        )
        return True

    async def disconnect(self):
        if self._executor:
            self._executor.shutdown(wait=False)
            self._executor = None
        logger.info("MultiCandleClient shutdown")

    async def fetch_signals(
        self, symbols: List[str], period: int = 60, timeout_per_batch: float = 30.0
    ) -> Dict[str, Any]:
        if not symbols or not self._executor:
            return {}

        batch_size = max(1, (len(symbols) + self.num_workers - 1) // self.num_workers)
        batches = [symbols[i:i + batch_size] for i in range(0, len(symbols), batch_size)]

        loop = asyncio.get_event_loop()
        futures = [
            asyncio.wait_for(
                loop.run_in_executor(
                    self._executor,
                    _fetch_signals_for_batch,
                    batch,
                    period,
                    self.ssid,
                ),
                timeout=timeout_per_batch,
            )
            for batch in batches
        ]

        results = await asyncio.gather(*futures, return_exceptions=True)

        all_signals: Dict[str, Any] = {}
        for idx, res in enumerate(results):
            if isinstance(res, asyncio.TimeoutError):
                logger.warning(f"Worker {idx} timed out after {timeout_per_batch}s")
                continue
            if isinstance(res, Exception):
                logger.error(f"Worker {idx} crashed: {res}")
                continue
            all_signals.update(res)

        logger.info(
            f"Parallel scan complete: {len(all_signals)} signals from "
            f"{len(symbols)} assets ({self.num_workers} workers)"
        )
        return all_signals