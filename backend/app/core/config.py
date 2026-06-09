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
    upstox_token_file: str = "/opt/nexusquant/upstox_token.json"
    primary_symbol: str = "NIFTY"
    nifty_instrument_key: str = "NSE_INDEX|Nifty 50"
    sensex_instrument_key: str = "BSE_INDEX|SENSEX"
    nifty_expiry_date: str | None = None
    sensex_expiry_date: str | None = None
    enable_live_trading: bool = False
    aggressive_mode: bool = False
    aggression_profile: str = "balanced_pro"
    ai_score_threshold: int = 82
    safe_mode_threshold: int = 86
    max_exposure_pct: int = 100
    daily_drawdown_pct: float = 3.0
    trading_capital_default: float = 500000.0
    min_required_move_points: float = 5.0
    historical_training_target_trades: int = 1000
    option_premium_history_available: bool = True
    explosive_runner_enabled: bool = True
    explosive_runner_scan_strikes: int = 16
    explosive_runner_min_score: float = 85.0
    explosive_runner_premium_min: float = 25.0
    explosive_runner_premium_max: float = 250.0
    background_market_monitor_enabled: bool = True
    news_lookback_items: int = 20
    news_cache_ttl_seconds: float = 300.0
    news_timeout_seconds: float = 3.0
    finnhub_api_key: str | None = None
    news_provider: str = "finnhub"
    upstox_news_enabled: bool = False
    snapshot_cache_seconds: float = 5.0
    account_snapshot_cache_seconds: float = 30.0
    expiry_cache_seconds: float = 3600.0
    market_snapshot_instrument_keys: str = "NSE_INDEX|Nifty 50,BSE_INDEX|SENSEX"
    market_snapshot_monitor_enabled: bool = True
    market_snapshot_poll_seconds: float = 60.0
    # Optimized profiles from high-win optimizer run.
    nifty_opt_min_tqs: int = 78
    nifty_opt_breakout_atr: float = 0.35
    nifty_opt_volume_multiplier: float = 2.0
    nifty_opt_target_points: float = 15.0
    nifty_opt_stop_points: float = 7.0
    nifty_opt_trail_atr: float = 0.65
    nifty_opt_entry_model: str = "breakout"
    sensex_opt_min_tqs: int = 74
    sensex_opt_breakout_atr: float = 0.35
    sensex_opt_volume_multiplier: float = 1.3
    sensex_opt_target_points: float = 20.0
    sensex_opt_stop_points: float = 10.0
    sensex_opt_trail_atr: float = 0.7
    sensex_opt_entry_model: str = "breakout"
    profit_lock_retain_pct: float = 100.0
    profit_target_fallback_pct: float = 6.0
    profit_target_secondary_pct: float = 12.0
    profit_target_primary_pct: float = 18.0
    max_paper_trade_seconds: int = 180
    paper_duplicate_signal_cooldown_seconds: int = 300
    paper_trade_allocation_pct: float = 12.0
    paper_min_trade_allocation_pct: float = 8.0
    paper_max_daily_loss_pct: float = 2.0
    paper_max_consecutive_losses: int = 3
    paper_daily_profit_stop_pct: float = 18.0
    paper_stop_points: float = 6.0
    paper_target_points: float = 12.0
    paper_breakeven_shift_points: float = 6.0
    option_brokerage_per_order: float = 20.0
    option_stt_sell_pct: float = 0.0625
    option_exchange_txn_pct: float = 0.03503
    option_sebi_pct: float = 0.0001
    option_stamp_buy_pct: float = 0.003
    option_gst_pct: float = 18.0
    ai_learning_enabled: bool = True
    ai_state_file: str = "/opt/nexusquant/ai_state.json"
    paper_replay_file: str = "/opt/nexusquant/paper_replay.jsonl"
    paper_replay_persist_limit: int = 20000
    paper_trading: bool = True
    paper_trading_respects_stop: bool = False
    shadow_trade_all_signals: bool = False
    market_poll_seconds: float = 5.0
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

    @property
    def market_snapshot_instrument_list(self) -> list[str]:
        return [item.strip() for item in self.market_snapshot_instrument_keys.split(",") if item.strip()]

    def optimized_profile_for(self, symbol: str) -> dict[str, float | int | str]:
        if symbol.upper() == "SENSEX":
            return {
                "symbol": "SENSEX",
                "mode": "runner_profile",
                "executionStyle": "RUNNER_BREAKOUT",
                "holdBias": "extend_winners",
                "partialExitPct": 0.6,
                "runnerPct": 0.4,
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
            "holdBias": "adaptive_momentum_capture",
            "partialExitPct": 0.75,
            "runnerPct": 0.25,
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
