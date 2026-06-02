from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.config import Settings, get_settings
from app.services.upstox_auth import UpstoxAuthError, UpstoxAuthService
from app.services.upstox_client import UpstoxClient

router = APIRouter(prefix="/api", tags=["terminal"])


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


@router.get("/terminal/state")
async def terminal_state() -> dict[str, str]:
    return {
        "pipeline": "telemetry -> regime -> heatmap -> ai-score -> greeks -> routing -> trailing -> analytics",
        "symbols": "NIFTY,SENSEX",
        "mode": "semi-automated-scalping-infrastructure",
    }


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


@router.get("/upstox/health")
async def upstox_health(client: UpstoxClient = Depends(get_upstox)) -> dict:
    return await client.health()


@router.get("/upstox/portfolio")
async def upstox_portfolio(client: UpstoxClient = Depends(get_upstox)) -> dict:
    return await client.portfolio()


@router.get("/upstox/option-chain/{symbol}")
async def option_chain(symbol: str, client: UpstoxClient = Depends(get_upstox)) -> dict:
    return await client.option_chain(symbol)


@router.get("/risk/config")
async def risk_config(settings: Settings = Depends(get_settings)) -> dict:
    return {
        "aiScoreThreshold": settings.ai_score_threshold,
        "safeModeThreshold": settings.safe_mode_threshold,
        "maxExposurePct": settings.max_exposure_pct,
        "dailyDrawdownPct": settings.daily_drawdown_pct,
    }
