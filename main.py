import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from jinja2 import Environment, FileSystemLoader

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

# ---------- Auto-connect ----------
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(bot.connect())

# ---------- Session cookie (client‑side signed) ----------
SESSION_COOKIE = "session"
SESSION_EXPIRE_DAYS = 1
serializer = URLSafeTimedSerializer(settings.secret_key)

def create_signed_session(username: str) -> str:
    return serializer.dumps({"username": username})

def get_session_data(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    try:
        data = serializer.loads(token, max_age=SESSION_EXPIRE_DAYS * 24 * 60 * 60)
        return data.get("username")
    except (SignatureExpired, BadSignature):
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
        secure=False,          # Works on both HTTP and HTTPS
    )

def clear_session_cookie(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/")

# ---------- Auth Middleware ----------
class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith(("/login", "/static", "/favicon.ico")):
            return await call_next(request)

        username = get_session_data(request)
        if not username:
            return RedirectResponse("/login", status_code=303)

        return await call_next(request)

app.add_middleware(AuthMiddleware)

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

    set_session_cookie(response, username)
    return RedirectResponse(url="/", status_code=303)

@app.post("/logout")
async def logout(request: Request, response: Response):
    clear_session_cookie(response)
    return RedirectResponse(url="/login", status_code=303)

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return render_template("dashboard.html", request=request)

@app.get("/partials/control", response_class=HTMLResponse)
async def control_partial(request: Request):
    stats = bot.get_stats()
    return render_template(
        "partials/control.html",
        request=request,
        running=stats.get("running", False),
        connected=stats.get("connected", False),
        config=settings,
    )

@app.post("/api/connect", response_class=HTMLResponse)
async def connect(request: Request):
    await bot.connect()
    stats = bot.get_stats()
    return render_template(
        "partials/control.html",
        request=request,
        running=stats.get("running", False),
        connected=stats.get("connected", False),
        config=settings,
    )

@app.post("/api/start", response_class=HTMLResponse)
async def start_trading(request: Request):
    if bot._connected and not bot._running:
        await bot.start_trading()
    stats = bot.get_stats()
    return render_template(
        "partials/control.html",
        request=request,
        running=stats.get("running", False),
        connected=stats.get("connected", False),
        config=settings,
    )

@app.post("/api/stop", response_class=HTMLResponse)
async def stop_trading(request: Request):
    if bot._running:
        await bot.stop_trading()
    stats = bot.get_stats()
    return render_template(
        "partials/control.html",
        request=request,
        running=stats.get("running", False),
        connected=stats.get("connected", False),
        config=settings,
    )

@app.get("/api/status")
async def status():
    return bot.get_stats()

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

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await bot.disconnect()

app.router.lifespan_context = lifespan

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)