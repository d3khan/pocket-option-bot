"""API endpoints for bot control."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...web.auth import get_current_user
from ...web.dependencies import get_bot_orchestrator
from ...core.bot_orchestrator import BotOrchestrator

router = APIRouter()

class BotActionResponse(BaseModel):
    status: str

@router.post("/bot/start", response_model=BotActionResponse)
async def start_bot(
    user: str = Depends(get_current_user),
    orchestrator: BotOrchestrator = Depends(get_bot_orchestrator)
):
    if orchestrator._running:
        raise HTTPException(status_code=400, detail="Bot already running")
    await orchestrator.start()
    return BotActionResponse(status="started")

@router.post("/bot/stop", response_model=BotActionResponse)
async def stop_bot(
    user: str = Depends(get_current_user),
    orchestrator: BotOrchestrator = Depends(get_bot_orchestrator)
):
    if not orchestrator._running:
        raise HTTPException(status_code=400, detail="Bot not running")
    await orchestrator.stop()
    return BotActionResponse(status="stopped")

@router.post("/bot/force_switch", response_model=BotActionResponse)
async def force_switch(
    user: str = Depends(get_current_user),
    orchestrator: BotOrchestrator = Depends(get_bot_orchestrator)
):
    if not orchestrator._running:
        raise HTTPException(status_code=400, detail="Bot not running")
    await orchestrator.asset_scanner.force_switch()
    return BotActionResponse(status="switched")

@router.get("/bot/status")
async def bot_status(
    user: str = Depends(get_current_user),
    orchestrator: BotOrchestrator = Depends(get_bot_orchestrator)
):
    # Return current bot state
    stats = orchestrator.session_mgr.get_stats()
    return {
        "running": orchestrator._running,
        "current_asset": orchestrator.asset_scanner._current_asset,
        "balance": stats["balance"],
        "total_pnl": stats["total_pnl"],
        "win_rate": stats["win_rate"],
        "consecutive_wins": stats["consecutive_wins"],
        "consecutive_losses": stats["consecutive_losses"],
        "current_stake": stats["current_stake"],
        "daily_pnl": orchestrator.risk_manager.get_daily_pnl()
    }

@router.get("/connection-status")
async def connection_status(
    user: str = Depends(get_current_user),
    orchestrator: BotOrchestrator = Depends(get_bot_orchestrator)
):
    return {"connected": orchestrator.client._connected}