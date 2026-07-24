"""UI routes for serving HTML pages."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from ...web.auth import get_current_user_redirect
router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")

# Register currency filter
def currency_filter(value):
    return f"${value:.2f}"
templates.env.filters["currency"] = currency_filter

@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, user: str = Depends(get_current_user_redirect)):
    return templates.TemplateResponse("dashboard.html", {"request": request})

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@router.get("/partials/stats_cards", response_class=HTMLResponse)
async def stats_cards_partial(request: Request):
    stats = request.app.state.stats
    return templates.TemplateResponse("partials/stats_cards.html", {"request": request, "stats": stats})

@router.get("/partials/trade_log", response_class=HTMLResponse)
async def trade_log_partial(request: Request):
    trades = request.app.state.trades
    return templates.TemplateResponse("partials/trade_log.html", {"request": request, "trades": trades})

@router.get("/partials/candle_display", response_class=HTMLResponse)
async def candle_display_partial(request: Request):
    candle = request.app.state.current_candle
    return templates.TemplateResponse("partials/candle_display.html", {"request": request, "candle": candle})

@router.get("/partials/control_panel", response_class=HTMLResponse)
async def control_panel_partial(request: Request):
    config = request.app.state.config
    bot_status = request.app.state.bot_status
    return templates.TemplateResponse(
        "partials/control_panel.html",
        {"request": request, "config": config, "bot_status": bot_status}
    )

@router.get("/partials/connection_status", response_class=HTMLResponse)
async def connection_status_partial(request: Request):
    connected = request.app.state.connected
    return templates.TemplateResponse(
        "partials/connection_status.html",
        {"request": request, "connected": connected}
    )