import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
from collections import deque

import yfinance as yf
import numpy as np
import pandas as pd

from client import POClient
from config import settings
from signals import generate_loose_signals

logger = logging.getLogger(__name__)

class TradingBot:
    def __init__(self, client: POClient):
        self.client = client
        self._running = False
        self._ready = False
        self._task: Optional[asyncio.Task] = None
        self._current_asset: Optional[str] = None
        self._eligible_assets: List[Dict[str, Any]] = []  # list of {"symbol": str, "payout": float}

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
        """Refresh asset list with symbol and payout (both OTC and non‑OTC)."""
        assets = await self.client.get_assets()
        self._eligible_assets = []
        for symbol, info in assets.items():
            if info.get("is_active") is False:
                continue
            # No OTC filter – include all active assets
            payout = info.get("payout", 0)
            self._eligible_assets.append({"symbol": symbol, "payout": payout})
        logger.info(f"Active assets (including OTC): {len(self._eligible_assets)}")

    # ---------- Yahoo Finance symbol mapping ----------
    def _map_symbol(self, symbol: str) -> str:
        base = symbol.replace("_otc", "").replace("-OTC", "")
        # Crypto
        crypto_list = [
            "BTC", "ETH", "LTC", "DOGE", "SOL", "ADA", "XRP", "BNB",
            "MATIC", "LINK", "DOT", "AVAX", "UNI", "ATOM", "SHIB",
            "TRX", "XLM", "VET", "THETA", "FIL", "ICP", "ETC", "AAVE",
            "MKR", "COMP", "ZEC", "XMR", "DASH", "NEO", "EOS", "XTZ"
        ]
        for crypto in crypto_list:
            if crypto in base:
                return base.replace("USD", "-USD")
        # Forex
        if len(base) == 6 and base.isalpha():
            return f"{base}=X"
        # Indices
        index_map = {
            "SPX500": "^GSPC", "NAS100": "^IXIC", "US30": "^DJI",
            "DAX40": "^GDAXI", "FTSE100": "^FTSE", "CAC40": "^FCHI",
            "JPN225": "^N225", "AUS200": "^AXJO", "EUSTX50": "^STOXX50E",
            "HSI50": "^HSI", "VIX": "^VIX", "RUT": "^RUT", "NDX": "^NDX"
        }
        for po_symbol, yf_symbol in index_map.items():
            if po_symbol in base:
                return yf_symbol
        # Commodities
        commodity_map = {
            "XAUUSD": "GC=F", "XAGUSD": "SI=F", "USOIL": "CL=F",
            "UKOIL": "BZ=F", "NGAS": "NG=F", "PLATINUM": "PL=F",
            "PALLADIUM": "PA=F", "COPPER": "HG=F"
        }
        for po_symbol, yf_symbol in commodity_map.items():
            if po_symbol in base:
                return yf_symbol
        # Stocks / ETFs
        return base.split(" ")[0].split("/")[0]

    # ---------- Simplified DataFrame sanitizer ----------
    def _sanitize_df(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.columns = [c.lower() for c in df.columns]
        for col in ['open', 'high', 'low', 'close']:
            if col not in df.columns:
                raise ValueError(f"Missing required column: {col}")
        return df

    # ---------- Signal fetch (1m only) ----------
    async def _fetch_signal_for_asset(self, symbol: str) -> Optional[Dict]:
        yf_symbol = self._map_symbol(symbol)
        try:
            df_1m = yf.download(
                yf_symbol,
                period="2d",
                interval="1m",
                progress=False,
                auto_adjust=False,
                multi_level_index=False
            )
            if df_1m.empty or len(df_1m) < 30:
                logger.debug(f"Not enough data for {symbol}")
                return None
            df_1m = self._sanitize_df(df_1m)
            signal_info = generate_loose_signals(df_1m)
            if signal_info["signal"] == "NONE":
                return None
            signal_info["symbol"] = symbol
            return signal_info
        except Exception as e:
            logger.warning(f"Signal error for {symbol}: {e}")
            return None

    # ---------- Asset Selection (refresh + combined score) ----------
    async def _pick_best_asset(self) -> Optional[Dict]:
        # Refresh asset list before each scan
        await self._scan_assets()
        if not self._eligible_assets:
            return None

        candidates = []
        for asset in self._eligible_assets:
            symbol = asset["symbol"]
            signal_info = await self._fetch_signal_for_asset(symbol)
            if signal_info:
                # Compute combined score: payout * strength
                payout = asset["payout"]
                strength = signal_info.get("strength", 0)
                score = payout * strength
                candidates.append({
                    "symbol": symbol,
                    "payout": payout,
                    "strength": strength,
                    "score": score,
                    "signal": signal_info["signal"],
                    "info": signal_info,
                })
            await asyncio.sleep(0.1)

        if not candidates:
            logger.info("No valid signals found.")
            return None

        # Pick the candidate with the highest combined score
        best = max(candidates, key=lambda x: x["score"])
        logger.info(
            f"🏆 Best asset: {best['symbol']} (payout: {best['payout']}%, "
            f"strength: {best['strength']:.3f}, score: {best['score']:.3f}) "
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
                direction = signal_info["signal"]  # "CALL" or "PUT"

                logger.info(f"🎯 Selected asset: {self._current_asset} ({direction})")

                # Wait for next minute start
                now = datetime.now(timezone.utc)
                seconds_to_next_minute = 60 - now.second
                if now.microsecond > 0:
                    seconds_to_next_minute -= 1
                wait_time = max(seconds_to_next_minute, 0.5)
                logger.info(f"⏳ Waiting {wait_time:.2f}s for next candle start...")
                await asyncio.sleep(wait_time)

                logger.info(f"💹 Placing trade: {direction} on {self._current_asset} for {settings.trade_duration}s with ${self.stake}")
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
        self._current_asset = None