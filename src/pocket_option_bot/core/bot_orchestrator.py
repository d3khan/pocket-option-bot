"""Orchestrates all core components; provides start/stop control."""

import asyncio
import logging
from typing import Optional
from datetime import datetime, timezone

from .client import PocketOptionClient
from .candle_stream import CandleStream
from .strategy import CandleColorStrategy
from .risk_manager import RiskManager
from .asset_scanner import AssetScanner
from .session_mgr import SessionManager
from .events import EventBus
from ..config import settings
from ..models.trade import Trade, Signal
from ..services.persistence import PersistenceService

logger = logging.getLogger(__name__)

class BotOrchestrator:
    """Top‑level bot controller."""

    def __init__(self, event_bus: EventBus, persistence: PersistenceService):
        self.event_bus = event_bus
        self.persistence = persistence
        self.client = PocketOptionClient(event_bus)
        self.candle_stream = CandleStream(event_bus)
        self.strategy = CandleColorStrategy()
        self.risk_manager = RiskManager()
        self.session_mgr = SessionManager()
        self.asset_scanner = AssetScanner(self.client, event_bus)
        self._running = False
        self._trade_task: Optional[asyncio.Task] = None
        self._stop_requested = False

    async def start(self):
        """Start the bot: connect, scan, subscribe, and begin trading loop."""
        if self._running:
            return
        self._stop_requested = False
        self._running = True
        await self.event_bus.start()

        # Subscribe to balance updates from client
        self.event_bus.subscribe("balance_update", self._on_balance_update)

        await self.client.connect()
        await self.asset_scanner.start()
        await self.candle_stream.start()
        # Select initial asset
        initial_asset = await self.asset_scanner.switch_asset()
        if initial_asset is None:
            logger.error("No eligible assets found. Stopping.")
            await self.stop()
            return
        # Subscribe to signal and trade result events
        self.event_bus.subscribe("signal_generated", self._on_signal)
        self.event_bus.subscribe("trade_closed", self._on_trade_closed)
        # Start trade loop
        self._trade_task = asyncio.create_task(self._trade_loop())
        await self.event_bus.emit("bot_status_changed", {"status": "running"})
        logger.info("Bot started")

    async def stop(self):
        """Stop the bot gracefully."""
        if not self._running:
            return
        self._stop_requested = True
        self._running = False
        if self._trade_task:
            self._trade_task.cancel()
        await self.candle_stream.stop()
        await self.asset_scanner.stop()
        await self.client.disconnect()
        await self.event_bus.stop()
        self.event_bus.unsubscribe("signal_generated", self._on_signal)
        self.event_bus.unsubscribe("trade_closed", self._on_trade_closed)
        self.event_bus.unsubscribe("balance_update", self._on_balance_update)
        await self.event_bus.emit("bot_status_changed", {"status": "stopped"})
        logger.info("Bot stopped")

    async def _on_balance_update(self, data: dict):
        """Handle balance update from Pocket Option client."""
        balance = data.get("balance", 0.0)
        # Update session manager balance
        self.session_mgr.set_real_balance(balance)
        # Broadcast updated stats
        await self._broadcast_stats()

    async def _trade_loop(self):
        """Main loop waiting for signals and executing trades."""
        while self._running and not self._stop_requested:
            # The actual trading is triggered by signals; we use asyncio events.
            # We'll just keep the loop alive.
            await asyncio.sleep(1)

    async def _on_signal(self, data: dict):
        """Handle signal: place trade using risk manager."""
        if not self._running:
            return
        asset = data["asset"]
        direction = data["direction"]
        stake = self.risk_manager.get_stake()
        duration = settings.bot.trade_duration
        # Place trade
        trade_id = await self.client.place_trade(asset, direction, stake, duration)
        if trade_id:
            trade = Trade(
                id=trade_id,
                asset=asset,
                direction=direction,
                stake=stake,
                duration=duration,
                open_time=datetime.now(timezone.utc)
            )
            self.session_mgr.add_trade(trade)
            await self.persistence.save_trade(trade)
            await self.event_bus.emit("trade_new", trade.dict())
            logger.info(f"Trade placed: {direction} {asset} for {stake}")

    async def _on_trade_closed(self, data: dict):
        """Process trade result, update risk and session, persist."""
        trade_id = data.get("id")
        result = data.get("result")  # "WIN" or "LOSS"
        profit = data.get("profit", 0.0)
        # Find trade in session
        trade = next((t for t in self.session_mgr.trades if t.id == trade_id), None)
        if trade:
            if result == "WIN":
                self.risk_manager.apply_win(profit)
                self.session_mgr.record_result(trade, "WIN", profit)
            else:
                stop = self.risk_manager.apply_loss(abs(profit))
                self.session_mgr.record_result(trade, "LOSS", -abs(profit))
                if stop:
                    await self.stop()
                    await self.event_bus.emit("bot_status_changed", {"status": "paused_max_losses"})
            await self.persistence.update_trade(trade)
            await self.event_bus.emit("trade_closed", trade.dict())
            # Broadcast stats update
            await self._broadcast_stats()

    async def _broadcast_stats(self):
        """Broadcast current stats via event bus."""
        stats = self.session_mgr.get_stats()
        stats["daily_pnl"] = self.risk_manager.get_daily_pnl()
        stats["current_stake"] = self.risk_manager.get_stake()
        stats["consecutive_losses"] = self.risk_manager.get_consecutive_losses()
        await self.event_bus.emit("stats_update", stats)