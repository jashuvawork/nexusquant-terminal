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
    explosive_runner_scan_strikes: int = 24
    explosive_runner_min_score: float = 78.0
    explosive_runner_momentum_min_score: float = 65.0
    explosive_runner_momentum_premium_velocity_pct: float = 4.0
    explosive_runner_elite_min_score: float = 88.0
    explosive_runner_elite_breakout_min: float = 62.0
    explosive_runner_elite_delta_velocity_min: float = 48.0
    explosive_runner_elite_spread_min: float = 70.0
    explosive_runner_premium_min: float = 25.0
    explosive_runner_premium_max: float = 185.0
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
    market_snapshot_instrument_keys: str = (
        "NSE_INDEX|Nifty 50,BSE_INDEX|SENSEX,NSE_INDEX|Nifty Bank,NSE_INDEX|Nifty IT,"
        "NSE_INDEX|Nifty Auto,NSE_INDEX|Nifty FMCG,NSE_INDEX|Nifty Pharma,NSE_INDEX|Nifty Metal,"
        "NSE_INDEX|Nifty Realty,NSE_INDEX|Nifty PSU Bank,NSE_INDEX|Nifty Media,NSE_INDEX|Nifty Energy,"
        "NSE_INDEX|Nifty Next 50,NSE_INDEX|Nifty 100,NSE_INDEX|Nifty Midcap 50,NSE_INDEX|India VIX"
    )
    market_snapshot_monitor_enabled: bool = True
    market_snapshot_poll_seconds: float = 60.0
    # Optimized profiles from high-win optimizer run.
    nifty_opt_min_tqs: int = 74
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
    open_drive_profit_target_fallback_pct: float = 11.0
    open_drive_profit_target_secondary_pct: float = 22.0
    open_drive_profit_target_primary_pct: float = 33.0
    open_drive_profit_stop_pct: float = 33.0
    open_drive_allocation_multiplier: float = 1.85
    max_paper_trade_seconds: int = 240
    paper_duplicate_signal_cooldown_seconds: int = 150
    paper_trade_allocation_pct: float = 25.0
    paper_min_trade_allocation_pct: float = 12.0
    paper_max_daily_loss_pct: float = 5.0
    paper_max_daily_loss_amount: float = 12500.0
    paper_max_consecutive_losses: int = 2
    paper_max_trade_loss_pct: float = 1.0
    paper_max_trade_loss_amount: float = 3000.0
    paper_max_open_trades: int = 2
    paper_max_open_same_side_trades: int = 2
    paper_same_side_entry_cooldown_seconds: int = 600
    paper_same_side_loss_cooldown_seconds: int = 1200
    paper_ai_min_win_probability_pct: float = 65.0
    paper_ai_min_risk_reward: float = 2.0
    paper_ai_min_confidence_pct: float = 75.0
    paper_breadth_filter_enabled: bool = True
    paper_breadth_min_count: int = 2
    paper_breadth_bullish_threshold: float = 60.0
    paper_breadth_bearish_threshold: float = 40.0
    market_breadth_recommended_count: int = 15
    paper_live_readiness_min_trades: int = 100
    paper_live_readiness_min_profit_factor: float = 2.0
    paper_live_readiness_min_win_rate_pct: float = 50.0
    paper_live_readiness_max_drawdown_pct: float = 5.0
    paper_daily_profit_target_amount: float = 50000.0
    paper_daily_profit_target_worst_pct: float = 5.0
    paper_daily_profit_target_medium_pct: float = 8.0
    paper_daily_profit_target_good_pct: float = 10.0
    paper_daily_profit_stop_pct: float = 18.0
    paper_stop_points: float = 6.0
    paper_target_points: float = 12.0
    paper_breakeven_shift_points: float = 9.0
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
    paper_sessions_file: str = "/opt/nexusquant/paper_sessions.jsonl"
    paper_sessions_persist_limit: int = 2000
    paper_trades_file: str = "/opt/nexusquant/paper_trades.json"
    paper_trades_persist_limit: int = 2000
    paper_session_rotation_enabled: bool = False
    paper_single_daily_session: bool = True
    paper_daily_target_lock_enabled: bool = True
    paper_trading: bool = True
    paper_session_adjustments_enabled: bool = True
    paper_high_confidence_only: bool = True
    paper_min_premium_ltp: float = 80.0
    paper_high_confidence_min_runner_score: float = 88.0
    paper_high_confidence_min_tqs: int = 86
    paper_runner_target_premium_pct: float = 35.0
    paper_runner_max_target_premium_pct: float = 55.0
    paper_runner_trail_retain_pct: float = 30.0
    paper_runner_min_hold_seconds: int = 60
    paper_runner_max_hold_seconds: int = 600
    paper_always_trade_explosive_runners: bool = True
    paper_min_hold_before_chop_exit_seconds: int = 45
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
