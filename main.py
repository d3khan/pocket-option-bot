import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from jinja2 import Environment, FileSystemLoader

from config import settings
from client import POClient
from bot import TradingBot

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

Path("data").mkdir(exist_ok=True)

client = POClient(settings.ssid)
bot = TradingBot(client)

app = FastAPI(title="Pocket Bot Simple")

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

app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

# ---------- Session store ----------
sessions = {}
SESSION_COOKIE = "session"
SESSION_EXPIRE_DAYS = 1

def create_session(username: str) -> str:
    token = settings.secret_key
    expires = datetime.now(timezone.utc) + timedelta(days=SESSION_EXPIRE_DAYS)
    sessions[token] = {"username": username, "expires": expires}
    return token

def get_session(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    session = sessions.get(token)
    if not session or session["expires"] < datetime.now(timezone.utc):
        sessions.pop(token, None)
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

# ---------- Auto-connect ----------
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(bot.connect())

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

# ---------- Login / Logout ----------
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
    resp = RedirectResponse(url="/", status_code=303)
    token = create_session(username)
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

# ---------- Main Dashboard ----------
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    stats = bot.get_stats()
    assets = bot.get_all_eur_usd_assets()   # all EUR/USD assets for dropdown
    return render_template(
        "dashboard.html",
        request=request,
        stats=stats,
        assets=assets,
        manual_asset=stats.get("manual_asset"),
        config=settings,
    )

# ---------- JSON API for polling ----------
@app.get("/api/status")
async def status():
    return bot.get_stats()

# ---------- Action endpoints (return JSON) ----------
@app.post("/api/connect")
async def api_connect():
    await bot.connect()
    return JSONResponse({"ok": True})

@app.post("/api/start")
async def api_start():
    if bot._connected and not bot._running:
        await bot.start_trading()
    return JSONResponse({"ok": True})

@app.post("/api/stop")
async def api_stop():
    if bot._running:
        await bot.stop_trading()
    return JSONResponse({"ok": True})

@app.post("/api/select_asset")
async def api_select_asset(request: Request):
    form = await request.form()
    asset = form.get("asset")
    if asset == "auto":
        await bot.clear_manual_asset()
    else:
        await bot.set_manual_asset(asset)
    return JSONResponse({"ok": True})

# ---------- Lifespan ----------
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await bot.disconnect()

app.router.lifespan_context = lifespan

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        access_log=False,
        log_level="info"
    )