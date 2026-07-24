"""FastAPI application entry point and lifespan management."""

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import FileResponse

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

# Lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting application")
    try:
        await persistence.initialize()
        logger.info("Database initialized")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        sys.exit(1)

    # Set up event bus forwarding for Socket.IO
    try:
        await setup_event_bus_forwarding()
        logger.info("Event bus forwarding set up")
    except Exception as e:
        logger.error(f"Event bus setup failed: {e}")

    yield

    # Shutdown
    logger.info("Shutting down")
    await persistence.close()
    orch = get_bot_orchestrator()
    if orch:
        await orch.stop()

# Build FastAPI app
app = fastapi_app
app.router.lifespan_context = lifespan

# Include routers
app.include_router(auth_router, prefix="/auth", tags=["auth"])
app.include_router(ui.router)
app.include_router(api.router, prefix="/api")
app.include_router(ws.router)

# Mount Socket.IO app
app.mount("/socket.io/", socketio_app)
logger.info("Socket.IO mounted")

# Static files
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "web" / "static"), name="static")
logger.info("Static files mounted")

# Templates with custom filter
from .utils.formatting import currency

templates = Jinja2Templates(directory=Path(__file__).parent / "web" / "templates")

templates.env.filters["currency"] = currency
app.state.templates = templates

# Set initial state for templates
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

# Dependency overrides for testing
app.dependency_overrides[get_event_bus] = lambda: event_bus
app.dependency_overrides[get_persistence] = lambda: persistence

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse("src/pocket_option_bot/web/static/img/icon.png")

# Health check
@app.get("/health")
async def health():
    return {"status": "ok"}

# If run directly
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("pocket_option_bot.main:app", host="0.0.0.0", port=settings.web.port, reload=True)