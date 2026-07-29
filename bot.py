import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
from collections import deque

import yfinance as yf

from client import POClient
from config import settings

logger = logging.getLogger(__name__)

# ─── Dynamic yfinance symbol resolution ──────────────
# Special mappings for indices & commodities (where '=X' doesn't work)
INDEX_MAP = {
    "NAS100": "^IXIC",
    "US30": "^DJI",
    "SPX500": "^GSPC",
    "DAX40": "^GDAXI",
    "FTSE100": "^FTSE",
    "CAC40": "^FCHI",
    "JPN225": "^N225",
    "AUS200": "^AXJO",
    "EUSTX50": "^STOXX50E",
    "HSI50": "^HSI",
}
COMMODITY_MAP = {
    "XAUUSD": "GC=F",
    "XAGUSD": "SI=F",
    "USOIL": "CL=F",
    "UKOIL": "BZ=F",
    "BRENT": "BZ=F",
    "WTICOIL": "CL=F",
    "NGAS": "NG=F",
    "PLATINUM": "PL=F",
    "PALLADIUM": "PA=F",
}

def get_yfinance_symbol(po_asset: str) -> str:
    """Convert a Pocket Option OTC symbol to a Yahoo Finance symbol."""
    if po_asset.endswith("_otc"):
        base = po_asset[:-4]  # remove '_otc'
    else:
        base = po_asset

    # Check if it's a known index
    if base in INDEX_MAP:
        return INDEX_MAP[base]
    # Check if it's a known commodity
    if base in COMMODITY_MAP:
        return COMMODITY_MAP[base]

    # For forex: assume the base is a currency pair (like EURUSD, GBPUSD, etc.)
    # Yahoo Finance forex symbols end with =X
    # We'll assume any base that looks like a currency pair (6 letters + maybe digits)
    # and contains only letters (maybe with a slash? but PO uses no slash)
    # Simpler: if it's not a stock (like AAPL, TSLA), we treat as forex.
    # We can check if the base is in a set of forex pairs we know – we'll generate
    # that set from the allowed_assets list dynamically in the bot.
    # In this function we can just return base + "=X" for all non-index/commodity assets.
    # That will work for forex, but for stocks it would fail because stocks don't have =X.
    # So we need to differentiate. Since all assets are OTC, they are either forex, index, commodity, or stock.
    # We'll check the config list to see if this asset is forex (based on its symbol pattern).
    # But we don't have that context here. Instead, we can try a heuristic: if the base has 3 letters followed by 3 letters, it's likely forex.
    # We'll use that heuristic: length 6, all letters, and it's not in INDEX_MAP or COMMODITY_MAP -> forex.
    # For stocks, they are typically 1-5 letters (AAPL, TSLA, etc.) and we return as is.
    if base.isalpha() and len(base) == 6:
        # Could be forex (e.g., EURUSD) – add =X
        return base + "=X"
    # Default: return the base as is (works for stocks)
    return base


# ─── Trading bot ────────────────────────────────────────
class TradingBot:
    def __init__(self, client: POClient):
        self.client = client
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._current_asset: Optional[str] = None
        self._eligible_assets: list = []      # from PO, for payout info
        self._all_assets: Dict[str, Dict] = {}  # all PO assets with payouts
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

        # Candle data (from yfinance)
        self.current_candle: Dict = {}
        self.current_price: Optional[float] = None
        self._prev_candle: Optional[Dict] = None

        # Connection
        self._connected = False
        self._data_task: Optional[asyncio.Task] = None
        self._update_interval = 3

        # Prevent multiple trades on same candle (based on previous candle time)
        self._last_traded_candle_time: Optional[float] = None

        # Manual asset selection (None = auto)
        self._manual_asset: Optional[str] = None

        # yfinance update task
        self._yfinance_task: Optional[asyncio.Task] = None

        # All config assets (for dropdown)
        self._all_config_assets = settings.allowed_assets.copy()

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
        if self._yfinance_task:
            self._yfinance_task.cancel()
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
            logger.debug("Asset payouts refreshed")

    # ---------- Asset Management ----------
    async def _scan_assets(self):
        assets = await self.client.get_assets()
        self._all_assets = assets
        self._eligible_assets = []
        for symbol in self._all_config_assets:
            info = assets.get(symbol)
            if info is None:
                continue
            payout = info.get("payout", 0)
            if payout >= settings.min_payout:
                self._eligible_assets.append({"symbol": symbol, "payout": payout})
        self._eligible_assets.sort(key=lambda x: x["payout"], reverse=True)
        logger.debug(f"Eligible assets: {len(self._eligible_assets)}")

    def _auto_select_asset(self) -> Optional[str]:
        for a in self._eligible_assets:
            if a["symbol"] not in self._recent_trades:
                return a["symbol"]
        return self._eligible_assets[0]["symbol"] if self._eligible_assets else None

    async def _yfinance_update_loop(self):
        """Fetch latest candle and price from yfinance every second."""
        while self._connected and self._current_asset:
            try:
                yf_symbol = get_yfinance_symbol(self._current_asset)
                ticker = yf.Ticker(yf_symbol)
                hist = ticker.history(period="5d", interval="1m")
                if hist.empty:
                    logger.warning(f"No data from yfinance for {yf_symbol} (asset: {self._current_asset})")
                    await asyncio.sleep(1)
                    continue

                times = hist.index
                if len(times) >= 1:
                    latest = hist.iloc[-1]
                    prev = hist.iloc[-2] if len(times) >= 2 else None

                    self.current_candle = {
                        "open": latest["Open"],
                        "close": latest["Close"],
                        "high": latest["High"],
                        "low": latest["Low"],
                        "time": latest.name.timestamp()
                    }
                    self.current_price = latest["Close"]

                    if prev is not None:
                        self._prev_candle = {
                            "open": prev["Open"],
                            "close": prev["Close"],
                            "time": prev.name.timestamp()
                        }
                    else:
                        self._prev_candle = None

                await asyncio.sleep(1)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"yfinance update error: {e}")
                await asyncio.sleep(5)

    async def _switch_asset(self):
        if not self._eligible_assets and self._manual_asset is None:
            return

        if self._manual_asset is not None:
            if self._manual_asset in self._all_assets:
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

        self._current_asset = target
        self.current_candle = {}
        self.current_price = None
        self._prev_candle = None
        self._last_traded_candle_time = None

        if self._yfinance_task:
            self._yfinance_task.cancel()
        self._yfinance_task = asyncio.create_task(self._yfinance_update_loop())

        logger.info(f"Switched to asset: {self._current_asset} (yfinance symbol: {get_yfinance_symbol(target)})")

    async def set_manual_asset(self, asset: str) -> bool:
        if asset not in self._all_assets:
            logger.warning(f"Asset {asset} not found in PO asset list")
            return False
        self._manual_asset = asset
        await self._switch_asset()
        return True

    async def clear_manual_asset(self):
        self._manual_asset = None
        await self._switch_asset()

    def get_all_eur_usd_assets(self) -> List[Dict]:
        """Return assets from config that contain EUR or USD, with their current payout (if available)."""
        result = []
        for symbol in self._all_config_assets:
            if "EUR" in symbol or "USD" in symbol:
                info = self._all_assets.get(symbol, {})
                payout = info.get("payout", 0)
                result.append({"symbol": symbol, "payout": payout})
        result.sort(key=lambda x: x["payout"], reverse=True)
        return result

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
                now = datetime.now(timezone.utc)
                # Trade at the very beginning of the minute (second 0-1)
                if now.second < 2 and self._prev_candle is not None:
                    prev = self._prev_candle
                    direction = "CALL" if prev["close"] > prev["open"] else "PUT"
                    candle_time = prev["time"]
                    if self._last_traded_candle_time != candle_time:
                        logger.info(f"New minute: previous candle {direction} at {prev['close']}")
                        await self._execute_trade(direction)
                        self._last_traded_candle_time = candle_time
                await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Trade loop error: {e}")
                await asyncio.sleep(1)

    async def _execute_trade(self, direction: str):
        if not self._running:
            return
        stake = self.stake
        duration = settings.trade_duration

        logger.info(f"Trade signal: {direction} on {self._current_asset} at {self.current_price}")

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
        if self.current_candle:
            close = self.current_candle.get("close")
            open_ = self.current_candle.get("open")
            if close is not None and open_ is not None:
                return "GREEN" if close > open_ else "RED"
        return "N/A"

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
        if self._current_asset and self._current_asset in self._all_assets:
            current_payout = self._all_assets[self._current_asset].get("payout", 0)

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
            "current_price": self.current_price,
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
        self._prev_candle = None
        self._last_traded_candle_time = None
        self._recent_trades.clear()
        self._current_asset = None
        self._manual_asset = None

    def reset_martingale(self):
        self.stake = settings.base_stake
        self.consecutive_losses = 0