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
        self._ready = False
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

        # Connection
        self._connected = False
        self._data_task: Optional[asyncio.Task] = None
        self._update_interval = 0.5

    # ---------- Connection ----------
    async def connect(self) -> bool:
        if self._connected:
            return True
        if await self.client.connect():
            self._connected = True
            self._data_task = asyncio.create_task(self._data_update_loop())
            await self._scan_assets()
            self._ready = True
            logger.info("Connected and asset list loaded")
            return True
        return False

    async def disconnect(self):
        self._connected = False
        self._running = False
        self._ready = False
        if self._task:
            self._task.cancel()
        if self._data_task:
            self._data_task.cancel()
        await self.client.disconnect()
        logger.info("Disconnected")

    async def _data_update_loop(self):
        while self._connected:
            try:
                await self.client.refresh_balance()
                self.balance = self.client.balance
                await asyncio.sleep(self._update_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Data update error: {e}")
                await asyncio.sleep(1)

    # ---------- Asset Management ----------
    async def _scan_assets(self):
        assets = await self.client.get_assets()
        self._eligible_assets = []
        for symbol, info in assets.items():
            if info.get("is_otc") is not True:
                continue
            if info.get("is_active") is False:
                continue
            payout = info.get("payout", 0)
            if payout >= settings.min_payout:
                self._eligible_assets.append({"symbol": symbol, "payout": payout})
        self._eligible_assets.sort(key=lambda x: x["payout"], reverse=True)
        logger.info(f"Eligible OTC assets (payout >= {settings.min_payout}%): {len(self._eligible_assets)}")

    # ---------- RSI / EMA calculation ----------
    def _compute_ema(self, prices: list, period: int = 20) -> float:
        prices = [float(p) for p in prices]
        if len(prices) < period:
            return prices[-1] if prices else 0.0
        k = 2.0 / (period + 1)
        ema = sum(prices[:period]) / period
        for price in prices[period:]:
            ema = price * k + ema * (1 - k)
        return ema

    def _compute_rsi(self, prices: list, period: int = 14) -> float:
        prices = [float(p) for p in prices]
        if len(prices) < period + 1:
            return 50.0
        gains, losses = 0.0, 0.0
        for i in range(1, period + 1):
            diff = prices[-i] - prices[-i-1]
            if diff > 0:
                gains += diff
            else:
                losses -= diff
        if losses == 0:
            return 100.0
        return 100.0 - (100.0 / (1.0 + gains / losses))

    async def _fetch_asset_indicators(self, symbol: str) -> tuple[float, float, list]:
        period = settings.candle_period
        offset = 20 * period
        candles = await self.client.get_candles(symbol, period=period, offset=offset)
        if len(candles) < 20:
            logger.warning(f"Only {len(candles)} candles for {symbol}, cannot compute indicators")
            return 50.0, 0.0, []
        closes = [float(c['close']) for c in candles[-20:]]
        ema = self._compute_ema(closes, settings.ema_period)
        rsi = self._compute_rsi(closes, settings.rsi_period)
        return rsi, ema, closes

    def _compute_score(self, rsi: float, ema: float, price: float) -> float:
        rsi_gap = abs(rsi - 50)
        price_diff = abs(price - ema) / (ema or 1)
        return rsi_gap + price_diff * 50

    async def _pick_best_asset(self) -> Optional[Dict]:
        if not self._eligible_assets:
            await self._scan_assets()
            if not self._eligible_assets:
                return None

        best_score = -1
        best_asset = None
        best_rsi = 50
        best_ema = 0.0
        best_price = 0.0

        for a in self._eligible_assets:
            symbol = a["symbol"]
            if symbol in self._recent_trades:
                continue
            try:
                rsi, ema, closes = await self._fetch_asset_indicators(symbol)
                if not closes:
                    continue
                price = closes[-1]
                score = self._compute_score(rsi, ema, price)
                logger.debug(f"📊 {symbol}: RSI={rsi:.2f}, EMA={ema:.5f}, price={price:.5f}, score={score:.2f}")
                if score > best_score:
                    best_score = score
                    best_asset = {"symbol": symbol, "payout": a["payout"]}
                    best_rsi = rsi
                    best_ema = ema
                    best_price = price
            except Exception as e:
                logger.error(f"Error fetching indicators for {symbol}: {e}")

        if best_asset:
            logger.info(f"🏆 Best asset: {best_asset['symbol']} with score {best_score:.2f} (RSI={best_rsi:.2f}, EMA={best_ema:.5f}, price={best_price:.5f})")
            return {
                "symbol": best_asset["symbol"],
                "rsi": best_rsi,
                "ema": best_ema,
                "price": best_price,
                "score": best_score,
            }
        else:
            logger.error("No asset could be scored.")
            return None

    # ---------- Trading ----------
    async def start_trading(self):
        if not self._connected:
            logger.error("Cannot start trading: not connected")
            return
        if self._running:
            return
        if not self._ready:
            logger.warning("Bot not ready (assets not loaded)")
            return
        self._running = True
        self._task = asyncio.create_task(self._trade_loop())
        logger.info("Trading started")

    async def stop_trading(self):
        self._running = False
        if self._task:
            self._task.cancel()
        self._task = None
        logger.info("Trading stopped")

    async def _trade_loop(self):
        while self._running:
            try:
                logger.info("🔍 Polling assets to pick the best one...")
                asset_info = await self._pick_best_asset()
                if not asset_info:
                    logger.error("No assets available, waiting 10s...")
                    await asyncio.sleep(10)
                    continue

                self._current_asset = asset_info["symbol"]
                logger.info(f"🎯 Selected asset: {self._current_asset} (RSI={asset_info['rsi']:.2f}, EMA={asset_info['ema']:.5f}, price={asset_info['price']:.5f})")
                
                symbol = self._current_asset
                rsi, ema, closes = await self._fetch_asset_indicators(symbol)
                if ((rsi > 60.0) and (closes[-1] > ema)):
                    direction = "CALL"
                elif ((rsi < 40.0) and (closes[-1] < ema)):
                    direction = "PUT"

                # ----- PRECISE TIMER -----
                now = datetime.now(timezone.utc)
                # Seconds until next minute start
                seconds_until_next_minute = 60 - now.second
                # Adjust for microseconds to land exactly on the start of the minute
                if now.microsecond > 0:
                    seconds_until_next_minute -= 1
                wait_time = max(seconds_until_next_minute, 0.5)
                logger.info(f"⏳ Waiting {wait_time:.2f} seconds for next candle start...")
                await asyncio.sleep(wait_time)

                # ----- PLACE TRADE -----
                logger.info(f"💹 Placing trade: {direction} on {self._current_asset}")
                result = await self.client.place_trade(
                    self._current_asset, direction, self.stake, settings.trade_duration, check_win=True
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
                        "stake": self.stake,
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

                    logger.info(f"✅ Trade {trade['result']}: {direction} {self._current_asset} {self.stake:.2f} P&L: {trade['pnl']:.2f}")
                    await self.client.refresh_balance()
                    self.balance = self.client.balance
                    await asyncio.sleep(2)
                else:
                    logger.error("❌ Trade placement failed – no result object returned")
                    await asyncio.sleep(5)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"🔥 Trade loop error: {e}", exc_info=True)
                await asyncio.sleep(5)

    def reset_martingale(self):
        self.stake = settings.base_stake
        self.consecutive_losses = 0
        logger.info("🔄 Martingale reset")

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
            "candle": {},
            "connected": self._connected,
            "ready": self._ready,
            "running": self._running,
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
        self._recent_trades.clear()
        self._current_asset = None