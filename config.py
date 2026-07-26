import os
from typing import List
from pydantic_settings import BaseSettings
from pydantic import ConfigDict

class Settings(BaseSettings):
    ssid: str = os.getenv("PO_SSID", "your_actual_ssid_here")
    base_stake: float = 1.0
    multiplier: float = 2.5
    max_stake: float = 16
    min_payout: int = 75
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

# Exotic cross pairs
"EURTRY_otc", "GBPTRY_otc", "EURZAR_otc", "GBPZAR_otc", "EURMXN_otc",
"GBPMXN_otc", "EURPLN_otc", "GBPPLN_otc", "EURHUF_otc", "GBPHUF_otc",
"EURNOK_otc", "GBPNOK_otc", "EURSEK_otc", "GBPSEK_otc", "EURCZK_otc",
"GBPCZK_otc", "EURNGN_otc", "GBPNGN_otc", "EURINR_otc", "GBPINR_otc",
"EURPHP_otc", "GBPPHP_otc", "EURIDR_otc", "GBPIDR_otc", "EURKRW_otc",
"GBPKRW_otc", "EURTHB_otc", "GBPTHB_otc", "EURBRL_otc", "GBPBRL_otc",
"EURCOP_otc", "GBPCOP_otc", "EURCLP_otc", "GBPCLP_otc", "EURPEN_otc",
"GBPPEN_otc", "EURARS_otc", "GBPARS_otc",

        # # Crypto OTC
        # "BTCUSD_otc", "ETHUSD_otc", "LTCUSD_otc", "DOGEUSD_otc", "DOGE_otc",
        # "SOLUSD_otc", "TONUSD_otc", "ADAUSD_otc", "XRPUSD_otc", "BNBUSD_otc",
        # "TRXUSD_otc", "DOTUSD_otc", "LINKUSD_otc", "AVAXUSD_otc", "MATICUSD_otc",
        # "BCHUSD_otc", "XLMUSD_otc", "ATOMUSD_otc", "SHIBUSD_otc", "UNIUSD_otc",

        # # Commodities & Indices
        # "XAUUSD_otc", "XAGUSD_otc", "USOIL_otc", "UKOIL_otc", "NAS100_otc",
        # "BRENT_otc", "WTICOIL_otc", "NGAS_otc", "PLATINUM_otc", "PALLADIUM_otc",
        # "DAX40_otc", "FTSE100_otc", "CAC40_otc", "JPN225_otc", "AUS200_otc",
        # "EUSTX50_otc", "HSI50_otc","US30_otc", "SPX500_otc",

        # # Stocks
        # "AAPL_otc", "AMZN_otc", "GOOGL_otc", "TSLA_otc", "MSFT_otc",
        # "AMD_otc", "PLTR_otc", "COIN_otc", "MARA_otc", "BABA_otc",
        # "INTC_otc", "IBM_otc", "ORCL_otc", "PYPL_otc", "V_otc",
        # "MA_otc", "BAC_otc", "JPM_otc", "WMT_otc", "DIS_otc",
        # "UBER_otc", "NKE_otc", "PEP_otc", "KO_otc", "XOM_otc",
        # "NVDA_otc", "META_otc", "NFLX_otc",
    ]

    model_config = ConfigDict(
        env_prefix="PO_",
        env_file=".env",
        extra="ignore"
    )

settings = Settings()
