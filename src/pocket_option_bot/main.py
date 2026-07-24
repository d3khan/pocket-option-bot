"""FastAPI application entry point and lifespan management."""

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jose import jwt, JWTError
from starlette.middleware.base import BaseHTTPMiddleware

from .config import settings
from .core.bot_orchestrator import BotOrchestrator
from .core.events import EventBus
from .services.persistence import PersistenceService
from .web.auth import auth_router
from .web.dependencies import get_bot_orchestrator, get_event_bus, get_persistence
from .web.routes import api, ui, ws
from .web.routes.ws import socketio_app, setup_event_bus_forwarding
from .web.server import app as fastapi_app

# Configure logging
from structlog import configure
from structlog.processors import JSONRenderer, TimeStamper, StackInfoRenderer, format_exc_info
from structlog.stdlib import LoggerFactory
import structlog

def setup_logging():
    configure(
        processors=[
            TimeStamper(fmt="iso"),
            StackInfoRenderer(),
            format_exc_info,
            JSONRenderer(indent=2) if settings.debug else JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

setup_logging()
logger = structlog.get_logger()

# Create required directories
Path(settings.db.path).parent.mkdir(parents=True, exist_ok=True)

# Global instances
event_bus = EventBus()
persistence = PersistenceService(db_path=settings.db.path)

# ---------- Authentication Middleware ----------
class AuthRedirectMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        public_paths = ["/login", "/auth", "/static", "/health", "/favicon.ico"]
        path = request.url.path
        if any(path.startswith(p) for p in public_paths):
            return await call_next(request)

        token = request.cookies.get("access_token")
        if not token:
            return RedirectResponse(url="/login", status_code=303)

        try:
            payload = jwt.decode(token, settings.jwt.secret_key, algorithms=[settings.jwt.algorithm])
            username = payload.get("sub")
            if username is None or username != settings.auth.username:
                return RedirectResponse(url="/login", status_code=303)
        except JWTError:
            return RedirectResponse(url="/login", status_code=303)

        return await call_next(request)

# Lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting application")
    try:
        await persistence.initialize()
        logger.info("Database initialized")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        sys.exit(1)

    try:
        await setup_event_bus_forwarding()
        logger.info("Event bus forwarding set up")
    except Exception as e:
        logger.error(f"Event bus setup failed: {e}")

    # Set up app state subscriptions
    setup_app_state_subscriptions()
    logger.info("App state subscriptions set up")
    yield

    logger.info("Shutting down")
    await persistence.close()
    orch = get_bot_orchestrator()
    if orch:
        await orch.stop()

# Build FastAPI app
app = fastapi_app
app.router.lifespan_context = lifespan

# Add middleware
app.add_middleware(AuthRedirectMiddleware)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth_router, prefix="/auth", tags=["auth"])
app.include_router(ui.router)
app.include_router(api.router, prefix="/api")
app.include_router(ws.router)

# Mount Socket.IO
app.mount("/socket.io/", socketio_app)
logger.info("Socket.IO mounted")

# Static files
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "web" / "static"), name="static")
logger.info("Static files mounted")

# ---------- Templates and custom filter ----------
templates = Jinja2Templates(directory=Path(__file__).parent / "web" / "templates")

def currency_filter(value):
    return f"${value:.2f}"

templates.env.filters["currency"] = currency_filter
app.state.templates = templates

# ---------- Application state (used by partials) ----------
app.state.stats = {
    "balance": 0.0,
    "total_pnl": 0.0,
    "daily_pnl": 0.0,
    "win_rate": 0.0,
    "consecutive_wins": 0,
    "consecutive_losses": 0,
    "current_stake": 0.0,
    "current_asset": "None",
}
app.state.config = {
    "bot": {
        "base_stake": settings.bot.base_stake,
        "multiplier": settings.bot.multiplier,
        "max_stake": settings.bot.max_stake,
        "min_payout": settings.bot.min_payout,
    }
}
app.state.trades = []
app.state.current_candle = {}
app.state.bot_status = "stopped"
app.state.connected = False

# Override dependencies for testing
app.dependency_overrides[get_event_bus] = lambda: event_bus
app.dependency_overrides[get_persistence] = lambda: persistence

# ---------- Subscribe to event bus to update app state ----------
async def update_stats_from_event(data: dict):
    """Update app.state.stats from stats_update event."""
    app.state.stats.update(data)
async def update_connection_status(data: dict):
    """Update app.state.connected from connection_status event."""
    app.state.connected = data.get("status") == "connected"
async def update_bot_status(data: dict):
    """Update app.state.bot_status from bot_status_changed event."""
    app.state.bot_status = data.get("status", "stopped")

# We'll subscribe after the event bus is set up in lifespan
def setup_app_state_subscriptions():
    event_bus.subscribe("stats_update", update_stats_from_event)
    event_bus.subscribe("connection_status", update_connection_status)
    event_bus.subscribe("bot_status_changed", update_bot_status)

# Health check
@app.get("/health")
async def health():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("pocket_option_bot.main:app", host="0.0.0.0", port=settings.web.port, reload=True)