"""Tracks session statistics (P&L, win rate, etc.)."""

import logging
from typing import List, Dict
from datetime import datetime

from ..models.trade import Trade

logger = logging.getLogger(__name__)

class SessionManager:
    """Maintains session stats and trade history."""

    def __init__(self):
        self.trades: List[Trade] = []
        self.wins = 0
        self.losses = 0
        self.total_pnl = 0.0
        self.current_stake = 1.0
        self.consecutive_wins = 0
        self.consecutive_losses = 0
        self.balance = 0.0

    def add_trade(self, trade: Trade):
        self.trades.append(trade)

    def record_result(self, trade: Trade, result: str, pnl: float):
        trade.result = result
        trade.pnl = pnl
        if result == "WIN":
            self.wins += 1
            self.consecutive_wins += 1
            self.consecutive_losses = 0
            self.total_pnl += pnl
        else:
            self.losses += 1
            self.consecutive_losses += 1
            self.consecutive_wins = 0
            self.total_pnl += pnl  # pnl is negative for loss
        self.balance += pnl

    def get_stats(self) -> Dict:
        total_trades = self.wins + self.losses
        win_rate = (self.wins / total_trades * 100) if total_trades > 0 else 0.0
        return {
            "balance": self.balance,
            "total_pnl": self.total_pnl,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(win_rate, 2),
            "consecutive_wins": self.consecutive_wins,
            "consecutive_losses": self.consecutive_losses,
            "current_stake": self.current_stake,
            "last_trade": self.trades[-1] if self.trades else None
        }

    def reset(self):
        self.trades.clear()
        self.wins = 0
        self.losses = 0
        self.total_pnl = 0.0
        self.consecutive_wins = 0
        self.consecutive_losses = 0
        self.balance = 0.0