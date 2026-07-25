import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any
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
        self._recent_trades = deque(maxlen=10)  # track last 10 assets

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

        # Candle
        self.current_candle: Dict = {}

        # Connection
        self._connected = False
        self._data_task: Optional[asyncio.Task] = None
        self._update_interval = 3

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
        if not self._eligible_assets:
            return

        # Pick the highest payout asset not in recent trades
        for a in self._eligible_assets:
            if a["symbol"] not in self._recent_trades:
                chosen = a
                break
        else:
            chosen = self._eligible_assets[0]

        if self._current_asset:
            try:
                await self.client.unsubscribe(self._current_asset)
            except Exception as e:
                logger.warning(f"Unsubscribe error: {e}")

        self._current_asset = chosen["symbol"]
        self.current_candle = {}  # Clear old candle data

        asyncio.create_task(self.client.subscribe_candles(self._current_asset, self._on_candle))
        logger.info(f"Switched to asset: {self._current_asset}")

    async def _on_candle(self, candle: Dict):
        # Ignore candles that don't match the current asset
        if candle.get("asset") != self._current_asset:
            return

        # Normalize time field to a timestamp (float)
        try:
            if "time" in candle:
                t = candle["time"]
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
                            logger.warning(f"Unrecognized time format: {t}")
                            candle["time"] = datetime.now(timezone.utc).timestamp()
                elif isinstance(t, (int, float)):
                    pass
                else:
                    candle["time"] = datetime.now(timezone.utc).timestamp()
            else:
                candle["time"] = datetime.now(timezone.utc).timestamp()
        except Exception as e:
            logger.error(f"Error processing candle time: {e}")
            candle["time"] = datetime.now(timezone.utc).timestamp()

        self.current_candle = candle
        logger.debug(f"Candle updated: {candle.get('asset')} close={candle.get('close')}")

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
        last_signal_time = None
        while self._running:
            try:
                if self.current_candle and "time" in self.current_candle:
                    try:
                        candle_start = datetime.fromtimestamp(self.current_candle["time"], tz=timezone.utc)
                        now = datetime.now(timezone.utc)
                        seconds_into = (now - candle_start).total_seconds()
                        if 30 <= seconds_into < 31 and (last_signal_time is None or now > last_signal_time):
                            last_signal_time = now
                            logger.info(f"Signal triggered at {seconds_into:.1f}s for {self._current_asset}")
                            await self._on_signal(self.current_candle)
                    except Exception as e:
                        logger.error(f"Error processing candle time: {e}")
                        await asyncio.sleep(1)
                        continue
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Trade loop error: {e}")
                await asyncio.sleep(1)

    async def _on_signal(self, candle: Dict):
        if not self._running:
            return
        direction = "PUT" if candle["close"] > candle["open"] else "CALL"
        stake = self.stake
        duration = settings.trade_duration

        logger.info(f"Trade signal: {direction} on {self._current_asset} at {candle['close']}")

        # Place trade and wait for result
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

            # Record this asset as recently traded
            self._recent_trades.append(self._current_asset)

            # --- Martingale logic ---
            if win:
                self.wins += 1
                self.stake = settings.base_stake
                self.consecutive_losses = 0
                # Switch to a new asset after a win
                await self._switch_asset()
            else:
                self.losses += 1
                self.consecutive_losses += 1
                self.stake = min(self.stake * settings.multiplier, settings.max_stake)
                if self.consecutive_losses >= settings.max_consecutive_losses or self.daily_pnl <= -settings.max_daily_loss:
                    logger.warning(f"Stop condition reached: losses={self.consecutive_losses}, daily_pnl={self.daily_pnl:.2f}")
                    await self.stop_trading()
            logger.info(f"Trade {trade['result']}: {direction} {self._current_asset} {stake:.2f} P&L: {trade['pnl']:.2f}")
        else:
            logger.error("Trade failed – no result")

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