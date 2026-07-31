import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List

import numpy as np

from client import POClient
from multi_candle_client import MultiCandleClient
from config import settings

logger = logging.getLogger(__name__)

# Milliseconds before :00 to fire the trade request.
# PocketOption usually accepts trades placed a few hundred ms before the
# candle open and assigns them to the new candle.  If your trades are
# still landing 1 s late, increase this (e.g. 400).  If they are being
# rejected as "too early", decrease it (e.g. 100).
FIRE_EARLY_MS = 1000


class TradingBot:
    def __init__(self, client: POClient, candle_client: MultiCandleClient):
        self.client = client
        self.candle_client = candle_client
        self._running = False
        self._ready = False
        self._task: Optional[asyncio.Task] = None
        self._current_asset: Optional[str] = None
        self._eligible_assets: List[Dict[str, Any]] = []

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
        self.trade_history: List[Dict[str, Any]] = []

        # Connection
        self._connected = False
        self._data_task: Optional[asyncio.Task] = None
        self._update_interval = 0.5

    # ---------- Connection ----------
    async def connect(self) -> bool:
        if self._connected:
            return True

        if not await self.client.connect():
            return False

        if not await self.candle_client.connect():
            logger.error("Candle client failed to connect")
            await self.client.disconnect()
            return False

        self._connected = True
        self._data_task = asyncio.create_task(self._data_update_loop())
        await self._scan_assets()
        self._ready = True
        logger.info("Connected and asset list loaded")
        return True

    async def disconnect(self):
        self._connected = False
        self._running = False
        self._ready = False
        if self._task:
            self._task.cancel()
        if self._data_task:
            self._data_task.cancel()
        await self.client.disconnect()
        await self.candle_client.disconnect()
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
        """Refresh asset list. Includes BOTH OTC and non-OTC.
        Filters for payout >= min_payout (default 92%).
        """
        assets = await self.client.get_assets()
        payouts = await self.client.get_all_payouts()
        self._eligible_assets = []

        for symbol, info in assets.items():
            if info.get("is_active") is False:
                continue
            payout = payouts.get(symbol, info.get("payout", 0))
            if payout >= settings.min_payout:
                self._eligible_assets.append({"symbol": symbol, "payout": payout})

        logger.info(
            f"Active assets (OTC + non-OTC) with >= {settings.min_payout}% payout: "
            f"{len(self._eligible_assets)}"
        )

    # ---------- Signal Scoring ----------
    def _calculate_score(self, signal_info: Dict[str, Any]) -> float:
        """Score based on EMA20 and RSI14 strength.

        Higher score = stronger signal.
        RSI distance from 50  +  price deviation from EMA20 (scaled).
        """
        rsi = signal_info.get("rsi", 50.0)
        close = signal_info.get("close", 0.0)
        ema20 = signal_info.get("ema20", close)

        if not ema20 or ema20 == 0 or np.isnan(ema20):
            return 0.0

        rsi_strength = abs(rsi - 50.0)
        ema_strength = abs(close - ema20) / ema20 * 1000.0
        return rsi_strength + ema_strength

    # ---------- Asset Selection (parallel EMA20 + RSI14 scoring) ----------
    async def _pick_best_asset(self) -> Optional[Dict[str, Any]]:
        await self._scan_assets()
        if not self._eligible_assets:
            logger.info("No eligible assets found.")
            return None

        symbols = [a["symbol"] for a in self._eligible_assets]
        all_signals = await self.candle_client.fetch_signals(
            symbols, settings.candle_period
        )

        if not all_signals:
            logger.info("No valid signals found.")
            return None

        candidates = []
        for symbol, sig in all_signals.items():
            payout = next(
                (a["payout"] for a in self._eligible_assets if a["symbol"] == symbol), 0
            )
            score = self._calculate_score(sig)
            candidates.append({
                "symbol": symbol,
                "payout": payout,
                "score": score,
                "signal": sig["signal"],
                "info": sig,
            })

        best = max(candidates, key=lambda x: x["score"])
        logger.info(
            f"🏆 Best asset: {best['symbol']} "
            f"(score: {best['score']:.2f}, payout: {best['payout']}%) "
            f"signal: {best['signal']}"
        )
        return best["info"]

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

    async def _wait_for_next_candle(self):
        """Block until ~FIRE_EARLY_MS before the next :00 boundary,
        then busy-wait with 1 ms sleeps for sub-10 ms precision.
        """
        now = datetime.now(timezone.utc)
        target = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
        fire_at = target - timedelta(milliseconds=FIRE_EARLY_MS)
        wait_seconds = (fire_at - now).total_seconds()

        if wait_seconds > 0:
            logger.info(
                f"⏳ Waiting {wait_seconds:.3f}s until candle open "
                f"({target.strftime('%H:%M:%S')})..."
            )
            # Async sleep the bulk, leaving 300 ms for precision approach
            if wait_seconds > 0.35:
                await asyncio.sleep(wait_seconds - 0.3)

            # Final 300 ms: blocking 1 ms sleeps for high precision.
            # This briefly blocks the event loop but guarantees sub-10 ms accuracy.
            while True:
                remaining = (fire_at - datetime.now(timezone.utc)).total_seconds()
                if remaining <= 0:
                    break
                time.sleep(0.001)

        now = datetime.now(timezone.utc)
        logger.info(f"🕐 Candle open reached: {now.strftime('%H:%M:%S.%f')[:-3]}")

    async def _trade_loop(self):
        while self._running:
            try:
                logger.info("🔍 Scanning for signals...")
                signal_info = await self._pick_best_asset()
                if not signal_info:
                    logger.warning("No signal, waiting 10s...")
                    await asyncio.sleep(10)
                    continue

                self._current_asset = signal_info["symbol"]
                direction = signal_info["signal"]
                ema20 = signal_info.get("ema20", 0.0)
                rsi14 = signal_info.get("rsi", 0.0)
                close = signal_info.get("close", 0.0)

                logger.info(
                    f"🎯 SIGNAL: {direction} on {self._current_asset} | "
                    f"Close: {close:.5f} | EMA20: {ema20:.5f} | RSI14: {rsi14:.2f}"
                )

                await self._wait_for_next_candle()

                logger.info(
                    f"💹 PLACING TRADE: {direction} on {self._current_asset} "
                    f"for {settings.trade_duration}s with ${self.stake}"
                )
                result = await self.client.place_trade(
                    self._current_asset,
                    direction,
                    self.stake,
                    settings.trade_duration,
                    check_win=True,
                )

                logger.info(
                    f"📊 TRADE PLACED: {direction} {self._current_asset} | "
                    f"Stake: ${self.stake:.2f} | EMA20: {ema20:.5f} | RSI14: {rsi14:.2f}"
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

                    logger.info(
                        f"✅ RESULT: {trade['result']} | "
                        f"{direction} {self._current_asset} ${self.stake:.2f} | "
                        f"P&L: {trade['pnl']:.2f}"
                    )
                    await self.client.refresh_balance()
                    self.balance = self.client.balance
                else:
                    logger.error("❌ Trade placement failed – no result returned")
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

    def get_stats(self) -> Dict[str, Any]:
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
        self._current_asset = None
