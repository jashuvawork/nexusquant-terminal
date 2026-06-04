from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "NexusQuant Institutional Terminal API"
    environment: str = "development"
    cors_origins: str = "http://localhost:5173,https://localhost:5173"
    database_url: str = "postgresql://postgres:postgres@localhost:5432/nexusquant"
    redis_url: str = "redis://localhost:6379/0"
    upstox_api_key: str | None = None
    upstox_api_secret: str | None = None
    upstox_redirect_uri: str | None = None
    upstox_access_token: str | None = None
    primary_symbol: str = "NIFTY"
    nifty_instrument_key: str = "NSE_INDEX|Nifty 50"
    sensex_instrument_key: str = "BSE_INDEX|SENSEX"
    nifty_expiry_date: str | None = None
    sensex_expiry_date: str | None = None
    enable_live_trading: bool = False
    aggressive_mode: bool = False
    aggression_profile: str = "realistic_aggressive"
    ai_score_threshold: int = 76
    safe_mode_threshold: int = 86
    max_exposure_pct: int = 42
    daily_drawdown_pct: float = 3.0
    trading_capital_default: float = 0.0
    market_poll_seconds: float = 1.0

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    def instrument_key_for(self, symbol: str) -> str:
        return self.sensex_instrument_key if symbol.upper() == "SENSEX" else self.nifty_instrument_key

    def expiry_for(self, symbol: str) -> str | None:
        return self.sensex_expiry_date if symbol.upper() == "SENSEX" else self.nifty_expiry_date


@lru_cache
def get_settings() -> Settings:
    return Settings()
