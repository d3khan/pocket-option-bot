"""Socket.IO integration for real-time updates."""

import logging
from typing import Any

import socketio
from fastapi import APIRouter

from ...web.dependencies import get_event_bus, get_bot_orchestrator
from ...core.events import EventBus

logger = logging.getLogger(__name__)

# Create a Socket.IO server instance (ASGI compatible)
sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')
router = APIRouter()

# Attach to FastAPI app later
socketio_app = socketio.ASGIApp(sio)   # <-- export this

# Store connected clients
connected_clients = set()

@sio.event
async def connect(sid, environ):
    logger.info(f"Client connected: {sid}")
    connected_clients.add(sid)
    # Send initial state
    await sio.emit('connection_established', {'status': 'ok'}, room=sid)

@sio.event
async def disconnect(sid):
    logger.info(f"Client disconnected: {sid}")
    connected_clients.discard(sid)

# Function to broadcast events to all connected clients
async def broadcast_event(event: str, data: Any):
    if connected_clients:
        await sio.emit(event, data)

# Function to set up event bus forwarding (called from main lifespan)
async def setup_event_bus_forwarding():
    event_bus = get_event_bus()
    # Forward internal events to Socket.IO clients
    event_bus.subscribe("stats_update", lambda data: broadcast_event("stats_update", data))
    event_bus.subscribe("trade_new", lambda data: broadcast_event("trade_new", data))
    event_bus.subscribe("trade_closed", lambda data: broadcast_event("trade_closed", data))
    event_bus.subscribe("candle_tick", lambda data: broadcast_event("candle_tick", data))
    event_bus.subscribe("asset_switched", lambda data: broadcast_event("asset_switched", data))
    event_bus.subscribe("connection_status", lambda data: broadcast_event("connection_status", data))
    event_bus.subscribe("bot_status_changed", lambda data: broadcast_event("bot_status_changed", data))
    logger.info("Event bus forwarding set up")