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
    min_required_move_points: float = 5.0
    historical_training_target_trades: int = 1000
    option_premium_history_available: bool = True
    news_lookback_items: int = 20
    finnhub_api_key: str | None = None
    news_provider: str = "upstox"
    # Optimized profiles from high-win optimizer run.
    nifty_opt_min_tqs: int = 72
    nifty_opt_breakout_atr: float = 0.35
    nifty_opt_volume_multiplier: float = 2.0
    nifty_opt_target_points: float = 4.0
    nifty_opt_stop_points: float = 2.5
    nifty_opt_trail_atr: float = 0.75
    nifty_opt_entry_model: str = "breakout"
    sensex_opt_min_tqs: int = 68
    sensex_opt_breakout_atr: float = 0.35
    sensex_opt_volume_multiplier: float = 1.3
    sensex_opt_target_points: float = 6.0
    sensex_opt_stop_points: float = 2.5
    sensex_opt_trail_atr: float = 0.75
    sensex_opt_entry_model: str = "breakout"
    profit_lock_retain_pct: float = 100.0
    profit_target_fallback_pct: float = 11.0
    profit_target_secondary_pct: float = 22.0
    profit_target_primary_pct: float = 33.0
    max_paper_trade_seconds: int = 180
    paper_stop_points: float = 3.0
    paper_target_points: float = 5.0
    ai_learning_enabled: bool = True
    paper_trading: bool = True
    paper_trading_respects_stop: bool = False
    shadow_trade_all_signals: bool = True
    market_poll_seconds: float = 1.0
    websocket_heartbeat_seconds: float = 10.0
    websocket_send_interval_seconds: float = 1.0

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    def instrument_key_for(self, symbol: str) -> str:
        return self.sensex_instrument_key if symbol.upper() == "SENSEX" else self.nifty_instrument_key

    def expiry_for(self, symbol: str) -> str | None:
        return self.sensex_expiry_date if symbol.upper() == "SENSEX" else self.nifty_expiry_date

    def optimized_profile_for(self, symbol: str) -> dict[str, float | int | str]:
        if symbol.upper() == "SENSEX":
            return {
                "symbol": "SENSEX",
                "mode": "runner_profile",
                "executionStyle": "RUNNER_BREAKOUT",
                "holdBias": "extend_winners",
                "partialExitPct": 0.35,
                "runnerPct": 0.65,
                "minTqs": self.sensex_opt_min_tqs,
                "breakoutAtr": self.sensex_opt_breakout_atr,
                "volumeMultiplier": self.sensex_opt_volume_multiplier,
                "targetPoints": self.sensex_opt_target_points,
                "stopPoints": self.sensex_opt_stop_points,
                "trailAtr": self.sensex_opt_trail_atr,
                "entryModel": self.sensex_opt_entry_model,
            }
        return {
            "symbol": "NIFTY",
            "mode": "high_win_scalp_profile",
            "executionStyle": "HIGH_WIN_SCALP",
            "holdBias": "fast_capture",
            "partialExitPct": 0.7,
            "runnerPct": 0.3,
            "minTqs": self.nifty_opt_min_tqs,
            "breakoutAtr": self.nifty_opt_breakout_atr,
            "volumeMultiplier": self.nifty_opt_volume_multiplier,
            "targetPoints": self.nifty_opt_target_points,
            "stopPoints": self.nifty_opt_stop_points,
            "trailAtr": self.nifty_opt_trail_atr,
            "entryModel": self.nifty_opt_entry_model,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
