import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
from collections import deque

from client import POClient
from config import settings

logger = logging.getLogger(__name__)

class TradingBot:
    def __init__(self, client: POClient):
        self.client = client
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._current_asset: Optional[str] = None
        self._eligible_assets: list = []
        self._recent_trades = deque(maxlen=10)

        # Risk state (martingale)
        self.stake = settings.base_stake
        self.consecutive_losses = 0
        self.daily_pnl = 0.0
        self._last_day = datetime.now(timezone.utc).date()

        # Stats
        self.balance = 0.0
        self.total_pnl = 0.0
        self.wins = 0
        self.losses = 0
        self.trade_history = []

        # Candle data
        self.current_candle: Dict = {}
        self.current_price: Optional[float] = None   # last tick price

        # Connection
        self._connected = False
        self._data_task: Optional[asyncio.Task] = None
        self._update_interval = 3

        # Prevent multiple trades on same candle
        self._last_traded_candle_time: Optional[float] = None

        # Manual asset selection (None = auto)
        self._manual_asset: Optional[str] = None

        # Price subscription task
        self._price_task: Optional[asyncio.Task] = None

    # ---------- Connection ----------
    async def connect(self) -> bool:
        if self._connected:
            return True
        if await self.client.connect():
            self._connected = True
            self._data_task = asyncio.create_task(self._data_update_loop())
            await self._scan_assets()
            if self._eligible_assets:
                await self._switch_asset()
            asyncio.create_task(self._refresh_assets_loop())
            logger.info("Connected and data updates started")
            return True
        return False

    async def disconnect(self):
        self._connected = False
        self._running = False
        if self._task:
            self._task.cancel()
        if self._data_task:
            self._data_task.cancel()
        if self._price_task:
            self._price_task.cancel()
        await self.client.disconnect()
        logger.info("Disconnected")

    async def _data_update_loop(self):
        while self._connected:
            try:
                self.balance = await self.client.get_balance()
                await asyncio.sleep(self._update_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Data update error: {e}")
                await asyncio.sleep(5)

    async def _refresh_assets_loop(self):
        while self._connected:
            await asyncio.sleep(60)
            await self._scan_assets()
            logger.info("Asset payouts refreshed")

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

    def _auto_select_asset(self) -> Optional[str]:
        for a in self._eligible_assets:
            if a["symbol"] not in self._recent_trades:
                return a["symbol"]
        return self._eligible_assets[0]["symbol"] if self._eligible_assets else None

    async def _switch_asset(self):
        if not self._eligible_assets:
            return

        if self._manual_asset is not None:
            if self._manual_asset in [a["symbol"] for a in self._eligible_assets]:
                target = self._manual_asset
            else:
                self._manual_asset = None
                target = self._auto_select_asset()
        else:
            target = self._auto_select_asset()

        if target is None:
            return
        if self._current_asset == target:
            return

        # Unsubscribe old
        if self._current_asset:
            try:
                await self.client.unsubscribe(self._current_asset)
            except Exception as e:
                logger.warning(f"Unsubscribe error: {e}")

        self._current_asset = target
        self.current_candle = {}
        self.current_price = None
        self._last_traded_candle_time = None

        # Subscribe to candles
        asyncio.create_task(self.client.subscribe_candles(self._current_asset, self._on_candle))
        # Subscribe to price ticks (run in background)
        if self._price_task:
            self._price_task.cancel()
        self._price_task = asyncio.create_task(self.client.subscribe_price(self._current_asset, self._on_price))

        logger.info(f"Switched to asset: {self._current_asset}")

    async def set_manual_asset(self, asset: str) -> bool:
        if asset not in [a["symbol"] for a in self._eligible_assets]:
            return False
        self._manual_asset = asset
        await self._switch_asset()
        return True

    async def clear_manual_asset(self):
        self._manual_asset = None
        await self._switch_asset()

    def get_filtered_assets_for_display(self) -> List[Dict]:
        return [a for a in self._eligible_assets if "EUR" in a["symbol"] or "USD" in a["symbol"]]

    # ---------- Callbacks ----------
    async def _on_candle(self, candle: Dict):
        if candle.get("asset") != self._current_asset:
            return
        try:
            # Normalise time
            t = candle.get("time")
            if isinstance(t, datetime):
                candle["time"] = t.timestamp()
            elif isinstance(t, str):
                try:
                    dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
                    candle["time"] = dt.timestamp()
                except ValueError:
                    try:
                        dt = datetime.strptime(t, "%Y-%m-%d %H:%M:%S")
                        dt = dt.replace(tzinfo=timezone.utc)
                        candle["time"] = dt.timestamp()
                    except ValueError:
                        candle["time"] = datetime.now(timezone.utc).timestamp()
            elif not isinstance(t, (int, float)):
                candle["time"] = datetime.now(timezone.utc).timestamp()
            # Ensure open and close are floats
            candle["open"] = float(candle.get("open", 0))
            candle["close"] = float(candle.get("close", 0))
        except Exception as e:
            logger.error(f"Error processing candle: {e}")
            return

        self.current_candle = candle
        logger.debug(f"Candle updated: {candle.get('asset')} open={candle['open']} close={candle['close']}")

    async def _on_price(self, tick: Dict):
        if tick.get("asset") != self._current_asset:
            return
        self.current_price = tick.get("price")
        # logger.debug(f"Price update: {self.current_price}")

    # ---------- Trading ----------
    async def start_trading(self):
        if not self._connected:
            logger.error("Cannot start trading: not connected")
            return
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._trade_loop())
        logger.info("Trading started")

    async def stop_trading(self):
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
        while self._running:
            try:
                if self.current_candle and "time" in self.current_candle:
                    try:
                        candle_start = datetime.fromtimestamp(self.current_candle["time"], tz=timezone.utc)
                        now = datetime.now(timezone.utc)
                        seconds_into = (now - candle_start).total_seconds()

                        if 30.0 <= seconds_into < 31.0:
                            candle_time = self.current_candle["time"]
                            if self._last_traded_candle_time != candle_time:
                                logger.info(f"Signal triggered at {seconds_into:.1f}s for {self._current_asset}")
                                await self._on_signal()
                                self._last_traded_candle_time = candle_time
                    except Exception as e:
                        logger.error(f"Error processing candle time: {e}")
                        await asyncio.sleep(0.5)
                        continue
                await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Trade loop error: {e}")
                await asyncio.sleep(1)

    async def _on_signal(self):
        if not self._running:
            return

        # Use current price vs open to decide direction
        open_price = self.current_candle.get("open")
        if open_price is None or self.current_price is None:
            logger.warning("Missing price data, skipping trade")
            return

        direction = "CALL" if self.current_price > open_price else "PUT"
        stake = self.stake
        duration = settings.trade_duration

        logger.info(f"Trade signal: {direction} on {self._current_asset} | open={open_price} current={self.current_price}")

        result = await self.client.place_trade(
            self._current_asset, direction, stake, duration, check_win=True
        )
        if result:
            trade_id = result.get("id")
            win = result.get("result") == "win"
            try:
                profit = float(result.get("profit", 0.0))
            except (ValueError, TypeError):
                profit = 0.0

            trade = {
                "id": trade_id,
                "asset": self._current_asset,
                "direction": direction,
                "stake": stake,
                "open_time": datetime.now(timezone.utc).isoformat(),
                "result": "WIN" if win else "LOSS",
                "pnl": profit if win else -abs(profit),
            }
            self.trade_history.insert(0, trade)
            if len(self.trade_history) > 100:
                self.trade_history.pop()

            self.total_pnl += trade["pnl"]
            self.daily_pnl += trade["pnl"]

            self._recent_trades.append(self._current_asset)

            if win:
                self.wins += 1
                self.stake = settings.base_stake
                self.consecutive_losses = 0
            else:
                self.losses += 1
                self.consecutive_losses += 1
                self.stake = min(self.stake * settings.multiplier, settings.max_stake)
                if self.consecutive_losses >= settings.max_consecutive_losses:
                    self.reset_martingale()

            if self._manual_asset is None:
                await self._switch_asset()

            logger.info(f"Trade {trade['result']}: {direction} {self._current_asset} {stake:.2f} P&L: {trade['pnl']:.2f}")
        else:
            logger.error("Trade failed – no result")

    # ---------- Stats / UI ----------
    def get_candle_color(self) -> str:
        """Real‑time colour based on current price vs open."""
        if self.current_price is None:
            return "N/A"
        open_price = self.current_candle.get("open")
        if open_price is None:
            return "N/A"
        return "GREEN" if self.current_price > open_price else "RED"

    def get_seconds_into_candle(self) -> float:
        if not self.current_candle or "time" not in self.current_candle:
            return 0.0
        try:
            start = datetime.fromtimestamp(self.current_candle["time"], tz=timezone.utc)
            now = datetime.now(timezone.utc)
            return (now - start).total_seconds()
        except Exception:
            return 0.0

    def get_stats(self) -> Dict:
        total = self.wins + self.losses
        win_rate = (self.wins / total * 100) if total > 0 else 0.0

        current_payout = 0
        if self._current_asset:
            for a in self._eligible_assets:
                if a["symbol"] == self._current_asset:
                    current_payout = a["payout"]
                    break

        return {
            "balance": self.balance,
            "total_pnl": self.total_pnl,
            "daily_pnl": self.daily_pnl,
            "win_rate": round(win_rate, 2),
            "consecutive_wins": 0,
            "consecutive_losses": self.consecutive_losses,
            "current_stake": self.stake,
            "current_asset": self._current_asset,
            "current_payout": current_payout,
            "manual_asset": self._manual_asset,
            "trades": self.trade_history[:20],
            "candle": self.current_candle,
            "connected": self._connected,
            "running": self._running,
            "candle_color": self.get_candle_color(),
            "seconds_into_candle": round(self.get_seconds_into_candle(), 1),
        }

    def reset_stats(self):
        self.stake = settings.base_stake
        self.consecutive_losses = 0
        self.daily_pnl = 0.0
        self.balance = 0.0
        self.total_pnl = 0.0
        self.wins = 0
        self.losses = 0
        self.trade_history = []
        self.current_candle = {}
        self.current_price = None
        self._last_traded_candle_time = None
        self._recent_trades.clear()
        self._current_asset = None
        self._manual_asset = None

    def reset_martingale(self):
        self.stake = settings.base_stake
        self.consecutive_losses = 0