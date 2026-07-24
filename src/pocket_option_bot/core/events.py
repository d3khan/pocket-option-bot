"""Internal asynchronous event bus for decoupled communication."""

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional, Awaitable

EventCallback = Callable[[Any], Awaitable[None]]

logger = logging.getLogger(__name__)


class EventBus:
    """Simple async event bus using asyncio.Queue."""

    def __init__(self):
        self._subscribers: Dict[str, List[EventCallback]] = {}
        self._queue: asyncio.Queue = asyncio.Queue()
        self._running = False
        self._worker_task: Optional[asyncio.Task] = None

    def subscribe(self, event_type: str, callback: EventCallback):
        """Subscribe a callback to an event type."""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(callback)

    def unsubscribe(self, event_type: str, callback: EventCallback):
        if event_type in self._subscribers:
            try:
                self._subscribers[event_type].remove(callback)
            except ValueError:
                pass

    async def emit(self, event_type: str, data: Any):
        """Emit an event asynchronously (non-blocking)."""
        await self._queue.put((event_type, data))

    async def start(self):
        """Start the event processing loop."""
        self._running = True
        self._worker_task = asyncio.create_task(self._worker())

    async def stop(self):
        """Stop the event processing loop."""
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

    async def _worker(self):
        while self._running:
            try:
                event_type, data = await self._queue.get()
                callbacks = self._subscribers.get(event_type, [])
                for cb in callbacks:
                    try:
                        await cb(data)
                    except Exception as e:
                        logger.error("Event callback error for '%s': %s", event_type, e, exc_info=True)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Event worker error: %s", e, exc_info=True)
                continue