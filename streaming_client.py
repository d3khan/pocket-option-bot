import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, Callable

from BinaryOptionsToolsV2 import PocketOptionAsync

from config import settings

logger = logging.getLogger(__name__)


class StreamingClient:
    """Live candle stream for a single asset using BinaryOptionsToolsV2."""

    def __init__(self, ssid: str, asset: str = settings.trade_asset):
        self.ssid = ssid
        self.asset = asset
        self._api: Optional[PocketOptionAsync] = None
        self._connected = False
        self._subscription = None
        self._signal_callback: Optional[Callable] = None

        # Candle state
        self.candle_open: Optional[float] = None
        self.candle_time: Optional[datetime] = None
        self.candle_close_time: Optional[datetime] = None
        self.last_tick_price: Optional[float] = None
        self.current_price: Optional[float] = None
        self.candle_high: float = 0.0
        self.candle_low: float = 0.0
        self._running = False
        self._monitor_task: Optional[asyncio.Task] = None
        self._trigger_sent = False  # prevent multiple trades per candle

        # NEW: signal control flags
        self._signal_enabled = False      # whether trading is allowed
        self._can_signal = False          # whether we are past the first full candle
        self._wait_for_next_candle = False

    async def connect(self) -> bool:
        """Connect to Pocket Option and subscribe to the asset."""
        try:
            self._api = PocketOptionAsync(ssid=self.ssid)
            await self._api.connect()
            await self._api.wait_for_assets(timeout=60)
            self._connected = True
            logger.info(f"Streaming client connected for {self.asset}")
            return True
        except Exception as e:
            logger.error(f"Streaming connect error: {e}")
            return False

    async def disconnect(self):
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        if self._api:
            try:
                await self._api.shutdown()
            except Exception:
                pass
        self._connected = False
        logger.info("Streaming client disconnected")

    async def start_streaming(self, signal_callback: Callable):
        """
        Start the live stream and call the callback when a signal is generated.
        The callback should accept a dict with:
            'symbol', 'direction', 'open', 'current', 'timestamp'
        """
        if not self._connected:
            logger.error("Cannot start streaming: not connected")
            return

        self._signal_callback = signal_callback
        self._running = True
        # Ensure signals are disabled initially
        self._signal_enabled = False
        self._can_signal = False
        self._wait_for_next_candle = False
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info(f"Streaming started for {self.asset} (signals disabled)")

    async def stop_streaming(self):
        """Stop the monitor loop and unsubscribe."""
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
        self._signal_enabled = False
        self._can_signal = False
        self._wait_for_next_candle = False
        logger.info("Streaming stopped")

    def enable_signals(self):
        """Enable signal generation, but wait for the next complete candle before actually sending any."""
        if not self._running:
            logger.warning("Cannot enable signals: streaming not running")
            return
        self._signal_enabled = True
        self._can_signal = False
        self._wait_for_next_candle = True
        # Prevent immediate trigger on the current candle
        self._trigger_sent = True
        logger.info("Signals enabled – will start trading from the next candle.")

    def disable_signals(self):
        """Disable signal generation."""
        self._signal_enabled = False
        self._can_signal = False
        self._wait_for_next_candle = False
        logger.info("Signals disabled.")

    async def _monitor_loop(self):
        """Main monitoring loop – subscribes to ticks and processes the forming candle."""
        try:
            # Subscribe to the asset (ticks)
            self._subscription = await self._api.subscribe_symbol(self.asset)
            logger.info(f"Subscribed to {self.asset}")

            # Wait for first tick
            first_tick = await anext(self._subscription)
            self.last_tick_price = float(first_tick['close'])

            # Align to the start of the current minute
            now = datetime.now(timezone.utc)
            self.candle_time = now.replace(second=0, microsecond=0)
            self.candle_open = self.last_tick_price
            self.candle_high = self.last_tick_price
            self.candle_low = self.last_tick_price
            self.candle_close_time = self.candle_time + timedelta(minutes=1)
            self._trigger_sent = False

            logger.info(
                f"Candle started at {self.candle_time.strftime('%H:%M:%S')}, "
                f"open = {self.candle_open:.5f}"
            )

            # Process incoming ticks
            async for tick in self._subscription:
                if not self._running:
                    break

                price = float(tick['close'])
                self.last_tick_price = price
                self.current_price = price
                self.candle_high = max(self.candle_high, price)
                self.candle_low = min(self.candle_low, price)

                now = datetime.now(timezone.utc)
                seconds_into_candle = (now - self.candle_time).total_seconds()

                # --- Trade trigger at 28 seconds ---
                if (seconds_into_candle >= settings.trade_trigger_seconds and
                    not self._trigger_sent and
                    self.candle_open is not None and
                    self._signal_enabled and
                    self._can_signal):   # <-- NEW condition

                    # Determine direction: RED candle at trigger time → CALL, else PUT
                    # RED = current price < open; GREEN = current price >= open
                    if self.current_price < self.candle_open:
                        direction = "PUT"
                    else:
                        direction = "CALL"

                    signal_data = {
                        "symbol": self.asset,
                        "direction": direction,
                        "open": self.candle_open,
                        "current": self.current_price,
                        "timestamp": now.isoformat(),
                        "seconds_into_candle": seconds_into_candle,
                    }

                    logger.info(
                        f"🎯 SIGNAL at {seconds_into_candle:.1f}s: "
                        f"{direction} on {self.asset} | "
                        f"Open: {self.candle_open:.5f} | "
                        f"Current: {self.current_price:.5f} | "
                        f"Diff: {self.current_price - self.candle_open:+.5f}"
                    )

                    if self._signal_callback:
                        await self._signal_callback(signal_data)

                    self._trigger_sent = True  # only one trade per candle

                # --- Candle close: reset for next candle ---
                if now >= self.candle_close_time:
                    # New candle: open = last tick price of previous candle
                    self.candle_open = self.last_tick_price
                    self.candle_time = self.candle_close_time
                    self.candle_close_time = self.candle_time + timedelta(minutes=1)
                    self.candle_high = self.last_tick_price
                    self.candle_low = self.last_tick_price
                    self._trigger_sent = False

                    # If we were waiting for the next candle, now we can allow signals
                    if self._wait_for_next_candle:
                        self._wait_for_next_candle = False
                        self._can_signal = True
                        logger.info("✅ First full candle passed – signal generation is now active.")

                    logger.info(
                        f"🕐 NEW CANDLE: {self.candle_time.strftime('%H:%M:%S')} | "
                        f"Open: {self.candle_open:.5f}"
                    )

        except asyncio.CancelledError:
            logger.info("Monitor loop cancelled")
        except Exception as e:
            logger.error(f"Monitor loop error: {e}", exc_info=True)
        finally:
            self._running = False