"""Pydantic models for trade, signal, candle, and asset."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field

class Trade(BaseModel):
    id: str
    asset: str
    direction: str  # "CALL" or "PUT"
    stake: float
    duration: int
    open_time: datetime
    close_time: Optional[datetime] = None
    result: Optional[str] = None  # "WIN" or "LOSS"
    pnl: Optional[float] = None

class Signal(BaseModel):
    asset: str
    direction: str
    open: float
    close: float
    timestamp: datetime

class Candle(BaseModel):
    asset: str
    open: float
    high: float
    low: float
    close: float
    time: datetime

class Asset(BaseModel):
    symbol: str
    payout: float
    # other fields optional