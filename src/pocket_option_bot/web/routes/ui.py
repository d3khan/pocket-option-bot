"""UI routes for serving HTML pages."""

from fastapi import APIRouter, Depends, Request, HTTPException, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from ...web.auth import get_current_user_redirect, get_current_user
from ...web.dependencies import get_bot_orchestrator
from ...core.bot_orchestrator import BotOrchestrator

router = APIRouter()

def get_templates(request: Request):
    return request.app.state.templates

@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, user: str = Depends(get_current_user_redirect)):
    """Main dashboard page. Redirects to login if not authenticated."""
    return get_templates(request).TemplateResponse("dashboard.html", {"request": request})

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page. Accessible without authentication."""
    return get_templates(request).TemplateResponse("login.html", {"request": request})

@router.get("/partials/stats_cards", response_class=HTMLResponse)
async def stats_cards_partial(request: Request, response: Response, user: str = Depends(get_current_user_redirect)):
    return get_templates(request).TemplateResponse("partials/stats_cards.html", {"request": request})

@router.get("/partials/trade_log", response_class=HTMLResponse)
async def trade_log_partial(request: Request, response: Response, user: str = Depends(get_current_user_redirect)):
    return get_templates(request).TemplateResponse("partials/trade_log.html", {"request": request})

@router.get("/partials/candle_display", response_class=HTMLResponse)
async def candle_display_partial(request: Request, response: Response, user: str = Depends(get_current_user_redirect)):
    return get_templates(request).TemplateResponse("partials/candle_display.html", {"request": request})

@router.get("/partials/control_panel", response_class=HTMLResponse)
async def control_panel_partial(request: Request, response: Response, user: str = Depends(get_current_user_redirect)):
    return get_templates(request).TemplateResponse("partials/control_panel.html", {"request": request})

@router.get("/partials/connection_status", response_class=HTMLResponse)
async def connection_status_partial(request: Request, response: Response, user: str = Depends(get_current_user_redirect)):
    return get_templates(request).TemplateResponse("partials/connection_status.html", {"request": request})