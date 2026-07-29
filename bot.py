import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List

import numpy as np
import pandas as pd

from client import POClient
from candle_client import CandleClient
from config import settings
from signals import signal

logger = logging.getLogger(__name__)


class TradingBot:
    def __init__(self, client: POClient, candle_client: CandleClient):
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

    # ---------- Signal fetch (reverse-engineered candle API + TA-Lib) ----------
    async def _fetch_signal_for_asset(self, symbol: str) -> Optional[Dict[str, Any]]:
        try:
            df = await self.candle_client.get_candles(symbol, settings.candle_period)
            if df.empty or len(df) < 30:
                logger.debug(f"Not enough candle data for {symbol} ({len(df)} rows)")
                return None

            required = {"open", "high", "low", "close"}
            missing = required - set(df.columns)
            if missing:
                logger.debug(f"{symbol} missing columns: {missing}")
                return None

            signal_info = signal(df)
            if signal_info["signal"] == "NONE":
                return None

            signal_info["symbol"] = symbol
            return signal_info

        except Exception as e:
            logger.warning(f"Signal error for {symbol}: {e}")
            return None

    # ---------- Asset Selection (EMA20 + RSI14 scoring) ----------
    async def _pick_best_asset(self) -> Optional[Dict[str, Any]]:
        await self._scan_assets()
        if not self._eligible_assets:
            logger.info("No eligible assets found.")
            return None

        candidates = []
        for asset in self._eligible_assets:
            symbol = asset["symbol"]
            signal_info = await self._fetch_signal_for_asset(symbol)
            if signal_info:
                score = self._calculate_score(signal_info)
                candidates.append({
                    "symbol": symbol,
                    "payout": asset["payout"],
                    "score": score,
                    "signal": signal_info["signal"],
                    "info": signal_info,
                })
            await asyncio.sleep(0.05)

        if not candidates:
            logger.info("No valid signals found.")
            return None

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
        """Block until the exact :00.000 second of the next minute candle."""
        now = datetime.now(timezone.utc)
        # Target: next minute boundary, seconds=0, microseconds=0
        target = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
        wait_seconds = (target - now).total_seconds()

        if wait_seconds > 0:
            logger.info(
                f"⏳ Waiting {wait_seconds:.3f}s until exact candle open "
                f"({target.strftime('%H:%M:%S')})..."
            )
            # Sleep the bulk, then tight-loop the last 100 ms for precision
            if wait_seconds > 0.15:
                await asyncio.sleep(wait_seconds - 0.1)

            # Fine-tune: spin-yield until we hit the boundary
            while datetime.now(timezone.utc) < target:
                await asyncio.sleep(0)  # yield but return immediately

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

                # Log the signal
                logger.info(
                    f"🎯 SIGNAL: {direction} on {self._current_asset} | "
                    f"Close: {close:.5f} | EMA20: {ema20:.5f} | RSI14: {rsi14:.2f}"
                )

                # Wait until the exact start of the next minute candle (:00.000)
                await self._wait_for_next_candle()

                # Place trade exactly at candle open
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

                # Log trade placement with indicator values
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
