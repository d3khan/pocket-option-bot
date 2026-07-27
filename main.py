import asyncio
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from jinja2 import Environment, FileSystemLoader

from config import settings
from client import POClient
from bot import TradingBot

# Suppress uvicorn access logs
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("uvicorn.error").setLevel(logging.INFO)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create data dir
Path("data").mkdir(exist_ok=True)

# Globals
client = POClient(settings.ssid)
bot = TradingBot(client)

# Connection status tracking
connection_status = "disconnected"  # disconnected, connecting, connected, failed
connection_error = None

app = FastAPI(title="Pocket Bot Simple")

# ---------- Direct Jinja2 Environment ----------
TEMPLATES_DIR = str(Path(__file__).parent / "templates")
jinja_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    cache_size=0,
    autoescape=True,
)
jinja_env.filters["currency"] = lambda v: f"${v:.2f}"

def render_template(name: str, **context):
    template = jinja_env.get_template(name)
    html = template.render(**context)
    return HTMLResponse(content=html)

# Static files
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

# ---------- Server-side session store ----------
sessions = {}
SESSION_COOKIE = "session"
SESSION_EXPIRE_DAYS = 1

def create_session(username: str) -> str:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=SESSION_EXPIRE_DAYS)
    sessions[token] = {"username": username, "expires": expires}
    return token

def get_session(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    session = sessions.get(token)
    if not session:
        return None
    if session["expires"] < datetime.now(timezone.utc):
        del sessions[token]
        return None
    return session["username"]

def set_session_cookie(response: Response, token: str):
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        max_age=SESSION_EXPIRE_DAYS * 24 * 60 * 60,
        samesite="lax",
        path="/",
        secure=False,
    )

def clear_session_cookie(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/")

# ---------- Auth middleware ----------
class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith(("/login", "/static", "/favicon.ico")):
            return await call_next(request)

        username = get_session(request)
        if not username:
            return RedirectResponse("/login", status_code=303)

        return await call_next(request)

app.add_middleware(AuthMiddleware)

# ---------- Lifespan (no auto‑connect) ----------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Application starting – no auto‑connect")
    yield
    logger.info("🛑 Application shutting down, disconnecting...")
    await bot.disconnect()
    logger.info("✅ Application shutdown complete")

app.router.lifespan_context = lifespan

# ---------- Routes ----------
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return render_template("login.html", request=request)

@app.post("/login")
async def login(request: Request, response: Response):
    form = await request.form()
    username = form.get("username", "").strip()
    password = form.get("password", "").strip()

    if username != settings.username or password != settings.password:
        return render_template("login.html", request=request, error="Invalid credentials")

    token = create_session(username)
    resp = RedirectResponse(url="/", status_code=303)
    set_session_cookie(resp, token)
    return resp

@app.post("/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        sessions.pop(token, None)
    clear_session_cookie(response)
    bot.reset_stats()
    await bot.disconnect()
    return RedirectResponse(url="/login", status_code=303)

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return render_template("dashboard.html", request=request)

# ---------- Background connection task ----------
async def background_connect():
    global connection_status, connection_error
    try:
        logger.info("🌐 [BG] Starting background connection...")
        connection_status = "connecting"
        logger.info("🌐 [BG] Calling bot.connect()...")
        success = await bot.connect()
        if success:
            connection_status = "connected"
            logger.info("🌐 [BG] ✅ Connection successful! Bot ready.")
        else:
            connection_status = "failed"
            connection_error = "Connection failed (bot.connect returned False)"
            logger.error("🌐 [BG] ❌ Connection failed.")
    except Exception as e:
        connection_status = "failed"
        connection_error = str(e)
        logger.error(f"🌐 [BG] ❌ Exception: {e}", exc_info=True)

# ---------- API endpoints ----------
@app.post("/api/connect")
async def connect():
    global connection_status, connection_error
    logger.info("📡 API /api/connect called")
    if connection_status == "connecting":
        logger.warning("📡 API /api/connect: Already connecting, ignoring.")
        return {"status": "already connecting"}
    if connection_status == "connected":
        logger.warning("📡 API /api/connect: Already connected.")
        return {"status": "already connected"}
    connection_error = None
    logger.info("📡 API /api/connect: Starting background connection...")
    asyncio.create_task(background_connect())
    logger.info("📡 API /api/connect: Background task started, returning immediately.")
    return {"status": "connecting"}

@app.post("/api/start")
async def start_trading():
    global connection_status
    logger.info("📡 API /api/start called")
    if connection_status != "connected":
        logger.warning("📡 API /api/start: Not connected (status=%s)", connection_status)
        return {"status": "not connected", "detail": f"Connection status: {connection_status}"}
    if not bot._ready:
        logger.warning("📡 API /api/start: Bot not ready (assets not loaded)")
        return {"status": "not ready", "detail": "Bot still initializing, please wait."}
    if bot._running:
        logger.warning("📡 API /api/start: Bot already running")
        return {"status": "already running"}
    logger.info("📡 API /api/start: Calling bot.start_trading()...")
    await bot.start_trading()
    logger.info("📡 API /api/start: start_trading() completed.")
    return {"status": "started"}

@app.post("/api/stop")
async def stop_trading():
    logger.info("📡 API /api/stop called")
    if bot._running:
        await bot.stop_trading()
        logger.info("📡 API /api/stop: Trading stopped.")
        return {"status": "stopped"}
    else:
        logger.warning("📡 API /api/stop: Bot not running")
        return {"status": "not running"}

@app.get("/api/status")
async def status():
    global connection_status, connection_error
    stats = bot.get_stats()
    stats["connection_status"] = connection_status
    if connection_error:
        stats["connection_error"] = connection_error
    logger.debug(f"📡 API /api/status: stats ok (conn={connection_status})")
    return stats

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)