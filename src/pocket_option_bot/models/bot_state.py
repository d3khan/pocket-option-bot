"""Bot state model."""

from enum import Enum
from typing import Optional
from pydantic import BaseModel
from .trade import Trade

class BotStatus(str, Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED_MAX_LOSSES = "paused_max_losses"
    PAUSED_DAILY_LOSS = "paused_daily_loss"

class BotState(BaseModel):
    status: BotStatus = BotStatus.STOPPED
    current_asset: Optional[str] = None
    current_payout: Optional[float] = None
    balance: float = 0.0
    total_pnl: float = 0.0
    daily_pnl: float = 0.0
    win_rate: float = 0.0
    consecutive_wins: int = 0
    consecutive_losses: int = 0
    current_stake: float = 0.0
    last_trade: Optional[Trade] = None