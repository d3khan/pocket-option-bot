import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, List
from collections import deque

from BinaryOptionsToolsV2 import PocketOptionAsync

from config import settings
from client import POClient

logger = logging.getLogger(__name__)


# ---------- Shared score manager ----------
class ScoreManager:
    def __init__(self):
        self.scores: Dict[str, float] = {}
        self.best_asset: Optional[str] = None
        self.lock = asyncio.Lock()

    async def update_score(self, asset: str, score: float):
        async with self.lock:
            self.scores[asset] = score
            if self.scores:
                self.best_asset = max(self.scores, key=self.scores.get)

    def get_best(self) -> Optional[str]:
        return self.best_asset


# ---------- Worker that subscribes to a subset of assets ----------
class AssetWorker:
    def __init__(self, ssid: str, assets: List[str], score_manager: ScoreManager):
        self.ssid = ssid
        self.assets = assets
        self.score_manager = score_manager
        self.running = False
        self.client: Optional[PocketOptionAsync] = None
        self.task: Optional[asyncio.Task] = None
        self.asset_data: Dict[str, Dict] = {}

    async def start(self):
        """Start the worker – create client, fetch history, and subscribe."""
        self.running = True
        self.client = PocketOptionAsync(ssid=self.ssid)
        await self.client.connect()
        await self.client.wait_for_assets(timeout=60)

        # Initialize data for each asset and pre-fetch 40 candles
        for asset in self.assets:
            self.asset_data[asset] = {
                'prices': deque(maxlen=40),
                'ema': None,
                'rsi': None,
            }
            try:
                candles = await self.client.get_candles(asset, period=60, offset=0)
                if len(candles) >= 40:
                    closes = [c['close'] for c in candles[-40:]]
                    self.asset_data[asset]['prices'] = deque(closes, maxlen=40)
                    self._update_indicators(asset, self.asset_data[asset])
                    score = self._compute_score(asset, self.asset_data[asset])
                    await self.score_manager.update_score(asset, score)
                    logger.debug(f"Prefetched 40 candles for {asset}")
                else:
                    logger.warning(f"Only {len(candles)} candles for {asset}, need 40")
            except Exception as e:
                logger.error(f"Failed to fetch history for {asset}: {e}")

        self.task = asyncio.create_task(self._run())
        logger.info(f"Worker started for {len(self.assets)} assets: {self.assets}")

    async def _run(self):
        try:
            tasks = []
            for asset in self.assets:
                tasks.append(self._subscribe_asset(asset))
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info(f"Worker cancelled for assets: {self.assets}")
        except Exception as e:
            logger.error(f"Worker error: {e}", exc_info=True)
        finally:
            self.running = False
            if self.client:
                try:
                    await self.client.shutdown()
                except Exception:
                    pass

    async def _subscribe_asset(self, asset: str):
        try:
            sub = await self.client.subscribe_symbol_time_aligned(asset, timedelta(seconds=60))
            async for candle in sub:
                if not self.running:
                    break
                data = self.asset_data[asset]
                data['prices'].append(candle['close'])
                self._update_indicators(asset, data)
                score = self._compute_score(asset, data)
                await self.score_manager.update_score(asset, score)
        except asyncio.CancelledError:
            logger.debug(f"Subscription cancelled for {asset}")
        except Exception as e:
            logger.error(f"Subscription error for {asset}: {e}")

    def _update_indicators(self, asset, data):
        closes = list(data['prices'])
        if len(closes) >= settings.ema_period:
            data['ema'] = self._compute_ema(closes, settings.ema_period)
        if len(closes) >= settings.rsi_period + 1:
            data['rsi'] = self._compute_rsi(closes, settings.rsi_period)

    def _compute_ema(self, prices, period):
        if len(prices) < period:
            return prices[-1]
        k = 2.0 / (period + 1)
        ema = sum(prices[:period]) / period
        for price in prices[period:]:
            ema = price * k + ema * (1 - k)
        return ema

    def _compute_rsi(self, prices, period):
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

    def _compute_score(self, asset, data):
        if data.get('ema') is None or data.get('rsi') is None:
            return 0.0
        price = data['prices'][-1]
        ema = data['ema']
        rsi = data['rsi']
        price_diff = abs(price - ema) / (ema or 1)
        rsi_strength = 0.0
        if rsi > settings.rsi_overbought:
            rsi_strength = (rsi - settings.rsi_overbought) / (100 - settings.rsi_overbought)
        elif rsi < settings.rsi_oversold:
            rsi_strength = (settings.rsi_oversold - rsi) / settings.rsi_oversold
        return price_diff * 100 + rsi_strength * 50

    async def stop(self):
        self.running = False
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        if self.client:
            try:
                await self.client.shutdown()
            except Exception:
                pass


# ---------- TradingBot that manages multiple workers ----------
class TradingBot:
    def __init__(self, client: POClient):
        self.client = client
        self._running = False
        self._trade_task: Optional[asyncio.Task] = None
        self._refresh_task: Optional[asyncio.Task] = None
        self._score_manager = ScoreManager()
        self._workers: List[AssetWorker] = []
        self._all_assets: List[str] = []
        self._selected_assets: List[str] = []
        self._trade_counter = 0

        # Risk / Martingale
        self.stake = settings.base_stake
        self.consecutive_losses = 0
        self.daily_pnl = 0.0
        self.total_pnl = 0.0
        self.wins = 0
        self.losses = 0
        self.trade_history = []
        self._last_day = datetime.now(timezone.utc).date()
        self.balance = 0.0

    async def connect(self) -> bool:
        if await self.client.connect():
            await self._scan_assets()
            return True
        return False

    async def disconnect(self):
        await self.stop_trading()
        await self.client.disconnect()

    async def _scan_assets(self):
        assets = await self.client.get_assets()
        eligible = []
        for symbol, info in assets.items():
            if info.get("is_otc") is not True:
                continue
            if info.get("is_active") is False:
                continue
            payout = info.get("payout", 0)
            if payout >= settings.min_payout:
                eligible.append((symbol, payout))
        eligible.sort(key=lambda x: x[1], reverse=True)
        self._all_assets = [sym for sym, _ in eligible]
        logger.info(f"Found {len(self._all_assets)} OTC assets with payout >= {settings.min_payout}%")
        return self._all_assets

    async def _refresh_assets_loop(self):
        while self._running:
            await asyncio.sleep(60)
            old_assets = set(self._selected_assets)
            await self._scan_assets()
            new_assets = self._all_assets[:len(self._selected_assets)]
            if set(new_assets) != old_assets:
                logger.info("Asset list changed, rebalancing workers...")
                await self._rebalance_workers()

    async def _rebalance_workers(self):
        await self._stop_workers()
        await self._start_workers()

    async def _stop_workers(self):
        for worker in self._workers:
            await worker.stop()
        self._workers.clear()

    async def _start_workers(self):
        if not self._all_assets:
            await self._scan_assets()
        if not self._all_assets:
            logger.error("No OTC assets available.")
            return False

        max_per_worker = 4
        num_workers = (len(self._all_assets) + max_per_worker - 1) // max_per_worker
        num_workers = min(num_workers, 20)
        if num_workers == 0:
            num_workers = 1

        assigned = []
        for i in range(num_workers):
            start = i * max_per_worker
            end = min(start + max_per_worker, len(self._all_assets))
            assets = self._all_assets[start:end]
            if assets:
                assigned.append(assets)

        self._selected_assets = [a for sublist in assigned for a in sublist]

        for assets in assigned:
            try:
                worker = AssetWorker(settings.ssid, assets, self._score_manager)
                await worker.start()
                self._workers.append(worker)
                logger.info(f"Worker started with {len(assets)} assets: {assets}")
            except Exception as e:
                logger.error(f"Failed to start worker: {e}")
                return False

        logger.info(f"Started {len(self._workers)} workers monitoring {len(self._selected_assets)} assets")
        return True

    async def start_trading(self):
        logger.info("🟢 start_trading() called")
        if self._running:
            logger.warning("Trading already running")
            return

        if not self._all_assets:
            logger.info("No assets loaded, scanning...")
            await self._scan_assets()
        if not self._all_assets:
            logger.error("No assets available, cannot start")
            return

        logger.info("Starting workers...")
        success = await self._start_workers()
        if not success or not self._workers:
            logger.error("No workers started, cannot trade")
            self._running = False
            return

        self._running = True
        self._refresh_task = asyncio.create_task(self._refresh_assets_loop())
        self._trade_task = asyncio.create_task(self._trade_loop())
        logger.info("▶️ Trading started successfully")

    async def stop_trading(self):
        self._running = False
        if self._trade_task and not self._trade_task.done():
            self._trade_task.cancel()
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
        await self._stop_workers()
        logger.info("⏹️ Trading stopped")

    async def _trade_loop(self):
        while self._running:
            try:
                now = datetime.now(timezone.utc)
                if now.second == 0:
                    if self._trade_counter % 2 == 0:
                        best = self._score_manager.get_best()
                        if best:
                            for worker in self._workers:
                                if best in worker.asset_data:
                                    data = worker.asset_data[best]
                                    if data.get('ema') is not None and data.get('rsi') is not None:
                                        price = data['prices'][-1]
                                        ema = data['ema']
                                        rsi = data['rsi']
                                        if price > ema and rsi > settings.rsi_overbought:
                                            direction = "call"
                                        elif price < ema and rsi < settings.rsi_oversold:
                                            direction = "put"
                                        else:
                                            direction = None
                                        if direction:
                                            logger.info(f"🚀 Trading {best} {direction} at :00")
                                            await self._execute_trade(best, direction)
                                    break
                    self._trade_counter += 1
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Trade loop error: {e}", exc_info=True)
                await asyncio.sleep(1)

    async def _execute_trade(self, asset, direction):
        try:
            stake = self.stake
            duration = settings.trade_duration
            logger.info(f"💼 Placing trade: {direction} {asset} with stake {stake}")

            client = self.client._client
            if direction == "call":
                trade_id, result = await client.buy(asset, stake, duration, check_win=True)
            else:
                trade_id, result = await client.sell(asset, stake, duration, check_win=True)
            win = result["result"] == "win"
            profit = result["profit"] if win else -abs(result["profit"])
            self.total_pnl += profit
            self.daily_pnl += profit
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
            trade = {
                "id": trade_id,
                "asset": asset,
                "direction": direction,
                "stake": stake,
                "open_time": datetime.now(timezone.utc).isoformat(),
                "result": "WIN" if win else "LOSS",
                "pnl": profit,
            }
            self.trade_history.insert(0, trade)
            if len(self.trade_history) > 100:
                self.trade_history.pop()
            logger.info(f"✅ Trade {trade['result']}: {direction} {asset} {stake:.2f} P&L: {profit:.2f}")
            await self.client.refresh_balance()
            self.balance = self.client.balance
        except Exception as e:
            logger.error(f"❌ Trade execution error: {e}", exc_info=True)

    def reset_martingale(self):
        self.stake = settings.base_stake
        self.consecutive_losses = 0

    def get_stats(self):
        total = self.wins + self.losses
        win_rate = (self.wins / total * 100) if total > 0 else 0.0
        return {
            "balance": self.client.balance if self.client else 0.0,
            "total_pnl": self.total_pnl,
            "daily_pnl": self.daily_pnl,
            "win_rate": round(win_rate, 2),
            "consecutive_wins": 0,
            "consecutive_losses": self.consecutive_losses,
            "current_stake": self.stake,
            "current_asset": self._score_manager.get_best(),
            "trades": self.trade_history[:20],
            "candle": {},
            "connected": self.client.is_connected if self.client else False,
            "running": self._running,
        }

    def reset_stats(self):
        self.stake = settings.base_stake
        self.consecutive_losses = 0
        self.daily_pnl = 0.0
        self.total_pnl = 0.0
        self.wins = 0
        self.losses = 0
        self.trade_history = []
        self._trade_counter = 0