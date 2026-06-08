from __future__ import annotations

import asyncio
import os

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.services.ai_engine import TradeQualityScorer
from app.services.auto_trader import AutoTraderEngine
from app.services.ai_learning import ContinuousAILearner
from app.services.event_journal import EventJournal
from app.services.historical_trainer import HistoricalTrainer
from app.services.institutional_readiness import InstitutionalReadinessEngine
from app.services.ltp_range_analyzer import LtpRangeAnalyzer
from app.services.market_movers import summarize_market_movers
from app.services.news_engine import NewsEngine
from app.services.news_provider import NewsProvider
from app.services.realtime_engine import MarketConfigurationError, RealTimeMarketEngine
from app.services.risk_engine import RiskEngine
from app.services.risk_profiles import profile_list
from app.services.option_premium_optimizer import OptionPremiumOptimizer
from app.services.strategy_optimizer import StrategyOptimizer
from app.services.session import current_session_state
from app.services.trading_control import TradingControl
from app.services.upstox_auth import UpstoxAuthError, UpstoxAuthService
from app.services.upstox_client import UpstoxAuthRequired, UpstoxClient, UpstoxDataError

router = APIRouter(prefix="/api", tags=["terminal"])
alias_router = APIRouter(tags=["upstox-aliases"])
_auto_trader_instance: AutoTraderEngine | None = None
_market_engine_instance: RealTimeMarketEngine | None = None


class ScalpOrderRequest(BaseModel):
    instrument_token: str = Field(..., description="Upstox instrument key for the selected option contract")
    quantity: int = Field(..., gt=0)
    transaction_type: Literal["BUY", "SELL"] = "BUY"
    order_type: Literal["LIMIT", "MARKET"] = "LIMIT"
    price: float = Field(..., ge=0)
    market_protection: int = Field(0, ge=0, le=100)
    tag: str = "nexusquant-scalp"


class TradingControlRequest(BaseModel):
    reason: str = "Manual operator action"


class TradingCapitalRequest(BaseModel):
    amount: float = Field(..., ge=0)
    reason: str = "Capital updated from NexusQuant terminal"


class RiskProfileRequest(BaseModel):
    profile: str = Field(..., description="safe_beginner, balanced_pro, aggressive_scalping, extreme_prop, realistic_aggressive")


class EventRecordRequest(BaseModel):
    event_type: str
    message: str
    symbol: str | None = None
    severity: str = "INFO"
    payload: dict = Field(default_factory=dict)


def get_upstox_auth(settings: Settings = Depends(get_settings)) -> UpstoxAuthService:
    return UpstoxAuthService(
        api_key=settings.upstox_api_key,
        api_secret=settings.upstox_api_secret,
        redirect_uri=settings.upstox_redirect_uri,
        redis_url=settings.redis_url,
        access_token=settings.upstox_access_token,
        token_file=settings.upstox_token_file,
    )


def get_upstox(
    settings: Settings = Depends(get_settings),
    auth_service: UpstoxAuthService = Depends(get_upstox_auth),
) -> UpstoxClient:
    return UpstoxClient(settings.upstox_api_key, settings.upstox_api_secret, auth_service)


def get_trading_control(settings: Settings = Depends(get_settings)) -> TradingControl:
    return TradingControl(settings.redis_url)


def get_event_journal(settings: Settings = Depends(get_settings)) -> EventJournal:
    return EventJournal(settings.database_url)


def get_ai_learner(settings: Settings = Depends(get_settings)) -> ContinuousAILearner:
    return ContinuousAILearner(settings.redis_url, settings.ai_learning_enabled, settings.ai_state_file)


def get_auto_trader(
    settings: Settings = Depends(get_settings),
    control: TradingControl = Depends(get_trading_control),
    learner: ContinuousAILearner = Depends(get_ai_learner),
) -> AutoTraderEngine:
    global _auto_trader_instance
    if _auto_trader_instance is None:
        _auto_trader_instance = AutoTraderEngine(settings, control, learner)
    return _auto_trader_instance


def get_historical_trainer(
    settings: Settings = Depends(get_settings),
    client: UpstoxClient = Depends(get_upstox),
    learner: ContinuousAILearner = Depends(get_ai_learner),
) -> HistoricalTrainer:
    return HistoricalTrainer(settings, client, learner)


def get_strategy_optimizer(settings: Settings = Depends(get_settings), client: UpstoxClient = Depends(get_upstox)) -> StrategyOptimizer:
    return StrategyOptimizer(settings, client)


def get_option_premium_optimizer(settings: Settings = Depends(get_settings), client: UpstoxClient = Depends(get_upstox)) -> OptionPremiumOptimizer:
    return OptionPremiumOptimizer(settings, client)


def get_market_engine(
    settings: Settings = Depends(get_settings),
    client: UpstoxClient = Depends(get_upstox),
    trading_control: TradingControl = Depends(get_trading_control),
) -> RealTimeMarketEngine:
    global _market_engine_instance
    if _market_engine_instance is None:
        scorer = TradeQualityScorer()
        risk_engine = RiskEngine(settings.ai_score_threshold, settings.safe_mode_threshold, settings.max_exposure_pct)
        _market_engine_instance = RealTimeMarketEngine(settings, client, scorer, risk_engine, trading_control)
    return _market_engine_instance


@router.get("/terminal/state")
async def terminal_state(settings: Settings = Depends(get_settings)) -> dict[str, str | bool | float | None]:
    return {
        "pipeline": "Upstox token -> option chain -> market quote -> intraday candles -> risk gates -> execution router",
        "symbols": "NIFTY,SENSEX",
        "mode": "real-upstox-data-only",
        "primarySymbol": settings.primary_symbol,
        "niftyExpiryDate": settings.nifty_expiry_date,
        "sensexExpiryDate": settings.sensex_expiry_date,
        "liveTradingEnabled": settings.enable_live_trading,
        "aggressiveMode": settings.aggressive_mode,
        "marketPollSeconds": settings.market_poll_seconds,
    }


@router.get("/deployment/status")
async def deployment_status(
    settings: Settings = Depends(get_settings),
    auth_service: UpstoxAuthService = Depends(get_upstox_auth),
    engine: RealTimeMarketEngine = Depends(get_market_engine),
) -> dict:
    token_status = await auth_service.token_status()
    return {
        "service": settings.app_name,
        "apiVersion": "0.9.4-option-premium-optimizer",
        "runtimeValidation": engine.validate_runtime(),
        "optimizerValidation": get_strategy_optimizer(settings, get_upstox(settings, auth_service)).validate_runtime(),
        "environment": settings.environment,
        "railwayCommit": os.getenv("RAILWAY_GIT_COMMIT_SHA"),
        "railwayService": os.getenv("RAILWAY_SERVICE_NAME"),
        "upstoxConfigured": token_status["configured"],
        "upstoxTokenPresent": token_status["hasToken"],
        "upstoxTokenSource": token_status.get("source"),
        "routes": [
            "/health",
            "/api/upstox/login-url",
            "/api/upstox/callback",
            "/api/upstox/token/status",
            "/api/upstox/token/diagnostics",
            "/api/upstox/account-summary",
            "/api/market/expiries/NIFTY",
            "/api/market/snapshot/NIFTY",
            "/api/market/snapshots",
            "/api/market/movers",
            "/api/institutional/readiness/NIFTY",
            "/api/market/news/NIFTY",
            "/api/auto-trader/status",
            "/api/auto-trader/reset",
            "/api/ai-learning/status",
            "/api/ai-learning/export",
            "/api/ai-learning/reset",
            "/api/ai-learning/train-historical",
            "/api/ai-learning/train-now",
            "/api/ai-learning/train-runner",
            "/api/ai-learning/train-runner-both",
            "/api/ai-learning/train-option-runner",
            "/api/ai-learning/train-option-runner-both",
            "/api/strategy-optimizer/run",
            "/api/strategy-optimizer/run-both",
            "/api/strategy-optimizer/latest",
            "/api/analytics/ltp-ranges",
            "/api/option-premium-optimizer/run",
            "/api/option-premium-optimizer/run-both",
            "/api/event-journal/recent",
        ],
    }


@router.get("/market/snapshots")
async def market_snapshots(engine: RealTimeMarketEngine = Depends(get_market_engine), auto_engine: AutoTraderEngine = Depends(get_auto_trader)) -> dict:
    results = await asyncio.gather(
        engine.snapshot("NIFTY"),
        engine.snapshot("SENSEX"),
        return_exceptions=True,
    )
    snapshots = {}
    errors = {}
    for symbol, result in zip(["NIFTY", "SENSEX"], results, strict=True):
        if isinstance(result, Exception):
            errors[symbol] = str(result)
        else:
            snapshots[symbol] = result
    if not snapshots:
        raise HTTPException(status_code=503, detail=errors)
    candidates = []
    for symbol, snapshot in snapshots.items():
        for trade in snapshot.get("suggestedTrades") or []:
            candidates.append({"symbol": symbol, **trade})
    payload = {"type": "multi_snapshot", "snapshots": snapshots, "symbolErrors": errors, "executionCandidates": candidates}
    session_state = current_session_state()
    if session_state.phase == "LIVE_MARKET":
        payload["autoTrader"] = await auto_engine.process(payload)
    else:
        payload["autoTrader"] = {**auto_engine.status(), "processingPaused": True, "pauseReason": "Market is closed; replay/learning mutations paused."}
    return payload


@router.get("/market/news/{symbol}")
async def market_news(symbol: Literal["NIFTY", "SENSEX"], settings: Settings = Depends(get_settings), client: UpstoxClient = Depends(get_upstox)) -> dict:
    external = await NewsProvider(settings).fetch(symbol)
    upstox_payload = None
    upstox_error = None
    use_upstox_news = settings.upstox_news_enabled or settings.news_provider.lower().strip() == "upstox"
    if use_upstox_news and not external.get("data"):
        try:
            upstox_payload = await client.news_headlines(settings.instrument_key_for(symbol))
        except (UpstoxAuthRequired, UpstoxDataError) as exc:
            upstox_error = str(exc)
    payload = external if external.get("data") else upstox_payload
    reason = None if external.get("data") or upstox_payload else external.get("reason") or upstox_error
    state = NewsEngine().analyze(payload, reason)
    state["providerStatus"] = {"primary": settings.news_provider, "external": external, "upstox": {"enabled": use_upstox_news, "available": bool(upstox_payload), "error": upstox_error}}
    return state


@router.get("/market/movers")
async def market_movers(settings: Settings = Depends(get_settings), client: UpstoxClient = Depends(get_upstox)) -> dict:
    instruments = settings.market_snapshot_instrument_list
    if not instruments:
        raise HTTPException(status_code=400, detail="MARKET_SNAPSHOT_INSTRUMENT_KEYS is empty.")
    try:
        payload = await client.full_market_quote(instruments)
    except (UpstoxAuthRequired, UpstoxDataError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return summarize_market_movers(instruments, payload)


@router.get("/institutional/readiness/{symbol}")
async def institutional_readiness(symbol: Literal["NIFTY", "SENSEX"], engine: RealTimeMarketEngine = Depends(get_market_engine), auto_engine: AutoTraderEngine = Depends(get_auto_trader)) -> dict:
    try:
        snapshot = await engine.snapshot(symbol)
        snapshot["autoTrader"] = auto_engine.status()
        return InstitutionalReadinessEngine().score_snapshot(snapshot)
    except (UpstoxAuthRequired, UpstoxDataError, MarketConfigurationError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/market/snapshot/{symbol}")
async def market_snapshot(symbol: Literal["NIFTY", "SENSEX"], engine: RealTimeMarketEngine = Depends(get_market_engine)) -> dict:
    try:
        return await engine.snapshot(symbol)
    except (UpstoxAuthRequired, UpstoxDataError, MarketConfigurationError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/upstox/login-url")
async def upstox_login_url(auth_service: UpstoxAuthService = Depends(get_upstox_auth)) -> dict[str, str]:
    try:
        return {"loginUrl": auth_service.login_url()}
    except UpstoxAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/upstox/callback")
async def upstox_callback(
    code: str = Query(..., description="Authorization code returned by Upstox"),
    auth_service: UpstoxAuthService = Depends(get_upstox_auth),
) -> dict:
    try:
        token_meta = await auth_service.exchange_code(code)
    except UpstoxAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "message": "Upstox access token stored successfully. You can close this tab and return to NexusQuant.",
        **token_meta,
    }


@router.get("/upstox/token/status")
async def upstox_token_status(auth_service: UpstoxAuthService = Depends(get_upstox_auth)) -> dict:
    return await auth_service.token_status()


@router.get("/upstox/token/diagnostics")
async def upstox_token_diagnostics(settings: Settings = Depends(get_settings), auth_service: UpstoxAuthService = Depends(get_upstox_auth)) -> dict:
    status = await auth_service.token_status()
    return {
        "apiKeyConfigured": bool(settings.upstox_api_key),
        "apiSecretConfigured": bool(settings.upstox_api_secret),
        "redirectUriConfigured": bool(settings.upstox_redirect_uri),
        "envAccessTokenConfigured": bool(settings.upstox_access_token),
        "tokenFile": settings.upstox_token_file,
        "tokenStatus": status,
        "note": "Token value is intentionally not returned.",
    }


@router.get("/upstox/account-summary")
async def upstox_account_summary(client: UpstoxClient = Depends(get_upstox)) -> dict:
    try:
        return {
            "health": await client.health(),
            "funds": await client.funds(),
            "positions": await client.positions(),
            "orders": await client.orders(),
        }
    except (UpstoxAuthRequired, UpstoxDataError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/upstox/health")
async def upstox_health(client: UpstoxClient = Depends(get_upstox)) -> dict:
    return await client.health()


@router.get("/upstox/portfolio")
async def upstox_portfolio(client: UpstoxClient = Depends(get_upstox)) -> dict:
    try:
        return {"funds": await client.funds(), "positions": await client.positions(), "orders": await client.orders()}
    except (UpstoxAuthRequired, UpstoxDataError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/market/expiries/{symbol}")
async def market_expiries(symbol: Literal["NIFTY", "SENSEX"], engine: RealTimeMarketEngine = Depends(get_market_engine), settings: Settings = Depends(get_settings)) -> dict:
    try:
        return await engine.resolve_expiry(symbol, settings.instrument_key_for(symbol), [])
    except (UpstoxAuthRequired, UpstoxDataError, MarketConfigurationError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/upstox/option-chain/{symbol}")
async def option_chain(symbol: Literal["NIFTY", "SENSEX"], settings: Settings = Depends(get_settings), client: UpstoxClient = Depends(get_upstox), engine: RealTimeMarketEngine = Depends(get_market_engine)) -> dict:
    try:
        expiry_state = await engine.resolve_expiry(symbol, settings.instrument_key_for(symbol), [])
        payload = await client.option_chain(settings.instrument_key_for(symbol), expiry_state["selectedExpiry"])
        return {"expiryState": expiry_state, **payload}
    except (UpstoxAuthRequired, UpstoxDataError, MarketConfigurationError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/capital")
async def capital_status(control: TradingControl = Depends(get_trading_control), settings: Settings = Depends(get_settings)) -> dict:
    status = await control.capital_status()
    if status["tradingCapital"] <= 0 and settings.trading_capital_default > 0:
        return {**status, "tradingCapital": settings.trading_capital_default, "source": "TRADING_CAPITAL_DEFAULT"}
    return {**status, "source": "runtime"}


@router.post("/capital")
async def set_capital(request: TradingCapitalRequest, control: TradingControl = Depends(get_trading_control)) -> dict:
    return await control.set_capital(request.amount, request.reason)


@router.get("/execution/status")
async def execution_status(control: TradingControl = Depends(get_trading_control), settings: Settings = Depends(get_settings)) -> dict:
    status = await control.status()
    capital = await control.capital_status()
    if capital["tradingCapital"] <= 0 and settings.trading_capital_default > 0:
        capital = {**capital, "tradingCapital": settings.trading_capital_default, "source": "TRADING_CAPITAL_DEFAULT"}
    else:
        capital = {**capital, "source": "runtime"}
    return {
        **status,
        "capital": capital,
        "liveTradingEnabled": settings.enable_live_trading,
        "aggressiveMode": settings.aggressive_mode,
    }


@router.post("/execution/stop")
async def stop_execution(request: TradingControlRequest | None = None, control: TradingControl = Depends(get_trading_control)) -> dict:
    reason = request.reason if request else "Manual emergency stop"
    return await control.stop(reason)


@router.post("/execution/resume")
async def resume_execution(request: TradingControlRequest | None = None, control: TradingControl = Depends(get_trading_control)) -> dict:
    reason = request.reason if request else "Manual resume"
    return await control.resume(reason)


@router.get("/execution/stop")
async def stop_execution_get(control: TradingControl = Depends(get_trading_control)) -> dict:
    return await control.stop("Manual emergency stop via GET")


@router.get("/execution/stop-now")
async def stop_execution_now(control: TradingControl = Depends(get_trading_control)) -> dict:
    return await control.stop("Manual emergency stop via browser link")


@router.get("/execution/resume")
async def resume_execution_get(control: TradingControl = Depends(get_trading_control)) -> dict:
    return await control.resume("Manual resume via GET")


@router.get("/execution/resume-now")
async def resume_execution_now(control: TradingControl = Depends(get_trading_control)) -> dict:
    return await control.resume("Manual resume via browser link")


@router.post("/execution/scalp-order")
async def place_scalp_order(
    request: ScalpOrderRequest,
    settings: Settings = Depends(get_settings),
    client: UpstoxClient = Depends(get_upstox),
    control: TradingControl = Depends(get_trading_control),
    auto_engine: AutoTraderEngine = Depends(get_auto_trader),
) -> dict:
    session = current_session_state()
    control_status = await control.status()
    if control_status.get("autoTradingStopped"):
        raise HTTPException(status_code=423, detail=f"Auto trading stopped: {control_status.get('reason') or 'manual stop'}")
    if not settings.enable_live_trading:
        raise HTTPException(status_code=403, detail="Live trading is disabled. Set ENABLE_LIVE_TRADING=true only after paper checks and risk approval.")
    if not session.execution_allowed:
        raise HTTPException(status_code=403, detail=f"Execution blocked: {session.label}. {session.reason}")
    if request.order_type == "MARKET" and request.market_protection <= 0:
        raise HTTPException(status_code=400, detail="Aggressive MARKET orders require market_protection > 0.")
    capital_status_value = await control.capital_status()
    trading_capital = capital_status_value.get("tradingCapital") or settings.trading_capital_default
    estimated_value = request.quantity * request.price if request.price > 0 else 0
    if trading_capital and estimated_value and estimated_value > trading_capital:
        raise HTTPException(status_code=403, detail=f"Order value {estimated_value} exceeds configured trading capital {trading_capital}.")
    profit_lock = auto_engine.profit_lock_status(float(trading_capital or 0))
    if profit_lock.get("blockNewTrades"):
        raise HTTPException(status_code=423, detail=f"Profit lock active: {profit_lock.get('message')}")

    order = {
        "quantity": request.quantity,
        "product": "I",
        "validity": "IOC" if settings.aggressive_mode else "DAY",
        "price": request.price if request.order_type == "LIMIT" else 0,
        "tag": request.tag,
        "instrument_token": request.instrument_token,
        "order_type": request.order_type,
        "transaction_type": request.transaction_type,
        "disclosed_quantity": 0,
        "trigger_price": 0,
        "is_amo": False,
        "market_protection": request.market_protection,
    }
    try:
        response = await client.place_order(order)
    except (UpstoxAuthRequired, UpstoxDataError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"submitted": True, "session": session.label, "order": order, "upstox": response}


@router.get("/event-journal/recent")
async def event_journal_recent(limit: int = 100, journal: EventJournal = Depends(get_event_journal)) -> dict:
    return {"events": await journal.recent(limit), "limit": limit}


@router.post("/event-journal/record")
async def event_journal_record(request: EventRecordRequest, journal: EventJournal = Depends(get_event_journal)) -> dict:
    event = await journal.record(
        request.event_type,
        request.message,
        symbol=request.symbol,
        severity=request.severity,
        payload=request.payload,
    )
    return {"event": event}


@router.get("/strategy-optimizer/latest")
async def strategy_optimizer_latest(optimizer: StrategyOptimizer = Depends(get_strategy_optimizer)) -> dict:
    return await optimizer.latest()


@router.get("/option-premium-optimizer/run")
async def option_premium_optimizer_run(
    symbol: Literal["NIFTY", "SENSEX"] = "NIFTY",
    target_samples: int = 500,
    expiry_date: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    max_contracts: int = 40,
    max_param_sets: int = 120,
    objective: str = "high_win_scalp",
    optimizer: OptionPremiumOptimizer = Depends(get_option_premium_optimizer),
) -> dict:
    try:
        return await optimizer.optimize(symbol, target_samples, expiry_date, from_date, to_date, max_contracts, max_param_sets, objective)
    except (UpstoxAuthRequired, UpstoxDataError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/option-premium-optimizer/run-both")
async def option_premium_optimizer_run_both(
    target_samples: int = 500,
    from_date: str | None = None,
    to_date: str | None = None,
    max_contracts: int = 40,
    max_param_sets: int = 120,
    objective: str = "high_win_scalp",
    optimizer: OptionPremiumOptimizer = Depends(get_option_premium_optimizer),
) -> dict:
    results = {}
    errors = {}
    for symbol in ["NIFTY", "SENSEX"]:
        try:
            results[symbol] = await optimizer.optimize(symbol, target_samples, None, from_date, to_date, max_contracts, max_param_sets, objective)
        except (UpstoxAuthRequired, UpstoxDataError) as exc:
            errors[symbol] = str(exc)
    if not results:
        raise HTTPException(status_code=503, detail=errors)
    return {"results": results, "errors": errors}


@router.get("/analytics/ltp-ranges")
async def analytics_ltp_ranges(
    target_trades: int = 10000,
    train: bool = False,
    settings: Settings = Depends(get_settings),
    client: UpstoxClient = Depends(get_upstox),
    engine: RealTimeMarketEngine = Depends(get_market_engine),
    trainer: HistoricalTrainer = Depends(get_historical_trainer),
) -> dict:
    analyzer = LtpRangeAnalyzer()
    results = {}
    errors = {}
    for symbol in ["NIFTY", "SENSEX"]:
        try:
            expiry_state = await engine.resolve_expiry(symbol, settings.instrument_key_for(symbol), [])
            chain = await client.option_chain(settings.instrument_key_for(symbol), expiry_state["selectedExpiry"])
            current_ranges = analyzer.analyze_option_chain(
                chain,
                capital=settings.trading_capital_default,
                max_exposure_pct=settings.max_exposure_pct,
                premium_min=settings.explosive_runner_premium_min,
                premium_max=settings.explosive_runner_premium_max,
            )
            training = None
            if train:
                training = await trainer.train(symbol, target_trades)
            results[symbol] = {"expiryState": expiry_state, "currentPremiumRanges": current_ranges, "historicalTraining": training}
        except Exception as exc:
            errors[symbol] = str(exc)
    return {
        "available": bool(results),
        "targetTrades": target_trades,
        "trained": train,
        "results": results,
        "errors": errors,
        "note": "Best premium LTP range uses current option-chain LTP. Historical training uses real Upstox index candles unless exact option premium history is available. If Upstox rate-limits this request, errors are returned without failing the whole website.",
    }


@router.get("/strategy-optimizer/run")
async def strategy_optimizer_run(
    symbol: Literal["NIFTY", "SENSEX"] = "NIFTY",
    target_samples: int = 1000,
    from_date: str | None = None,
    to_date: str | None = None,
    max_param_sets: int = 96,
    objective: str = "balanced",
    optimizer: StrategyOptimizer = Depends(get_strategy_optimizer),
) -> dict:
    try:
        return await optimizer.optimize(symbol, target_samples, from_date, to_date, 1, max_param_sets, objective)
    except (UpstoxAuthRequired, UpstoxDataError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/strategy-optimizer/run-both")
async def strategy_optimizer_run_both(
    target_samples: int = 1000,
    from_date: str | None = None,
    to_date: str | None = None,
    max_param_sets: int = 96,
    objective: str = "balanced",
    optimizer: StrategyOptimizer = Depends(get_strategy_optimizer),
) -> dict:
    results = {}
    errors = {}
    for symbol in ["NIFTY", "SENSEX"]:
        try:
            results[symbol] = await optimizer.optimize(symbol, target_samples, from_date, to_date, 1, max_param_sets, objective)
        except (UpstoxAuthRequired, UpstoxDataError) as exc:
            errors[symbol] = str(exc)
    if not results:
        raise HTTPException(status_code=503, detail=errors)
    return {"results": results, "errors": errors}


@router.post("/ai-learning/train-now")
async def ai_learning_train_now(
    target_trades: int | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    trainer: HistoricalTrainer = Depends(get_historical_trainer),
) -> dict:
    target = target_trades or 1000
    results: dict[str, dict] = {}
    errors: dict[str, str] = {}
    for symbol in ["NIFTY", "SENSEX"]:
        try:
            results[symbol] = await trainer.train(symbol, target, from_date, to_date)
        except (UpstoxAuthRequired, UpstoxDataError) as exc:
            errors[symbol] = str(exc)
    if not results:
        raise HTTPException(status_code=503, detail=errors)
    return {"targetTradesPerSymbol": target, "results": results, "errors": errors}


@router.get("/ai-learning/train-now")
async def ai_learning_train_now_get(
    target_trades: int | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    trainer: HistoricalTrainer = Depends(get_historical_trainer),
) -> dict:
    return await ai_learning_train_now(target_trades, from_date, to_date, trainer)


@router.get("/ai-learning/train-option-runner")
async def ai_learning_train_option_runner(
    symbol: Literal["NIFTY", "SENSEX"] = "NIFTY",
    target_trades: int | None = None,
    expiry_date: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    max_contracts: int = 60,
    trainer: HistoricalTrainer = Depends(get_historical_trainer),
) -> dict:
    try:
        return await trainer.train_option_runner(symbol, target_trades, expiry_date, from_date, to_date, 1, max_contracts)
    except (UpstoxAuthRequired, UpstoxDataError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/ai-learning/train-option-runner-both")
async def ai_learning_train_option_runner_both(
    target_trades: int | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    max_contracts: int = 60,
    trainer: HistoricalTrainer = Depends(get_historical_trainer),
) -> dict:
    results = {}
    errors = {}
    for symbol in ["NIFTY", "SENSEX"]:
        try:
            results[symbol] = await trainer.train_option_runner(symbol, target_trades, None, from_date, to_date, 1, max_contracts)
        except (UpstoxAuthRequired, UpstoxDataError) as exc:
            errors[symbol] = str(exc)
    if not results:
        raise HTTPException(status_code=503, detail=errors)
    return {"results": results, "errors": errors}


@router.get("/ai-learning/train-explosive-high-profit")
async def ai_learning_train_explosive_high_profit(
    symbol: Literal["NIFTY", "SENSEX"] = "NIFTY",
    target_trades: int | None = 500,
    expiry_date: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    max_contracts: int = 80,
    trainer: HistoricalTrainer = Depends(get_historical_trainer),
) -> dict:
    try:
        return await trainer.train_option_runner(symbol, target_trades, expiry_date, from_date, to_date, 1, max_contracts, high_profit_only=True)
    except (UpstoxAuthRequired, UpstoxDataError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/ai-learning/train-explosive-high-profit-both")
async def ai_learning_train_explosive_high_profit_both(
    target_trades: int | None = 500,
    from_date: str | None = None,
    to_date: str | None = None,
    max_contracts: int = 80,
    trainer: HistoricalTrainer = Depends(get_historical_trainer),
) -> dict:
    results = {}
    errors = {}
    for symbol in ["NIFTY", "SENSEX"]:
        try:
            results[symbol] = await trainer.train_option_runner(symbol, target_trades, None, from_date, to_date, 1, max_contracts, high_profit_only=True)
        except (UpstoxAuthRequired, UpstoxDataError) as exc:
            errors[symbol] = str(exc)
    if not results:
        raise HTTPException(status_code=503, detail=errors)
    return {"results": results, "errors": errors}


@router.get("/ai-learning/train-replay-missed")
async def ai_learning_train_replay_missed(
    target_trades: int = 500,
    horizon_ticks: int = 60,
    min_profit_points: float = 8.0,
    include_losses: bool = True,
    auto_engine: AutoTraderEngine = Depends(get_auto_trader),
) -> dict:
    return await auto_engine.train_replay_opportunities(target_trades, horizon_ticks, min_profit_points, include_losses)


@router.get("/ai-learning/train-runner")
async def ai_learning_train_runner(
    symbol: Literal["NIFTY", "SENSEX"] = "NIFTY",
    target_trades: int | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    trainer: HistoricalTrainer = Depends(get_historical_trainer),
) -> dict:
    try:
        return await trainer.train_runner(symbol, target_trades, from_date, to_date)
    except (UpstoxAuthRequired, UpstoxDataError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/ai-learning/train-runner-both")
async def ai_learning_train_runner_both(
    target_trades: int | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    trainer: HistoricalTrainer = Depends(get_historical_trainer),
) -> dict:
    results = {}
    errors = {}
    for symbol in ["NIFTY", "SENSEX"]:
        try:
            results[symbol] = await trainer.train_runner(symbol, target_trades, from_date, to_date)
        except (UpstoxAuthRequired, UpstoxDataError) as exc:
            errors[symbol] = str(exc)
    if not results:
        raise HTTPException(status_code=503, detail=errors)
    return {"results": results, "errors": errors}


@router.post("/ai-learning/train-historical")
async def ai_learning_train_historical(
    symbol: Literal["NIFTY", "SENSEX"] = "NIFTY",
    target_trades: int | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    trainer: HistoricalTrainer = Depends(get_historical_trainer),
) -> dict:
    try:
        return await trainer.train(symbol, target_trades, from_date, to_date)
    except (UpstoxAuthRequired, UpstoxDataError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/ai-learning/train-historical")
async def ai_learning_train_historical_get(
    symbol: Literal["NIFTY", "SENSEX"] = "NIFTY",
    target_trades: int | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    trainer: HistoricalTrainer = Depends(get_historical_trainer),
) -> dict:
    return await ai_learning_train_historical(symbol, target_trades, from_date, to_date, trainer)


@router.get("/ai-learning/status")
async def ai_learning_status(learner: ContinuousAILearner = Depends(get_ai_learner)) -> dict:
    return await learner.status()


@router.get("/ai-learning/export")
async def ai_learning_export(learner: ContinuousAILearner = Depends(get_ai_learner)) -> dict:
    return await learner.export_state()


@router.post("/ai-learning/reset")
async def ai_learning_reset(learner: ContinuousAILearner = Depends(get_ai_learner)) -> dict:
    return await learner.reset()


@router.get("/ai-learning/reset")
async def ai_learning_reset_get(learner: ContinuousAILearner = Depends(get_ai_learner)) -> dict:
    return await learner.reset()


@router.get("/auto-trader/status")
async def auto_trader_status(engine: AutoTraderEngine = Depends(get_auto_trader)) -> dict:
    return engine.status()


@router.get("/auto-trader/profit-lock")
async def auto_trader_profit_lock(engine: AutoTraderEngine = Depends(get_auto_trader), control: TradingControl = Depends(get_trading_control), settings: Settings = Depends(get_settings)) -> dict:
    capital = await control.capital_status()
    amount = capital.get("tradingCapital", 0) or settings.trading_capital_default
    return engine.profit_lock_status(amount)


@router.get("/auto-trader/replay")
async def auto_trader_replay(limit: int = 250, engine: AutoTraderEngine = Depends(get_auto_trader)) -> dict:
    return engine.replay(limit)


@router.post("/auto-trader/reset")
async def auto_trader_reset(engine: AutoTraderEngine = Depends(get_auto_trader)) -> dict:
    return engine.reset()


@router.get("/auto-trader/reset")
async def auto_trader_reset_get(engine: AutoTraderEngine = Depends(get_auto_trader)) -> dict:
    return engine.reset()


@router.get("/auto-trader/daily-report")
async def auto_trader_daily_report(engine: AutoTraderEngine = Depends(get_auto_trader)) -> dict:
    return engine.daily_report()


@router.get("/risk/profiles")
async def risk_profiles(settings: Settings = Depends(get_settings)) -> dict:
    return {"activeProfile": settings.aggression_profile, "profiles": profile_list()}


@router.get("/risk/optimized-profiles")
async def optimized_profiles(settings: Settings = Depends(get_settings)) -> dict:
    return {
        "NIFTY": settings.optimized_profile_for("NIFTY"),
        "SENSEX": settings.optimized_profile_for("SENSEX"),
        "source": "stored_backend_defaults_or_railway_env",
    }


@router.post("/risk/profile")
async def set_risk_profile(request: RiskProfileRequest) -> dict:
    # Runtime profile persistence should be handled by Railway env vars for production stability.
    return {
        "accepted": True,
        "profile": request.profile,
        "message": "Set AGGRESSION_PROFILE in Railway variables to persist this profile across deploys.",
    }


@router.get("/risk/config")
async def risk_config(settings: Settings = Depends(get_settings)) -> dict:
    return {
        "aiScoreThreshold": settings.ai_score_threshold,
        "safeModeThreshold": settings.safe_mode_threshold,
        "maxExposurePct": settings.max_exposure_pct,
        "dailyDrawdownPct": settings.daily_drawdown_pct,
        "aggressionProfile": settings.aggression_profile,
        "enableLiveTrading": settings.enable_live_trading,
        "aggressiveMode": settings.aggressive_mode,
    }


@alias_router.get("/upstox/login-url")
async def upstox_login_url_alias(auth_service: UpstoxAuthService = Depends(get_upstox_auth)) -> dict[str, str]:
    return await upstox_login_url(auth_service)


@alias_router.get("/upstox/callback")
async def upstox_callback_alias(
    code: str = Query(..., description="Authorization code returned by Upstox"),
    auth_service: UpstoxAuthService = Depends(get_upstox_auth),
) -> dict:
    return await upstox_callback(code, auth_service)


@alias_router.get("/upstox/token/status")
async def upstox_token_status_alias(auth_service: UpstoxAuthService = Depends(get_upstox_auth)) -> dict:
    return await upstox_token_status(auth_service)


@alias_router.get("/deployment/status")
async def deployment_status_alias(
    settings: Settings = Depends(get_settings),
    auth_service: UpstoxAuthService = Depends(get_upstox_auth),
    engine: RealTimeMarketEngine = Depends(get_market_engine),
) -> dict:
    return await deployment_status(settings, auth_service, engine)


@alias_router.get("/execution/status")
async def execution_status_alias(control: TradingControl = Depends(get_trading_control), settings: Settings = Depends(get_settings)) -> dict:
    return await execution_status(control, settings)


@alias_router.get("/execution/stop-now")
async def execution_stop_now_alias(control: TradingControl = Depends(get_trading_control)) -> dict:
    return await stop_execution_now(control)


@alias_router.get("/execution/resume-now")
async def execution_resume_now_alias(control: TradingControl = Depends(get_trading_control)) -> dict:
    return await resume_execution_now(control)


@alias_router.get("/capital")
async def capital_status_alias(control: TradingControl = Depends(get_trading_control), settings: Settings = Depends(get_settings)) -> dict:
    return await capital_status(control, settings)
