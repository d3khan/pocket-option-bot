import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, List
from collections import deque

from BinaryOptionsToolsV2 import PocketOptionAsync

from config import settings
from client import POClient

logger = logging.getLogger(__name__)


class ScoreManager:
    """Tracks scores for all assets and picks the top ones."""
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

    def get_top_n(self, n: int) -> List[str]:
        sorted_scores = sorted(self.scores.items(), key=lambda x: x[1], reverse=True)
        return [asset for asset, _ in sorted_scores[:n]]


class TradingBot:
    def __init__(self, client: POClient):
        self.client = client
        self._running = False
        self._subscribed_assets: List[str] = []           # currently active 4 assets
        self._all_assets: List[str] = []                  # all eligible OTC assets
        self._asset_data: Dict[str, Dict] = {}            # indicators for all assets
        self._score_manager = ScoreManager()
        self._subscription_tasks: List[asyncio.Task] = []
        self._trade_task: Optional[asyncio.Task] = None
        self._rebalance_task: Optional[asyncio.Task] = None
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

    # ---------- Connection & Asset Scanning ----------
    async def connect(self) -> bool:
        if await self.client.connect():
            await self._scan_assets()
            # Pre‑fetch 20 candles for all assets and calculate initial scores
            await self._fetch_initial_data()
            # Subscribe to the top 4 assets
            await self._subscribe_top_assets()
            # Start rebalance loop
            self._rebalance_task = asyncio.create_task(self._rebalance_loop())
            return True
        return False

    async def disconnect(self):
        self._running = False
        for task in self._subscription_tasks:
            task.cancel()
        if self._trade_task:
            self._trade_task.cancel()
        if self._rebalance_task:
            self._rebalance_task.cancel()
        for asset in self._subscribed_assets:
            await self.client.unsubscribe(asset)
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
        self._all_assets = [sym for sym, _ in eligible[:30]]  # keep top 30 for pool
        logger.info(f"Found {len(self._all_assets)} OTC assets with payout >= {settings.min_payout}%")

    async def _fetch_initial_data(self):
        """Fetch 20 candles for all assets and compute indicators."""
        for asset in self._all_assets:
            try:
                candles = await self.client.get_candles(asset, period=60, offset=1200)  # 20 candles
                if len(candles) >= 20:
                    closes = [c['close'] for c in candles[-20:]]
                    self._asset_data[asset] = {
                        'prices': deque(closes, maxlen=40),
                        'ema': None,
                        'rsi': None,
                        'last_update': datetime.now(timezone.utc),
                    }
                    self._update_indicators(asset, self._asset_data[asset])
                    score = self._compute_score(asset, self._asset_data[asset])
                    await self._score_manager.update_score(asset, score)
                    logger.info(f"Initialized {asset} – score {score:.2f}")
                else:
                    logger.warning(f"Only {len(candles)} candles for {asset}, skipping")
            except Exception as e:
                logger.error(f"Failed to fetch data for {asset}: {e}")

    async def _subscribe_top_assets(self):
        """Subscribe to the top 4 assets by score."""
        top_assets = self._score_manager.get_top_n(4)
        if not top_assets:
            logger.error("No assets with scores, cannot subscribe")
            return
        self._subscribed_assets = top_assets
        for asset in top_assets:
            task = asyncio.create_task(self._subscribe_asset(asset))
            self._subscription_tasks.append(task)
        logger.info(f"Subscribed to top 4 assets: {top_assets}")

    async def _subscribe_asset(self, asset: str):
        """Subscribe to a single asset and process its candles."""
        try:
            sub = await self.client._client.subscribe_symbol_time_aligned(asset, timedelta(seconds=60))
            async for candle in sub:
                if not self._running:
                    break
                data = self._asset_data.get(asset)
                if data:
                    data['prices'].append(candle['close'])
                    self._update_indicators(asset, data)
                    score = self._compute_score(asset, data)
                    await self._score_manager.update_score(asset, score)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Subscription error for {asset}: {e}")

    # ---------- Rebalance Logic ----------
    async def _rebalance_loop(self):
        """Periodically check if we should replace an active asset."""
        while self._running:
            await asyncio.sleep(60)  # check every minute
            if not self._running:
                break
            # Get current scores for all assets
            all_scores = {asset: self._score_manager.scores.get(asset, 0) for asset in self._all_assets}
            # Get top 4 overall
            top_assets = sorted(all_scores, key=all_scores.get, reverse=True)[:4]
            # Compare with currently subscribed assets
            current_set = set(self._subscribed_assets)
            new_set = set(top_assets)
            if current_set != new_set:
                to_add = new_set - current_set
                to_remove = current_set - new_set
                # Unsubscribe removed assets
                for asset in to_remove:
                    if asset in self._subscribed_assets:
                        self._subscribed_assets.remove(asset)
                        await self.client.unsubscribe(asset)
                        logger.info(f"Unsubscribed {asset} – dropped from top 4")
                # Subscribe to new assets
                for asset in to_add:
                    if asset not in self._subscribed_assets:
                        self._subscribed_assets.append(asset)
                        task = asyncio.create_task(self._subscribe_asset(asset))
                        self._subscription_tasks.append(task)
                        logger.info(f"Subscribed {asset} – entered top 4")
                # Update the score manager's best asset for UI
                self._score_manager.best_asset = top_assets[0] if top_assets else None

    # ---------- Indicator Helpers ----------
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

    # ---------- Trading ----------
    async def start_trading(self):
        if self._running:
            return
        if not self._subscribed_assets:
            logger.error("No assets subscribed, cannot trade")
            return
        self._running = True
        self._trade_task = asyncio.create_task(self._trade_loop())
        logger.info("▶️ Trading started")

    async def stop_trading(self):
        self._running = False
        if self._trade_task:
            self._trade_task.cancel()
        logger.info("⏹️ Trading stopped")

    async def _trade_loop(self):
        while self._running:
            try:
                now = datetime.now(timezone.utc)
                if now.second == 0 and self._trade_counter % 2 == 0:
                    best = self._score_manager.get_best()
                    if best and best in self._subscribed_assets:
                        data = self._asset_data.get(best)
                        if data and data.get('ema') is not None:
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