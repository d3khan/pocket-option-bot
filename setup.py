#!/usr/bin/env python3
"""
File generator for the simple Pocket Option bot.
Run this script from the project root (pocket-option-bot-simple) to create all files.
"""

import os
import stat

# ------------------------------------------------------------
# Project structure
# ------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PACKAGE_DIR = os.path.join(BASE_DIR, "pocket_bot_simple")
TEMPLATES_DIR = os.path.join(PACKAGE_DIR, "templates")
PARTIALS_DIR = os.path.join(TEMPLATES_DIR, "partials")
STATIC_DIR = os.path.join(PACKAGE_DIR, "static")
CSS_DIR = os.path.join(STATIC_DIR, "css")
JS_DIR = os.path.join(STATIC_DIR, "js")
DATA_DIR = os.path.join(BASE_DIR, "data")   # separate from package

DIRS = [
    PACKAGE_DIR,
    TEMPLATES_DIR,
    PARTIALS_DIR,
    STATIC_DIR,
    CSS_DIR,
    JS_DIR,
    DATA_DIR,
]

# ------------------------------------------------------------
# File content definitions
# ------------------------------------------------------------

# --- __init__.py ---
INIT_PY = """\"\"\"Pocket Bot Simple - a minimal trading bot for Pocket Option.\"\"\"
__version__ = "0.1.0"
"""

# --- config.py ---
CONFIG_PY = '''import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    ssid: str = os.getenv("PO_SSID", "your_ssid_here")
    base_stake: float = 1.0
    multiplier: float = 2.5
    max_stake: float = 16.0
    min_payout: int = 92
    trade_duration: int = 30
    max_consecutive_losses: int = 5
    max_daily_loss: float = 50.0
    username: str = "admin"
    password: str = "admin123"  # change in production
    secret_key: str = "super-secret-key-change-me"
    db_path: str = "data/bot.db"

settings = Settings()
'''

# --- client.py ---
CLIENT_PY = '''import asyncio
import logging
from typing import Optional, Dict, Any

from BinaryOptionsToolsV2 import PocketOptionAsync

logger = logging.getLogger(__name__)

class POClient:
    def __init__(self, ssid: str):
        self.ssid = ssid
        self._client: Optional[PocketOptionAsync] = None
        self._connected = False

    async def connect(self) -> bool:
        try:
            self._client = PocketOptionAsync(ssid=self.ssid)
            await self._client.connect()
            await self._client.wait_for_assets(timeout=60)
            self._connected = self._client.is_connected()
            return self._connected
        except Exception as e:
            logger.error(f"Connection error: {e}")
            return False

    async def disconnect(self):
        if self._client:
            await self._client.shutdown()
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected and self._client is not None and self._client.is_connected()

    async def get_balance(self) -> float:
        if not self.is_connected:
            return 0.0
        try:
            return float(await self._client.balance())
        except Exception as e:
            logger.error(f"Balance error: {e}")
            return 0.0

    async def get_assets(self) -> Dict[str, Any]:
        if not self.is_connected:
            return {}
        try:
            result = await self._client.active_assets()
            if isinstance(result, list):
                return {item.get("symbol", str(i)): item for i, item in enumerate(result)}
            return result
        except Exception as e:
            logger.error(f"Assets error: {e}")
            return {}

    async def get_payout(self, asset: str) -> int:
        if not self.is_connected:
            return 0
        try:
            return await self._client.payout(asset) or 0
        except Exception:
            return 0

    async def place_trade(self, asset: str, direction: str, amount: float, duration: int) -> Optional[str]:
        if not self.is_connected:
            return None
        direction = direction.lower()
        try:
            if direction == "call":
                trade_id, _ = await self._client.buy(asset, amount, duration, check_win=False)
            else:
                trade_id, _ = await self._client.sell(asset, amount, duration, check_win=False)
            return trade_id
        except Exception as e:
            logger.error(f"Trade error: {e}")
            return None

    async def check_trade_result(self, trade_id: str) -> Optional[Dict]:
        if not self.is_connected:
            return None
        try:
            return await self._client.check_win(trade_id)
        except Exception as e:
            logger.error(f"Check result error: {e}")
            return None

    async def get_candles(self, asset: str, period: int = 60, offset: int = 0):
        if not self.is_connected:
            return []
        try:
            return await self._client.get_candles(asset, period, offset)
        except Exception as e:
            logger.error(f"Candles error: {e}")
            return []

    async def subscribe_candles(self, asset: str, callback):
        """Subscribe to real‑time candles and call callback on each."""
        if not self.is_connected:
            return
        try:
            sub = await self._client.subscribe_symbol_time_aligned(asset, 60)
            async for candle in sub:
                await callback({**candle, "asset": asset})
        except Exception as e:
            logger.error(f"Subscription error: {e}")

    async def unsubscribe(self, asset: str):
        if self._client:
            await self._client.unsubscribe(asset)
'''

# --- bot.py ---
BOT_PY = '''import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from .client import POClient
from .config import settings

logger = logging.getLogger(__name__)

class TradingBot:
    def __init__(self, client: POClient):
        self.client = client
        self._running = False
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

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run())
        logger.info("Bot started")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("Bot stopped")

    async def _run(self):
        if not await self.client.connect():
            logger.error("Cannot start bot – connection failed")
            self._running = False
            return

        await self._scan_assets()
        if not self._eligible_assets:
            logger.error("No eligible assets found")
            self._running = False
            return

        await self._switch_asset()

        last_signal_time = None
        while self._running:
            try:
                self.balance = await self.client.get_balance()

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
                logger.error(f"Bot loop error: {e}")
                await asyncio.sleep(1)

        if self._current_asset:
            await self.client.unsubscribe(self._current_asset)

    async def _scan_assets(self):
        assets = await self.client.get_assets()
        self._eligible_assets = []
        for symbol, info in assets.items():
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
                        logger.warning("Stop condition reached, stopping bot")
                        await self.stop()

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
        }
'''

# --- main.py ---
MAIN_PY = '''import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starlette.middleware.base import BaseHTTPMiddleware

from .config import settings
from .client import POClient
from .bot import TradingBot

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create data dir
Path("data").mkdir(exist_ok=True)

client = POClient(settings.ssid)
bot = TradingBot(client)

app = FastAPI(title="Pocket Bot Simple")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters["currency"] = lambda v: f"${v:.2f}"

app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

security = HTTPBasic()

def verify_auth(credentials: HTTPBasicCredentials = Depends(security)):
    if credentials.username != settings.username or credentials.password != settings.password:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return True

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith(("/login", "/static")):
            return await call_next(request)
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return RedirectResponse("/login")
        return await call_next(request)

app.add_middleware(AuthMiddleware)

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})

@app.get("/partials/stats")
async def stats_partial(request: Request):
    stats = bot.get_stats()
    return templates.TemplateResponse("partials/stats.html", {"request": request, "stats": stats})

@app.get("/partials/candle")
async def candle_partial(request: Request):
    candle = bot.current_candle or {}
    return templates.TemplateResponse("partials/candle.html", {"request": request, "candle": candle})

@app.get("/partials/trades")
async def trades_partial(request: Request):
    trades = bot.trade_history[:20]
    return templates.TemplateResponse("partials/trades.html", {"request": request, "trades": trades})

@app.get("/partials/control")
async def control_partial(request: Request):
    return templates.TemplateResponse("partials/control.html", {
        "request": request,
        "running": bot._running,
        "connected": client.is_connected,
        "config": settings,
    })

@app.post("/api/start")
async def start_bot():
    if bot._running:
        return {"status": "already running"}
    await bot.start()
    return {"status": "started"}

@app.post("/api/stop")
async def stop_bot():
    if not bot._running:
        return {"status": "already stopped"}
    await bot.stop()
    return {"status": "stopped"}

@app.get("/api/status")
async def status():
    stats = bot.get_stats()
    stats["running"] = bot._running
    stats["connected"] = client.is_connected
    return stats

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await bot.stop()

app.router.lifespan_context = lifespan

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("pocket_bot_simple.main:app", host="0.0.0.0", port=8000, reload=True)
'''

# --- Templates ---
BASE_HTML = '''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Pocket Bot</title>
    <script src="/static/js/htmx.min.js"></script>
    <link rel="stylesheet" href="/static/css/theme.css">
</head>
<body>
    <div id="app">
        {% block content %}{% endblock %}
    </div>
</body>
</html>
'''

LOGIN_HTML = '''{% extends "base.html" %}
{% block content %}
<div class="login-container">
    <div class="login-box">
        <h1>Pocket Bot</h1>
        <form action="/login" method="post">
            <div class="form-group">
                <label>Username</label>
                <input type="text" name="username" required>
            </div>
            <div class="form-group">
                <label>Password</label>
                <input type="password" name="password" required>
            </div>
            <button type="submit">Log In</button>
        </form>
    </div>
</div>
{% endblock %}
'''

DASHBOARD_HTML = '''{% extends "base.html" %}
{% block content %}
<div class="dashboard">
    <header>
        <h1>Pocket Bot</h1>
        <span id="connection-status" hx-get="/partials/control" hx-trigger="every 5s" hx-swap="outerHTML">
            Checking...
        </span>
        <form action="/logout" method="post" style="display:inline;">
            <button type="submit" class="btn-danger">Logout</button>
        </form>
    </header>
    <main>
        <section id="stats" hx-get="/partials/stats" hx-trigger="load, every 5s" hx-swap="innerHTML">
            Loading stats...
        </section>
        <section id="candle" hx-get="/partials/candle" hx-trigger="load, every 3s" hx-swap="innerHTML">
            Loading candle...
        </section>
        <section id="control" hx-get="/partials/control" hx-trigger="load" hx-swap="innerHTML">
            Loading controls...
        </section>
        <section id="trades" hx-get="/partials/trades" hx-trigger="load, every 10s" hx-swap="innerHTML">
            Loading trades...
        </section>
    </main>
</div>
{% endblock %}
'''

# Partial templates
STATS_HTML = '''<div class="stats-grid">
    <div class="stat-card"><span>Balance</span><span>{{ stats.balance | currency }}</span></div>
    <div class="stat-card"><span>Session P&L</span><span>{{ stats.total_pnl | currency }}</span></div>
    <div class="stat-card"><span>Daily P&L</span><span>{{ stats.daily_pnl | currency }}</span></div>
    <div class="stat-card"><span>Win Rate</span><span>{{ stats.win_rate }}%</span></div>
    <div class="stat-card"><span>Current Stake</span><span>{{ stats.current_stake | currency }}</span></div>
    <div class="stat-card"><span>Consecutive Losses</span><span>{{ stats.consecutive_losses }}</span></div>
    <div class="stat-card"><span>Active Asset</span><span>{{ stats.current_asset or "None" }}</span></div>
</div>
'''

CANDLE_HTML = '''<div class="candle-display">
    <div class="candle-header">Forming Candle - {{ candle.asset or "N/A" }}</div>
    <div class="candle-values">
        <span>Open: {{ candle.open or 0.0 }}</span>
        <span>High: {{ candle.high or 0.0 }}</span>
        <span>Low: {{ candle.low or 0.0 }}</span>
        <span>Close: {{ candle.close or 0.0 }}</span>
    </div>
</div>
'''

TRADES_HTML = '''<table class="trade-log">
    <thead><tr><th>Time</th><th>Asset</th><th>Dir</th><th>Stake</th><th>Result</th><th>P&L</th></tr></thead>
    <tbody>
        {% for t in trades %}
        <tr>
            <td>{{ (t.open_time|string)[11:19] }}</td>
            <td>{{ t.asset }}</td>
            <td>{{ t.direction }}</td>
            <td>{{ t.stake | currency }}</td>
            <td>{{ t.result }}</td>
            <td>{{ t.pnl | currency if t.pnl is not none else "-" }}</td>
        </tr>
        {% endfor %}
    </tbody>
</table>
'''

CONTROL_HTML = '''<div class="control-panel">
    <div class="controls">
        <button hx-post="/api/start" hx-target="#control" hx-swap="outerHTML">Start Bot</button>
        <button hx-post="/api/stop" hx-target="#control" hx-swap="outerHTML">Stop Bot</button>
    </div>
    <div class="status">
        <span>Status: {{ "Running" if running else "Stopped" }}</span>
        <span>Connected: {{ "Yes" if connected else "No" }}</span>
    </div>
    <div class="config">
        <span>Base Stake: {{ config.base_stake }}</span>
        <span>Multiplier: {{ config.multiplier }}</span>
        <span>Max Stake: {{ config.max_stake }}</span>
        <span>Min Payout: {{ config.min_payout }}%</span>
    </div>
</div>
'''

THEME_CSS = '''body { font-family: sans-serif; margin: 20px; background: #f5f5f5; }
.dashboard { max-width: 1200px; margin: auto; }
header { display: flex; justify-content: space-between; align-items: center; }
.stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px,1fr)); gap: 10px; margin: 20px 0; }
.stat-card { background: white; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
.stat-card span { display: block; }
.stat-card span:first-child { font-size: 0.8em; color: #666; }
.stat-card span:last-child { font-size: 1.4em; font-weight: bold; }
.candle-display { background: white; padding: 15px; border-radius: 8px; margin: 20px 0; }
.candle-values span { margin-right: 20px; }
.control-panel { background: white; padding: 15px; border-radius: 8px; margin: 20px 0; }
.controls button { margin-right: 10px; padding: 8px 16px; }
table { width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; }
th, td { padding: 8px 12px; text-align: left; border-bottom: 1px solid #ddd; }
.btn-danger { background: #dc3545; color: white; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; }
.login-container { display: flex; justify-content: center; align-items: center; height: 100vh; }
.login-box { background: white; padding: 40px; border-radius: 8px; box-shadow: 0 4px 8px rgba(0,0,0,0.1); }
.login-box h1 { margin-top: 0; }
.form-group { margin-bottom: 15px; }
.form-group label { display: block; margin-bottom: 5px; }
.form-group input { width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px; }
.login-box button { width: 100%; padding: 10px; background: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; }
'''

# --- requirements.txt ---
REQUIREMENTS = '''fastapi
uvicorn
pydantic-settings
BinaryOptionsToolsV2
aiosqlite
'''

# --- README.md ---
README = '''# Pocket Bot Simple

A minimal trading bot for Pocket Option.

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
       Edit pocket_bot_simple/config.py to set your SSID and credentials.

    Place htmx.min.js in pocket_bot_simple/static/js/ (you can download it from https://unpkg.com/htmx.org).

    Run:
    bash

    python -m pocket_bot_simple.main

    Open http://localhost:8000 and log in.

Configuration

    SSID: set in config.py or via environment variable PO_SSID.

    Username/password: change in config.py (default admin/admin123).

Notes

    The bot uses a simple martingale‑style risk management.

    It switches to a new asset after each win.

    Trades are placed at the 30‑second mark of each minute candle.
    '''

FILES = [
(os.path.join(PACKAGE_DIR, "init.py"), INIT_PY),
(os.path.join(PACKAGE_DIR, "config.py"), CONFIG_PY),
(os.path.join(PACKAGE_DIR, "client.py"), CLIENT_PY),
(os.path.join(PACKAGE_DIR, "bot.py"), BOT_PY),
(os.path.join(PACKAGE_DIR, "main.py"), MAIN_PY),
(os.path.join(TEMPLATES_DIR, "base.html"), BASE_HTML),
(os.path.join(TEMPLATES_DIR, "login.html"), LOGIN_HTML),
(os.path.join(TEMPLATES_DIR, "dashboard.html"), DASHBOARD_HTML),
(os.path.join(PARTIALS_DIR, "stats.html"), STATS_HTML),
(os.path.join(PARTIALS_DIR, "candle.html"), CANDLE_HTML),
(os.path.join(PARTIALS_DIR, "trades.html"), TRADES_HTML),
(os.path.join(PARTIALS_DIR, "control.html"), CONTROL_HTML),
(os.path.join(CSS_DIR, "theme.css"), THEME_CSS),
(os.path.join(BASE_DIR, "requirements.txt"), REQUIREMENTS),
(os.path.join(BASE_DIR, "README.md"), README),
]

def create_dirs():
    for d in DIRS:
        os.makedirs(d, exist_ok=True)
        print(f"Created directory: {d}")

def write_files():
    for path, content in FILES:
        parent = os.path.dirname(path)
        os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
            print(f"Created file: {path}")

def make_executable():
    main_path = os.path.join(PACKAGE_DIR, "main.py")
    if os.path.exists(main_path):
        st = os.stat(main_path)
    os.chmod(main_path, st.st_mode | stat.S_IEXEC)

if True:
    print("Creating project structure for Pocket Bot Simple...")
    create_dirs()
    write_files()
    make_executable()
    print("\n✅ All files created successfully!")
    print("Now place htmx.min.js in pocket_bot_simple/static/js/")
    print("Then edit pocket_bot_simple/config.py with your SSID and credentials.")
    print("Run with: python -m pocket_bot_simple.main")