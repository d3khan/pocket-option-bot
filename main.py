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

Path("data").mkdir(exist_ok=True)

client = POClient(settings.ssid)
bot = TradingBot(client)

app = FastAPI(title="Pocket Bot Simple")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters["currency"] = lambda v: f"${v:.2f}"

app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(bot.connect())

# ---------- Session management ----------
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
        return data.get("username")
    except (SignatureExpired, BadSignature):
        return None

# ---------- Auth middleware ----------
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
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login(request: Request, response: Response):
    form = await request.form()
    username = form.get("username", "").strip()
    password = form.get("password", "").strip()

    if username != settings.username or password != settings.password:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid credentials"}
        )

    # Create signed token
    token = create_signed_session(username)
    # Return HTML with JavaScript to set cookie and redirect
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Redirecting...</title>
    </head>
    <body>
        <script>
            // Set cookie with 1 day expiry
            document.cookie = "session={token}; path=/; max-age=86400; SameSite=Lax";
            window.location.href = "/";
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html, status_code=200)

@app.post("/logout")
async def logout(request: Request, response: Response):
    # Delete cookie via JS
    html = """
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><title>Logging out...</title></head>
    <body>
        <script>
            document.cookie = "session=; path=/; max-age=0;";
            window.location.href = "/login";
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html, status_code=200)

# ---------- Dashboard ----------
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})

@app.get("/partials/control", response_class=HTMLResponse)
async def control_partial(request: Request):
    stats = bot.get_stats()
    return templates.TemplateResponse("partials/control.html", {
        "request": request,
        "running": stats.get("running", False),
        "connected": stats.get("connected", False),
        "config": settings,
    })

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
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)