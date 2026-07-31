import os
from typing import List
from pydantic_settings import BaseSettings
from pydantic import ConfigDict

class Settings(BaseSettings):
    ssid: str = os.getenv("PO_SSID", "your_actual_ssid_here")
    base_stake: float = 1.0
    multiplier: float = 2.5
    max_stake: float = 16.0
    min_payout: int = 92
    candle_period: int = 60          
    trade_duration: int = 60          
    ema_period: int = 20
    rsi_period: int = 14
    rsi_overbought: int = 60
    rsi_oversold: int = 40
    max_consecutive_losses: int = 2
    max_daily_loss: float = 50.0
    username: str = os.getenv("PO_USERNAME", "d3khan04")
    password: str = os.getenv("PO_PASSWORD", "fake_smile02")
    secret_key: str = os.getenv("SECRET_KEY", "super-secret-key-change-me")

    model_config = ConfigDict(
        env_prefix="PO_",
        env_file=".env",
        extra="ignore"
    )

settings = Settings()