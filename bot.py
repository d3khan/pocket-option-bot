import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from client import POClient
from config import settings

logger = logging.getLogger(__name__)

class TradingBot:
    def __init__(self, client: POClient):
        self.client = client
        self._running = False          # trading loop active
        self._task: Optional[asyncio.Task] = None
        self._current_asset: Optional[str] = None
        self._eligible_assets: list = []
        self._recent_trades = []

        # Risk state
        self.stake = settings.base_stake
        self.consecutive_losses = 0
        self.daily_pnl = 0.0
        self._last_day = datetime.now(timezone.utc).date()

        # Stats for UI
        self.balance = 0.0
        self.total_pnl = 0.0
        self.wins = 0
        self.losses = 0
        self.trade_history = []

        # Candle display
        self.current_candle: Dict = {}

        # Connection and data update
        self._connected = False
        self._data_task: Optional[asyncio.Task] = None
        self._update_interval = 3  # seconds

    # ---------- Connection Management ----------
    async def connect(self) -> bool:
        """Establish connection and start data updates."""
        if self._connected:
            return True
        if await self.client.connect():
            self._connected = True
            # Start background data update loop (balance, assets, candles)
            self._data_task = asyncio.create_task(self._data_update_loop())
            # Initial scan for assets
            await self._scan_assets()
            if self._eligible_assets:
                await self._switch_asset()
            logger.info("Connected and data updates started")
            return True
        return False

    async def disconnect(self):
        """Disconnect and stop all loops."""
        self._connected = False
        self._running = False
        if self._task:
            self._task.cancel()
        if self._data_task:
            self._data_task.cancel()
        await self.client.disconnect()
        logger.info("Disconnected")

    async def _data_update_loop(self):
        """Continuous loop to update balance and other stats."""
        while self._connected:
            try:
                self.balance = await self.client.get_balance()
                # Optionally update other data
                await asyncio.sleep(self._update_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Data update error: {e}")
                await asyncio.sleep(5)

    # ---------- Asset Management ----------
    async def _scan_assets(self):
        assets = await self.client.get_assets()
        self._eligible_assets = []
        for symbol, info in assets.items():
            if symbol not in settings.allowed_assets:
                continue
            payout = info.get("payout", 0)
            if payout >= settings.min_payout:
                self._eligible_assets.append({"symbol": symbol, "payout": payout})
        self._eligible_assets.sort(key=lambda x: x["payout"], reverse=True)
        logger.info(f"Eligible assets: {len(self._eligible_assets)}")

    async def _switch_asset(self):
        traded = [t["asset"] for t in self._recent_trades[-10:]]
        chosen = None
        for a in self._eligible_assets:
            if a["symbol"] not in traded:
                chosen = a
                break
        if chosen is None and self._eligible_assets:
            chosen = self._eligible_assets[0]

        if not chosen:
            return

        if self._current_asset:
            await self.client.unsubscribe(self._current_asset)

        self._current_asset = chosen["symbol"]
        asyncio.create_task(self.client.subscribe_candles(self._current_asset, self._on_candle))
        logger.info(f"Switched to asset: {self._current_asset}")

    async def _on_candle(self, candle: Dict):
        self.current_candle = candle

    # ---------- Trading Control ----------
    async def start_trading(self):
        """Start the trading loop (signals -> trades)."""
        if not self._connected:
            logger.error("Cannot start trading: not connected")
            return
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._trade_loop())
        logger.info("Trading started")

    async def stop_trading(self):
        """Stop the trading loop (but keep data updates)."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("Trading stopped")

    async def _trade_loop(self):
        """Main trading loop: scans for signals and executes trades."""
        last_signal_time = None
        while self._running:
            try:
                if self.current_candle:
                    now = datetime.now(timezone.utc)
                    candle_start = datetime.fromtimestamp(self.current_candle["time"], tz=timezone.utc)
                    seconds_into = (now - candle_start).total_seconds()
                    if 30 <= seconds_into < 31 and (last_signal_time is None or now > last_signal_time):
                        last_signal_time = now
                        await self._on_signal(self.current_candle)
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Trade loop error: {e}")
                await asyncio.sleep(1)

    async def _on_signal(self, candle: Dict):
        if not self._running:
            return
        direction = "CALL" if candle["close"] > candle["open"] else "PUT"
        stake = self.stake
        duration = settings.trade_duration
        trade_id = await self.client.place_trade(self._current_asset, direction, stake, duration)
        if trade_id:
            trade = {
                "id": trade_id,
                "asset": self._current_asset,
                "direction": direction,
                "stake": stake,
                "open_time": datetime.now(timezone.utc).isoformat(),
                "result": "Pending",
                "pnl": None,
            }
            self.trade_history.insert(0, trade)
            if len(self.trade_history) > 100:
                self.trade_history.pop()
            logger.info(f"Trade placed: {direction} {self._current_asset} {stake}")

            result = await self.client.check_trade_result(trade_id)
            if result:
                win = result.get("result") == "win"
                profit = result.get("profit", 0.0)
                trade["result"] = "WIN" if win else "LOSS"
                trade["pnl"] = profit if win else -abs(profit)
                self.total_pnl += trade["pnl"]
                self.daily_pnl += trade["pnl"]
                if win:
                    self.wins += 1
                    self.stake = settings.base_stake
                    self.consecutive_losses = 0
                    await self._switch_asset()
                else:
                    self.losses += 1
                    self.consecutive_losses += 1
                    self.stake = min(self.stake * settings.multiplier, settings.max_stake)
                    if self.consecutive_losses >= settings.max_consecutive_losses or self.daily_pnl <= -settings.max_daily_loss:
                        logger.warning("Stop condition reached, stopping trading")
                        await self.stop_trading()

    def get_stats(self) -> Dict:
        total = self.wins + self.losses
        win_rate = (self.wins / total * 100) if total > 0 else 0.0
        return {
            "balance": self.balance,
            "total_pnl": self.total_pnl,
            "daily_pnl": self.daily_pnl,
            "win_rate": round(win_rate, 2),
            "consecutive_wins": 0,
            "consecutive_losses": self.consecutive_losses,
            "current_stake": self.stake,
            "current_asset": self._current_asset,
            "trades": self.trade_history[:20],
            "candle": self.current_candle,
            "connected": self._connected,
            "running": self._running,
        }