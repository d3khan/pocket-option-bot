import os
from typing import List
from pydantic_settings import BaseSettings
from pydantic import ConfigDict

class Settings(BaseSettings):
    ssid: str = os.getenv("PO_SSID", "your_actual_ssid_here")
    base_stake: float = 1.0
    multiplier: float = 2.5
    max_stake: float = 16
    min_payout: int = 92
    trade_duration: int = 30
    max_consecutive_losses: int = 4
    max_daily_loss: float = 50.0
    username: str = os.getenv("PO_USERNAME", "d3khan04")
    password: str = os.getenv("PO_PASSWORD", "fake_smile02")
    secret_key: str = os.getenv("SECRET_KEY", "super-secret-key-change-me")
    allowed_assets: List[str] = [
        # Forex OTC
        "EURUSD_otc", "GBPUSD_otc", "USDJPY_otc", "AUDUSD_otc", "NZDUSD_otc",
        "USDCAD_otc", "USDCHF_otc", "EURJPY_otc", "GBPJPY_otc", "AUDCAD_otc",
        "AUDCHF_otc", "CHFJPY_otc", "GBPAUD_otc", "USDZAR_otc", "USDMXN_otc",
        "USDSGD_otc", "USDHKD_otc", "USDNOK_otc", "USDSEK_otc", "USDPLN_otc",
        "USDCNH_otc", "USDHUF_otc", "EURCHF_otc", "EURAUD_otc", "EURNZD_otc",
        "EURCAD_otc", "EURUSD_otc", "GBPCAD_otc", "GBPCHF_otc", "NZDCAD_otc",
        "NZDJPY_otc", "CADJPY_otc", "CHFSGD_otc", "AUDJPY_otc", "AUDNZD_otc",
        "AUDNZD_otc", "EURGBP_otc",

        # Exotic currency pairs
        "NGNUSD_otc", "USDNGN_otc", "USDMXN_otc", "USDZAR_otc", "USDTRY_otc",
        "USDTHB_otc", "USDINR_otc", "USDBRL_otc", "USDCNH_otc", "USDCZK_otc",
        "USDDKK_otc", "USDHKD_otc", "USDHUF_otc", "USDIDR_otc", "USDILS_otc",
        "USDKRW_otc", "USDNOK_otc", "USDPHP_otc", "USDPLN_otc", "USDRUB_otc",
        "USDSEK_otc", "USDSGD_otc", "USDTWD_otc", "USDVND_otc", "USDAED_otc",
        "USDSAR_otc", "USDCOP_otc", "USDCLP_otc", "USDPEN_otc", "USDARS_otc",
    ]

    model_config = ConfigDict(
        env_prefix="PO_",
        env_file=".env",
        extra="ignore"
    )

settings = Settings()
