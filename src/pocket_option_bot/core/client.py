"""Wrapper for PocketOptionAsync client using the real BinaryOptionsToolsV2 API."""

import asyncio
import logging
from typing import Optional, Dict, Any, Callable, Awaitable, List, Union
from datetime import timedelta
from enum import Enum

from BinaryOptionsToolsV2 import PocketOptionAsync
from BinaryOptionsToolsV2.pocketoption.asynchronous import AsyncSubscription

from ..config import settings
from .events import EventBus

logger = logging.getLogger(__name__)


class ConnectionState(Enum):
    """Connection state enumeration."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


class PocketOptionClient:
    """
    Async client that wraps BinaryOptionsToolsV2.PocketOptionAsync.
    Provides connection, balance, trading, asset info, and real-time data subscriptions.
    """

    # Default configuration constants
    DEFAULT_BALANCE_POLL_INTERVAL = 10
    DEFAULT_RECONNECT_DELAY = 5
    DEFAULT_MAX_RECONNECT_ATTEMPTS = 5
    DEFAULT_CONNECTION_CHECK_INTERVAL = 5

    __slots__ = (
        "event_bus",
        "_client",
        "_running",
        "_state",
        "_balance_poll_task",
        "_connection_monitor_task",
        "_reconnect_task",
        "_balance_poll_interval",
        "_reconnect_delay",
        "_max_reconnect_attempts",
        "_connection_check_interval",
        "_cached_balance",
        "_client_lock",
        "_balance_lock",
    )

    def __init__(
        self,
        event_bus: EventBus,
        balance_poll_interval: int = DEFAULT_BALANCE_POLL_INTERVAL,
        reconnect_delay: int = DEFAULT_RECONNECT_DELAY,
        max_reconnect_attempts: int = DEFAULT_MAX_RECONNECT_ATTEMPTS,
        connection_check_interval: int = DEFAULT_CONNECTION_CHECK_INTERVAL,
    ):
        self.event_bus = event_bus
        self._client: Optional[PocketOptionAsync] = None
        self._running = False
        self._state = ConnectionState.DISCONNECTED

        # Background tasks
        self._balance_poll_task: Optional[asyncio.Task] = None
        self._connection_monitor_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None

        # Configurable intervals
        self._balance_poll_interval = balance_poll_interval
        self._reconnect_delay = reconnect_delay
        self._max_reconnect_attempts = max_reconnect_attempts
        self._connection_check_interval = connection_check_interval

        # Cached state
        self._cached_balance: float = 0.0

        # Locks for thread safety
        self._client_lock = asyncio.Lock()
        self._balance_lock = asyncio.Lock()

    # ──────────────────────────────────────────────
    # Connection Management
    # ──────────────────────────────────────────────

    async def connect(self) -> None:
        """Establish connection to Pocket Option."""
        if self._running:
            logger.warning("Client already running, ignoring connect() call")
            return

        self._running = True
        self._state = ConnectionState.CONNECTING
        await self._connect_internal()

    async def _connect_internal(self, attempt: int = 1) -> None:
        """Internal connection logic with retry."""
        try:
            async with self._client_lock:
                # Clean up any existing client
                if self._client:
                    try:
                        await self._client.shutdown()
                    except Exception:
                        pass
                    self._client = None

                # Create new client
                self._client = PocketOptionAsync(ssid=settings.pocket_option.ssid)
                await self._client.connect()

                # Wait for assets to load (important for trading)
                await self._client.wait_for_assets(timeout=60.0)

            self._state = ConnectionState.CONNECTED
            await self.event_bus.emit("connection_status", {"status": "connected"})
            logger.info(
                "Connected to Pocket Option (demo=%s, is_ssid_valid=%s)",
                settings.pocket_option.is_demo,
                self._client.is_ssid_valid(),
            )

            # Emit initial balance
            await self._emit_balance()

            # Start background tasks
            self._start_balance_poll()
            self._start_connection_monitor()

        except Exception as e:
            logger.error("Connection attempt %d failed: %s", attempt, e)
            self._state = ConnectionState.DISCONNECTED
            await self.event_bus.emit("connection_status", {"status": "disconnected"})

            if self._running and attempt < self._max_reconnect_attempts:
                delay = self._reconnect_delay * (2 ** (attempt - 1))  # Exponential backoff
                logger.info("Retrying in %d seconds (attempt %d/%d)...", delay, attempt + 1, self._max_reconnect_attempts)
                await asyncio.sleep(delay)
                await self._connect_internal(attempt + 1)
            elif self._running:
                logger.error("Max reconnection attempts reached, giving up")
                self._running = False
                await self.event_bus.emit("connection_status", {"status": "failed"})

    async def _reconnect_with_backoff(self) -> bool:
        """Attempt to reconnect with exponential backoff. Returns True if successful."""
        self._state = ConnectionState.RECONNECTING
        await self.event_bus.emit("connection_status", {"status": "reconnecting"})

        for attempt in range(self._max_reconnect_attempts):
            if not self._running:
                return False

            try:
                async with self._client_lock:
                    if self._client and self._client.is_connected():
                        # Already connected somehow
                        self._state = ConnectionState.CONNECTED
                        await self.event_bus.emit("connection_status", {"status": "connected"})
                        return True

                    # Use library's reconnect method
                    if self._client:
                        await self._client.reconnect()
                    else:
                        # Full reconnect
                        self._client = PocketOptionAsync(ssid=settings.pocket_option.ssid)
                        await self._client.connect()
                        await self._client.wait_for_assets(timeout=60.0)

                if self._client.is_connected():
                    self._state = ConnectionState.CONNECTED
                    await self.event_bus.emit("connection_status", {"status": "connected"})
                    await self._emit_balance()
                    logger.info("Reconnected successfully (attempt %d)", attempt + 1)
                    return True

            except Exception as e:
                logger.warning("Reconnect attempt %d failed: %s", attempt + 1, e)

            delay = min(self._reconnect_delay * (2 ** attempt), 60)
            await asyncio.sleep(delay)

        logger.error("All reconnect attempts exhausted")
        self._running = False
        self._state = ConnectionState.DISCONNECTED
        await self.event_bus.emit("connection_status", {"status": "failed"})
        return False

    def _start_balance_poll(self) -> None:
        """Start periodic balance polling."""
        if self._balance_poll_task:
            self._balance_poll_task.cancel()
        self._balance_poll_task = asyncio.create_task(self._balance_poll_loop())

    def _start_connection_monitor(self) -> None:
        """Start connection health monitoring."""
        if self._connection_monitor_task:
            self._connection_monitor_task.cancel()
        self._connection_monitor_task = asyncio.create_task(self._connection_monitor_loop())

    async def _balance_poll_loop(self) -> None:
        """Periodically fetch and emit balance."""
        while self._running and self._state == ConnectionState.CONNECTED:
            try:
                await asyncio.sleep(self._balance_poll_interval)
                if self._running and self._state == ConnectionState.CONNECTED:
                    await self._emit_balance()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Balance poll error: %s", e)
                await asyncio.sleep(self._balance_poll_interval)

    async def _connection_monitor_loop(self) -> None:
        """Monitor connection health and trigger reconnect on drop."""
        while self._running:
            try:
                await asyncio.sleep(self._connection_check_interval)
                if not self._running:
                    break

                # Check connection health
                async with self._client_lock:
                    if self._client and not self._client.is_connected():
                        logger.warning("Connection lost detected by monitor")
                        self._state = ConnectionState.DISCONNECTED
                        await self.event_bus.emit("connection_status", {"status": "disconnected"})
                        if self._running:
                            asyncio.create_task(self._reconnect_with_backoff())
                        break

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Connection monitor error: %s", e)

    async def _emit_balance(self) -> None:
        """Fetch and emit balance update."""
        async with self._client_lock:
            if not self._client or self._state != ConnectionState.CONNECTED:
                return
            client = self._client

        try:
            balance = await client.balance()
            balance_float = float(balance)

            # Update cache
            async with self._balance_lock:
                self._cached_balance = balance_float

            await self.event_bus.emit("balance_update", {"balance": balance_float})
        except Exception as e:
            logger.error("Failed to fetch balance: %s", e)

    async def disconnect(self) -> None:
        """Close the connection and clean up resources."""
        logger.info("Disconnecting...")
        self._running = False
        self._state = ConnectionState.DISCONNECTED

        # Cancel all background tasks
        for task in [self._balance_poll_task, self._connection_monitor_task, self._reconnect_task]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Shutdown client
        async with self._client_lock:
            if self._client:
                try:
                    await self._client.shutdown()
                except Exception as e:
                    logger.error("Error during client shutdown: %s", e)
                finally:
                    self._client = None

        await self.event_bus.emit("connection_status", {"status": "disconnected"})
        logger.info("Disconnected from Pocket Option")

    # ──────────────────────────────────────────────
    # Context Manager Support
    # ──────────────────────────────────────────────

    async def __aenter__(self) -> "PocketOptionClient":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.disconnect()

    # ──────────────────────────────────────────────
    # Account & Balance
    # ──────────────────────────────────────────────

    async def get_balance(self, force_refresh: bool = False) -> float:
        """
        Retrieve current account balance.

        Args:
            force_refresh: If True, fetch fresh from server. Otherwise return cached value.

        Returns:
            float: balance in account currency (USD for demo).
        """
        if force_refresh or self._cached_balance == 0.0:
            async with self._balance_lock:
                if force_refresh or self._cached_balance == 0.0:
                    async with self._client_lock:
                        if self._client and self._state == ConnectionState.CONNECTED:
                            try:
                                self._cached_balance = float(await self._client.balance())
                            except Exception as e:
                                logger.error("Failed to fetch balance: %s", e)
        return self._cached_balance

    async def is_demo(self) -> bool:
        """Check if using a demo account."""
        async with self._client_lock:
            if self._client:
                return self._client.is_demo()
        return True  # Default to demo for safety

    async def is_ssid_valid(self) -> bool:
        """Check if the SSID passed basic format validation."""
        async with self._client_lock:
            if self._client:
                return self._client.is_ssid_valid()
        return False

    @property
    def is_connected(self) -> bool:
        """Return current connection status."""
        return self._state == ConnectionState.CONNECTED

    @property
    def connection_state(self) -> ConnectionState:
        """Return detailed connection state."""
        return self._state

    # ──────────────────────────────────────────────
    # Assets & Market Data
    # ──────────────────────────────────────────────

    async def get_assets(self) -> List[Dict[str, Any]]:
        """
        Fetch all tradable assets with their details.

        Returns:
            List of asset dictionaries containing:
            - id, symbol, name, asset_type, payout, is_otc, is_active, allowed_candles
        """
        async with self._client_lock:
            if not self._client or self._state != ConnectionState.CONNECTED:
                return []
            client = self._client

        try:
            return await client.active_assets()
        except Exception as e:
            logger.error("Failed to fetch assets: %s", e)
            return []

    async def get_payout(self, asset: Optional[str] = None) -> Union[Dict[str, int], int, None]:
        """
        Get payout percentages for assets.

        Args:
            asset: Optional specific asset symbol. If None, returns all.

        Returns:
            Dict of asset->payout, or single int payout, or None if not found.
        """
        async with self._client_lock:
            if not self._client or self._state != ConnectionState.CONNECTED:
                return {} if asset is None else None
            client = self._client

        try:
            return await client.payout(asset)
        except Exception as e:
            logger.error("Failed to fetch payout: %s", e)
            return {} if asset is None else None

    async def get_server_time(self) -> Optional[int]:
        """Get current server time as Unix timestamp."""
        async with self._client_lock:
            if not self._client or self._state != ConnectionState.CONNECTED:
                return None
            client = self._client

        try:
            return await client.get_server_time()
        except Exception as e:
            logger.error("Failed to fetch server time: %s", e)
            return None

    # ──────────────────────────────────────────────
    # Historical Candles
    # ──────────────────────────────────────────────

    async def get_candles(
        self,
        asset: str,
        period: int,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Get historical candles for an asset.

        Args:
            asset: Asset symbol (e.g., "EURUSD_otc")
            period: Candle timeframe in seconds (1, 5, 15, 30, 60, 300)
            offset: Offset in seconds from now (0 = latest)

        Returns:
            List of candles with time, open, high, low, close.
        """
        async with self._client_lock:
            if not self._client or self._state != ConnectionState.CONNECTED:
                return []
            client = self._client

        try:
            return await client.get_candles(asset, period, offset)
        except Exception as e:
            logger.error("Failed to fetch candles for %s: %s", asset, e)
            return []

    async def get_candles_advanced(
        self,
        asset: str,
        period: int,
        offset: int,
        time: int,
    ) -> List[Dict[str, Any]]:
        """Get historical candles from a specific timestamp."""
        async with self._client_lock:
            if not self._client or self._state != ConnectionState.CONNECTED:
                return []
            client = self._client

        try:
            return await client.get_candles_advanced(asset, period, offset, time)
        except Exception as e:
            logger.error("Failed to fetch advanced candles for %s: %s", asset, e)
            return []

    async def compile_candles(
        self,
        asset: str,
        custom_period: int,
        lookback_period: int,
    ) -> List[Dict[str, Any]]:
        """
        Compile custom timeframe candles from raw ticks.

        Args:
            asset: Asset symbol
            custom_period: Desired candle duration in seconds
            lookback_period: Seconds of tick history to fetch

        Returns:
            List of compiled candles.
        """
        if custom_period <= 0 or lookback_period <= 0:
            raise ValueError("Periods must be positive integers")

        async with self._client_lock:
            if not self._client or self._state != ConnectionState.CONNECTED:
                return []
            client = self._client

        try:
            return await client.compile_candles(asset, custom_period, lookback_period)
        except Exception as e:
            logger.error("Failed to compile candles for %s: %s", asset, e)
            return []

    async def history(self, asset: str, period: int) -> List[Dict[str, Any]]:
        """Get historical price data (alternative endpoint)."""
        async with self._client_lock:
            if not self._client or self._state != ConnectionState.CONNECTED:
                return []
            client = self._client

        try:
            return await client.history(asset, period)
        except Exception as e:
            logger.error("Failed to fetch history for %s: %s", asset, e)
            return []

    # ──────────────────────────────────────────────
    # Real-time Candle Subscriptions
    # ──────────────────────────────────────────────

    async def subscribe_candles(
        self,
        asset: str,
        callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        interval: int = 60,
        mode: str = "time_aligned",
    ) -> Optional[AsyncSubscription]:
        """
        Subscribe to real-time candle updates for an asset.

        Args:
            asset: Asset symbol to subscribe to
            callback: Optional async callback to receive each candle
            interval: Candle interval in seconds
            mode: Subscription mode - "raw", "chunked", "timed", "time_aligned"

        Returns:
            AsyncSubscription object (can be used to iterate manually), or None on failure.
        """
        async with self._client_lock:
            if not self._client or self._state != ConnectionState.CONNECTED:
                logger.warning("Cannot subscribe: client not connected")
                return None
            client = self._client

        try:
            if mode == "raw":
                sub = await client.subscribe_symbol(asset)
            elif mode == "chunked":
                sub = await client.subscribe_symbol_chunked(asset, chunk_size=interval)
            elif mode == "timed":
                sub = await client.subscribe_symbol_timed(asset, timedelta(seconds=interval))
            elif mode == "time_aligned":
                sub = await client.subscribe_symbol_time_aligned(asset, timedelta(seconds=interval))
            else:
                raise ValueError(f"Unknown subscription mode: {mode}")

            # If callback provided, spawn background task to feed it
            if callback:
                asyncio.create_task(self._feed_subscription_callback(sub, callback, asset))

            logger.info("Subscribed to %s candles (mode=%s, interval=%ss)", asset, mode, interval)
            return sub

        except Exception as e:
            logger.error("Failed to subscribe to %s candles: %s", asset, e)
            return None

    async def _feed_subscription_callback(
        self,
        sub: AsyncSubscription,
        callback: Callable[[Dict[str, Any]], Awaitable[None]],
        asset: str,
    ) -> None:
        """Background task to feed subscription data to callback."""
        try:
            async for msg in sub:
                try:
                    # Emit to event bus as well
                    await self.event_bus.emit(f"candle_{asset}", msg)
                    # Call user callback
                    await callback(msg)
                except Exception as e:
                    logger.error("Subscription callback error for %s: %s", asset, e)
        except Exception as e:
            logger.error("Subscription stream error for %s: %s", asset, e)

    async def unsubscribe_candles(self, asset: str) -> None:
        """Unsubscribe from candle updates for an asset."""
        async with self._client_lock:
            if not self._client:
                return
            client = self._client

        try:
            await client.unsubscribe(asset)
            logger.info("Unsubscribed from %s candles", asset)
        except Exception as e:
            logger.error("Failed to unsubscribe from %s: %s", asset, e)

    # ──────────────────────────────────────────────
    # Trading
    # ──────────────────────────────────────────────

    async def place_trade(
        self,
        asset: str,
        direction: str,
        amount: float,
        duration: int,
        check_win: bool = False,
    ) -> Optional[str]:
        """
        Place a binary option trade.

        Args:
            asset: Asset symbol to trade
            direction: 'call' (buy) or 'put' (sell) - case insensitive
            amount: Stake amount in account currency
            duration: Trade duration in seconds
            check_win: If True, wait for trade result and return it in the result

        Returns:
            Trade ID if successful, None if failed.
        """
        direction = direction.lower()
        if direction not in ("call", "put"):
            logger.error("Invalid direction: %s (use 'call' or 'put')", direction)
            return None

        if amount <= 0:
            logger.error("Invalid amount: %s (must be positive)", amount)
            return None

        if duration <= 0:
            logger.error("Invalid duration: %s (must be positive)", duration)
            return None

        async with self._client_lock:
            if not self._client or self._state != ConnectionState.CONNECTED:
                logger.error("Cannot place trade: client not connected")
                return None
            client = self._client

        try:
            if direction == "call":
                trade_id, result = await client.buy(asset, amount, duration, check_win=check_win)
            else:  # put
                trade_id, result = await client.sell(asset, amount, duration, check_win=check_win)

            logger.info("Trade placed: %s %s for %.2f (ID: %s)", asset, direction, amount, trade_id)

            # Emit trade event
            await self.event_bus.emit("trade_placed", {
                "trade_id": trade_id,
                "asset": asset,
                "direction": direction,
                "amount": amount,
                "duration": duration,
                "result": result if check_win else None,
            })

            return trade_id

        except Exception as e:
            logger.error("Trade failed: %s", e)
            await self.event_bus.emit("trade_error", {
                "asset": asset,
                "direction": direction,
                "amount": amount,
                "error": str(e),
            })
            return None

    async def get_trade_result(self, trade_id: str, timeout_seconds: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """
        Check the result of a specific trade.

        Args:
            trade_id: Trade ID to check
            timeout_seconds: Max time to wait for result (default: trade duration + 30)

        Returns:
            Trade result dict with 'result' (win/loss/draw), 'profit', etc.
        """
        async with self._client_lock:
            if not self._client or self._state != ConnectionState.CONNECTED:
                return None
            client = self._client

        try:
            return await client.check_win(trade_id, timeout_seconds=timeout_seconds)
        except Exception as e:
            logger.error("Failed to get trade result for %s: %s", trade_id, e)
            return None

    async def get_trade_end_time(self, trade_id: str) -> Optional[int]:
        """Get expected close time of a trade as Unix timestamp."""
        async with self._client_lock:
            if not self._client or self._state != ConnectionState.CONNECTED:
                return None
            client = self._client

        try:
            return await client.get_deal_end_time(trade_id)
        except Exception as e:
            logger.error("Failed to get trade end time for %s: %s", trade_id, e)
            return None

    async def get_open_trades(self) -> List[Dict[str, Any]]:
        """Get all currently open trades."""
        async with self._client_lock:
            if not self._client or self._state != ConnectionState.CONNECTED:
                return []
            client = self._client

        try:
            deal_ids = await client.opened_deals()
            trades = []
            for deal_id in deal_ids:
                deal = await client.get_opened_deal(deal_id)
                if deal:
                    trades.append(deal)
            return trades
        except Exception as e:
            logger.error("Failed to fetch open trades: %s", e)
            return []

    async def get_closed_trades(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recently closed trades."""
        async with self._client_lock:
            if not self._client or self._state != ConnectionState.CONNECTED:
                return []
            client = self._client

        try:
            deal_ids = await client.closed_deals()
            trades = []
            for deal_id in deal_ids[:limit]:
                deal = await client.get_closed_deal(deal_id)
                if deal:
                    trades.append(deal)
            return trades
        except Exception as e:
            logger.error("Failed to fetch closed trades: %s", e)
            return []

    # ──────────────────────────────────────────────
    # Pending Orders
    # ──────────────────────────────────────────────

    async def get_pending_orders(self) -> List[Dict[str, Any]]:
        """Get all pending orders."""
        async with self._client_lock:
            if not self._client or self._state != ConnectionState.CONNECTED:
                return []
            client = self._client

        try:
            return await client.get_pending_deals()
        except Exception as e:
            logger.error("Failed to fetch pending orders: %s", e)
            return []

    async def place_pending_order(
        self,
        open_type: int,
        amount: float,
        asset: str,
        open_time: Union[int, str],
        open_price: float,
        timeframe: int,
        min_payout: int,
        command: int,
    ) -> Optional[Dict[str, Any]]:
        """
        Place a pending order.

        Args:
            open_type: Pending order type
            amount: Trade amount
            asset: Asset symbol
            open_time: Unix timestamp or "YYYY-MM-DD HH:MM:SS" string
            open_price: Price to trigger at
            timeframe: Trade duration in seconds
            min_payout: Minimum payout percentage
            command: 0 for Call, 1 for Put

        Returns:
            Order details or None on failure.
        """
        async with self._client_lock:
            if not self._client or self._state != ConnectionState.CONNECTED:
                return None
            client = self._client

        try:
            return await client.open_pending_order(
                open_type, amount, asset, open_time, open_price, timeframe, min_payout, command
            )
        except Exception as e:
            logger.error("Failed to place pending order: %s", e)
            return None

    async def cancel_pending_order(self, ticket: str) -> Optional[Dict[str, Any]]:
        """Cancel a pending order by ticket."""
        async with self._client_lock:
            if not self._client or self._state != ConnectionState.CONNECTED:
                return None
            client = self._client

        try:
            return await client.cancel_pending_order(ticket)
        except Exception as e:
            logger.error("Failed to cancel pending order %s: %s", ticket, e)
            return None

    async def cancel_pending_orders(self, tickets: List[str]) -> Optional[Dict[str, Any]]:
        """Cancel multiple pending orders."""
        async with self._client_lock:
            if not self._client or self._state != ConnectionState.CONNECTED:
                return None
            client = self._client

        try:
            return await client.cancel_pending_orders(tickets)
        except Exception as e:
            logger.error("Failed to cancel pending orders: %s", e)
            return None

    # ──────────────────────────────────────────────
    # Advanced / Raw API Access
    # ──────────────────────────────────────────────

    async def max_subscriptions(self) -> int:
        """Get maximum concurrent subscriptions allowed."""
        async with self._client_lock:
            if self._client:
                return self._client.max_subscriptions()
        return 0

    async def wait_for_assets(self, timeout: float = 60.0) -> None:
        """Wait for assets to be loaded from server."""
        async with self._client_lock:
            if not self._client:
                raise RuntimeError("Client not initialized")
            await self._client.wait_for_assets(timeout)

    async def clear_closed_deals(self) -> None:
        """Clear closed deals cache."""
        async with self._client_lock:
            if not self._client or self._state != ConnectionState.CONNECTED:
                return
            client = self._client

        try:
            await client.clear_closed_deals()
        except Exception as e:
            logger.error("Failed to clear closed deals: %s", e)
