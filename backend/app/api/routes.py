from __future__ import annotations

import asyncio
import os

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.services.ai_engine import TradeQualityScorer
from app.services.realtime_engine import MarketConfigurationError, RealTimeMarketEngine
from app.services.risk_engine import RiskEngine
from app.services.risk_profiles import profile_list
from app.services.session import current_session_state
from app.services.trading_control import TradingControl
from app.services.upstox_auth import UpstoxAuthError, UpstoxAuthService
from app.services.upstox_client import UpstoxAuthRequired, UpstoxClient, UpstoxDataError

router = APIRouter(prefix="/api", tags=["terminal"])
alias_router = APIRouter(tags=["upstox-aliases"])


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


def get_upstox_auth(settings: Settings = Depends(get_settings)) -> UpstoxAuthService:
    return UpstoxAuthService(
        api_key=settings.upstox_api_key,
        api_secret=settings.upstox_api_secret,
        redirect_uri=settings.upstox_redirect_uri,
        redis_url=settings.redis_url,
    )


def get_upstox(
    settings: Settings = Depends(get_settings),
    auth_service: UpstoxAuthService = Depends(get_upstox_auth),
) -> UpstoxClient:
    return UpstoxClient(settings.upstox_api_key, settings.upstox_api_secret, auth_service)


def get_trading_control(settings: Settings = Depends(get_settings)) -> TradingControl:
    return TradingControl(settings.redis_url)


def get_market_engine(
    settings: Settings = Depends(get_settings),
    client: UpstoxClient = Depends(get_upstox),
    trading_control: TradingControl = Depends(get_trading_control),
) -> RealTimeMarketEngine:
    scorer = TradeQualityScorer()
    risk_engine = RiskEngine(settings.ai_score_threshold, settings.safe_mode_threshold, settings.max_exposure_pct)
    return RealTimeMarketEngine(settings, client, scorer, risk_engine, trading_control)


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
        "apiVersion": "0.5.0-runtime-validated",
        "runtimeValidation": engine.validate_runtime(),
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
            "/api/upstox/account-summary",
            "/api/market/expiries/NIFTY",
            "/api/market/snapshot/NIFTY",
        ],
    }


@router.get("/market/snapshots")
async def market_snapshots(engine: RealTimeMarketEngine = Depends(get_market_engine)) -> dict:
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
    return {"type": "multi_snapshot", "snapshots": snapshots, "symbolErrors": errors, "executionCandidates": candidates}


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


@router.get("/risk/profiles")
async def risk_profiles(settings: Settings = Depends(get_settings)) -> dict:
    return {"activeProfile": settings.aggression_profile, "profiles": profile_list()}


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
