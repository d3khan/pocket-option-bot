import os
from typing import List
from pydantic_settings import BaseSettings
from pydantic import ConfigDict

class Settings(BaseSettings):
    ssid: str = "your_actual_ssid_here"
    base_stake: float = 1.0
    multiplier: float = 2.5
    max_stake: float = 16.0
    min_payout: int = 92
    trade_duration: int = 30
    max_consecutive_losses: int = 5
    max_daily_loss: float = 50.0
    username: str = "d3khan04"
    password: str = "fake_smile02"
    secret_key: str = "0BVMBWw3Ry8qMpk5nsbTtGWsc17-6fShGqslKtgt78I="
    db_path: str = "data/bot.db"
    allowed_assets: List[str] = [
        "EURUSD_otc", "GBPUSD_otc", "USDJPY_otc", "AUDUSD_otc",
        "USDCAD_otc", "NZDUSD_otc", "EURJPY_otc", "GBPJPY_otc",
        "AUDCAD_otc", "AEDCNY_otc", "ETHUSD_otc", "DOGE_otc"
    ]

    model_config = ConfigDict(
        env_prefix="PO_",
        env_file=".env",
        extra="ignore"
    )

settings = Settings()