import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature

from config import settings
from client import POClient
from bot import TradingBot

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create data dir
Path("data").mkdir(exist_ok=True)

# Globals
client = POClient(settings.ssid)
bot = TradingBot(client)

app = FastAPI(title="Pocket Bot Simple")

# ---------- Templates (with cache disabled) ----------
TEMPLATES_DIR = str(Path(__file__).parent / "templates")
templates = Jinja2Templates(directory=TEMPLATES_DIR)
templates.env.filters["currency"] = lambda v: f"${v:.2f}"
# Disable the cache to avoid the unhashable type error
templates.env.cache = {}

app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

# ---------- Auto-connect on startup ----------
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(bot.connect())

# ---------- Client-side signed session cookie ----------
SESSION_COOKIE = "session"
SESSION_EXPIRE_DAYS = 1
serializer = URLSafeTimedSerializer(settings.secret_key)

def create_signed_session(username: str) -> str:
    return serializer.dumps({"username": username})

def get_session_data(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        logger.info("No session cookie found")
        return None
    try:
        data = serializer.loads(token, max_age=SESSION_EXPIRE_DAYS * 24 * 60 * 60)
        username = data.get("username")
        logger.info(f"Session decoded: username={username}")
        return username
    except SignatureExpired as e:
        logger.info(f"Session expired: {e}")
        return None
    except BadSignature as e:
        logger.info(f"Bad signature: {e}")
        return None

def set_session_cookie(response: Response, username: str):
    signed = create_signed_session(username)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=signed,
        httponly=True,
        max_age=SESSION_EXPIRE_DAYS * 24 * 60 * 60,
        samesite="lax",
        path="/",
        domain=None,
        secure=False,
    )
    logger.info(f"Session cookie set for {username}")

def clear_session_cookie(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/", domain=None)

# ---------- Auth middleware ----------
class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith(("/login", "/static", "/favicon.ico")):
            return await call_next(request)

        logger.info(f"Auth: path={request.url.path}, cookies={list(request.cookies.keys())}")
        username = get_session_data(request)
        if not username:
            logger.warning("Unauthenticated, redirecting to login")
            return RedirectResponse("/login", status_code=303)

        return await call_next(request)

app.add_middleware(AuthMiddleware)

# ---------- Routes ----------
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login(request: Request, response: Response):
    form = await request.form()
    username = form.get("username", "").strip()
    password = form.get("password", "").strip()

    logger.info(f"Login attempt: username='{username}', password='{password}'")
    logger.info(f"Expected: username='{settings.username}', password='{settings.password}'")

    if username != settings.username or password != settings.password:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid credentials"}
        )

    set_session_cookie(response, username)
    return RedirectResponse(url="/", status_code=303)

@app.post("/logout")
async def logout(request: Request, response: Response):
    clear_session_cookie(response)
    return RedirectResponse(url="/login", status_code=303)

# ---------- Dashboard ----------
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})

# ---------- Partial for control panel ----------
@app.get("/partials/control", response_class=HTMLResponse)
async def control_partial(request: Request):
    stats = bot.get_stats()
    return templates.TemplateResponse("partials/control.html", {
        "request": request,
        "running": stats.get("running", False),
        "connected": stats.get("connected", False),
        "config": settings,
    })

# ---------- API endpoints ----------
@app.post("/api/connect", response_class=HTMLResponse)
async def connect(request: Request):
    await bot.connect()
    stats = bot.get_stats()
    return templates.TemplateResponse("partials/control.html", {
        "request": request,
        "running": stats.get("running", False),
        "connected": stats.get("connected", False),
        "config": settings,
    })

@app.post("/api/start", response_class=HTMLResponse)
async def start_trading(request: Request):
    if bot._connected and not bot._running:
        await bot.start_trading()
    stats = bot.get_stats()
    return templates.TemplateResponse("partials/control.html", {
        "request": request,
        "running": stats.get("running", False),
        "connected": stats.get("connected", False),
        "config": settings,
    })

@app.post("/api/stop", response_class=HTMLResponse)
async def stop_trading(request: Request):
    if bot._running:
        await bot.stop_trading()
    stats = bot.get_stats()
    return templates.TemplateResponse("partials/control.html", {
        "request": request,
        "running": stats.get("running", False),
        "connected": stats.get("connected", False),
        "config": settings,
    })

@app.get("/api/status")
async def status():
    return bot.get_stats()

# ---------- WebSocket ----------
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            stats = bot.get_stats()
            await websocket.send_json(stats)
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass

# ---------- Lifespan ----------
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await bot.disconnect()

app.router.lifespan_context = lifespan

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)