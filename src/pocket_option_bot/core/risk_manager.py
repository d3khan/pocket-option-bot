"""Martingale risk management with daily loss limit."""

import logging
from datetime import datetime, timezone
from typing import Optional

from ..config import settings

logger = logging.getLogger(__name__)

class RiskManager:
    """Manages stake size, consecutive losses, and daily loss limit."""

    def __init__(self):
        self.base_stake = settings.bot.base_stake
        self.multiplier = settings.bot.multiplier
        self.max_stake = settings.bot.max_stake
        self.max_consecutive_losses = settings.bot.max_consecutive_losses
        self.max_daily_loss = settings.bot.max_daily_loss

        self.current_stake = self.base_stake
        self.consecutive_losses = 0
        self.daily_pnl = 0.0
        self._current_day = datetime.now(timezone.utc).date()
        self._total_pnl = 0.0  # overall session

    def reset_after_win(self):
        """Reset stake and loss count after a win."""
        self.current_stake = self.base_stake
        self.consecutive_losses = 0

    def apply_loss(self, loss_amount: float) -> bool:
        """Update state after a loss. Returns True if trading should stop."""
        self.consecutive_losses += 1
        self.daily_pnl -= loss_amount
        self._total_pnl -= loss_amount
        # Check daily reset
        self._check_day_reset()
        # Increase stake
        new_stake = self.current_stake * self.multiplier
        self.current_stake = min(new_stake, self.max_stake)
        # Check limits
        if self.consecutive_losses >= self.max_consecutive_losses:
            logger.warning("Max consecutive losses reached. Stopping.")
            return True  # stop trading
        if self.daily_pnl <= -self.max_daily_loss:
            logger.warning("Daily loss limit reached. Stopping.")
            return True
        return False

    def apply_win(self, profit: float):
        """Update after a win."""
        self.reset_after_win()
        self.daily_pnl += profit
        self._total_pnl += profit
        self._check_day_reset()

    def _check_day_reset(self):
        now = datetime.now(timezone.utc)
        if now.date() != self._current_day:
            self.daily_pnl = 0.0
            self._current_day = now.date()

    def get_stake(self) -> float:
        return self.current_stake

    def get_daily_pnl(self) -> float:
        self._check_day_reset()
        return self.daily_pnl

    def get_total_pnl(self) -> float:
        return self._total_pnl

    def get_consecutive_losses(self) -> int:
        return self.consecutive_losses