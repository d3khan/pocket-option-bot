"""FastAPI dependency injection."""

from typing import Optional

from fastapi import Request

from ..core.bot_orchestrator import BotOrchestrator
from ..core.events import EventBus
from ..services.persistence import PersistenceService

# Global instances (set during startup)
_bot_orchestrator: Optional[BotOrchestrator] = None
_event_bus: Optional[EventBus] = None
_persistence: Optional[PersistenceService] = None

def get_event_bus() -> EventBus:
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus

def get_persistence() -> PersistenceService:
    global _persistence
    if _persistence is None:
        from ..config import settings
        _persistence = PersistenceService(settings.db.path)
    return _persistence

def get_bot_orchestrator() -> BotOrchestrator:
    global _bot_orchestrator
    if _bot_orchestrator is None:
        _bot_orchestrator = BotOrchestrator(get_event_bus(), get_persistence())
    return _bot_orchestrator