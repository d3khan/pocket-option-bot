from pathlib import Path
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
import yaml

class PocketOptionConfig(BaseSettings):
    ssid: str = Field(..., alias="PO_SSID")
    is_demo: bool = True

    model_config = SettingsConfigDict(populate_by_name=True)

class AuthConfig(BaseSettings):
    username: str = "d3khan04"
    password_hash: str = ""

class JWTConfig(BaseSettings):
    secret_key: str = Field(..., alias="JWT_SECRET")
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440

    model_config = SettingsConfigDict(populate_by_name=True)

class BotConfig(BaseSettings):
    base_stake: float = 1.0
    multiplier: float = 2.5
    max_stake: float = 16.0
    min_payout: int = 92
    trade_duration: int = 30
    max_consecutive_losses: int = 5
    max_daily_loss: float = 50.0

class WebConfig(BaseSettings):
    port: int = 8000
    session_secret: str = Field(..., alias="SESSION_SECRET")

    model_config = SettingsConfigDict(populate_by_name=True)

class DbConfig(BaseSettings):
    path: str = "/app/data/bot_data.db"

class Settings(BaseSettings):
    pocket_option: PocketOptionConfig = Field(default_factory=PocketOptionConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    jwt: JWTConfig = Field(default_factory=JWTConfig)
    bot: BotConfig = Field(default_factory=BotConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    db: DbConfig = Field(default_factory=DbConfig)
    debug: bool = False

    model_config = SettingsConfigDict(
        env_nested_delimiter="__",
        extra="ignore",
        env_prefix="PO_",
        populate_by_name=True
    )

    @classmethod
    def from_yaml(cls, yaml_path: Path) -> "Settings":
        with open(yaml_path, "r") as f:
            raw = yaml.safe_load(f)
        return cls(**raw)

settings = Settings.from_yaml(Path(__file__).parent.parent.parent / "config" / "settings.yaml")