import os
from typing import List
from pydantic_settings import BaseSettings
from pydantic import ConfigDict

class Settings(BaseSettings):
    ssid: str = os.getenv("PO_SSID", "your_actual_ssid_here")
    base_stake: float = 1.0
    multiplier: float = 2.5
    max_stake: float = 6.25
    min_payout: int = 92
    trade_duration: int = 30
    max_consecutive_losses: int = 3
    max_daily_loss: float = 50.0
    username: str = os.getenv("PO_USERNAME", "d3khan04")
    password: str = os.getenv("PO_PASSWORD", "fake_smile02")
    secret_key: str = os.getenv("SECRET_KEY", "super-secret-key-change-me")
    allowed_assets: List[str] = [
    # Forex OTC
    "EURUSD_otc", "GBPUSD_otc", "USDJPY_otc", "AUDUSD_otc", "NZDUSD_otc",
    "USDCAD_otc", "USDCHF_otc", "EURJPY_otc", "GBPJPY_otc", "AUDCAD_otc",
    "AUDNZD_otc", "EURGBP_otc",

    # Crypto OTC
    "BTCUSD_otc", "ETHUSD_otc", "LTCUSD_otc", "DOGEUSD_otc", "DOGE_otc", 

    # Commodities & Indices
    "XAUUSD_otc", "XAGUSD_otc", "USOIL_otc", "UKOIL_otc", "NAS100_otc",
    "US30_otc", "SPX500_otc",

    # Stocks
    "AAPL_otc", "AMZN_otc", "GOOGL_otc", "TSLA_otc", "MSFT_otc",
    "NVDA_otc", "META_otc", "NFLX_otc",
]

    model_config = ConfigDict(
        env_prefix="PO_",
        env_file=".env",
        extra="ignore"
    )

settings = Settings()