from __future__ import annotations

import asyncio
import os

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.services.ai_engine import TradeQualityScorer
from app.services.auto_trader import AutoTraderEngine
from app.services.ai_learning import ContinuousAILearner
from app.services.event_journal import EventJournal
from app.services.historical_trainer import HistoricalTrainer
from app.services.institutional_readiness import InstitutionalReadinessEngine
from app.services.ltp_range_analyzer import LtpRangeAnalyzer
from app.services.market_movers import quote_item, summarize_market_movers
from app.services.market_heatmap import fetch_constituent_heatmap
from app.services.instrument_keys import expanded_market_snapshot_instruments, resolve_config_instrument_list
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
        "symbols": ",".join(settings.trading_symbol_list),
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
        "backendCommit": os.getenv("NEXUSQUANT_BACKEND_COMMIT"),
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
            "/api/upstox/token/persist",
            "/api/upstox/account-summary",
            "/api/market/expiries/NIFTY",
            "/api/market/snapshot/NIFTY",
            "/api/market/snapshots",
            "/api/market/movers",
            "/api/institutional/readiness/NIFTY",
            "/api/market/news/NIFTY",
            "/api/auto-trader/status",
            "/api/auto-trader/performance-analysis",
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
    active_symbols = get_settings().trading_symbol_list
    snapshots = {}
    errors = {}
    for symbol in active_symbols:
        loaded: dict[str, Any] | None = None
        last_error: str | None = None
        for attempt in range(2):
            try:
                loaded = await engine.snapshot(symbol)
                break
            except Exception as exc:
                last_error = str(exc)
                if attempt == 0 and ("429" in last_error or "Too Many Request" in last_error):
                    await asyncio.sleep(1.5)
                    continue
                stale = engine.stale_snapshot(symbol)
                if stale:
                    loaded = stale
                    break
        if loaded is not None:
            snapshots[symbol] = loaded
        elif last_error:
            errors[symbol] = last_error
        if symbol != active_symbols[-1]:
            await asyncio.sleep(0.3)
    if not snapshots:
        raise HTTPException(status_code=503, detail=errors)
    settings = get_settings()
    primary_symbol = settings.primary_symbol.upper()
    primary = snapshots.get(primary_symbol) or snapshots.get("NIFTY") or snapshots.get("SENSEX") or next(iter(snapshots.values()))
    candidates = []
    for symbol, snapshot in snapshots.items():
        for trade in snapshot.get("suggestedTrades") or []:
            candidates.append({"symbol": symbol, **trade})
    market_snapshot: dict[str, Any] = {"available": False, "reason": "not_loaded"}
    try:
        instruments = expanded_market_snapshot_instruments(settings.market_snapshot_instrument_list)
        if instruments:
            client = get_upstox(settings, get_upstox_auth(settings))
            try:
                quote_payload = await client.full_market_quote_batched(instruments)
            except Exception:
                instruments = ["NSE_INDEX|Nifty 50", "BSE_INDEX|SENSEX"]
                quote_payload = await client.full_market_quote(instruments)
            market_snapshot = {"available": True, **summarize_market_movers(instruments, quote_payload)}
    except Exception as exc:
        market_snapshot = {"available": False, "reason": str(exc)}
    payload = {
        **primary,
        "type": "multi_snapshot",
        "displaySymbol": primary.get("symbol"),
        "backgroundSymbols": active_symbols,
        "snapshots": snapshots,
        "symbolErrors": errors,
        "executionCandidates": candidates,
        "marketSnapshot": market_snapshot,
    }
    session_state = current_session_state()
    if session_state.phase == "LIVE_MARKET":
        payload["autoTrader"] = await auto_engine.process(payload)
    else:
        payload["autoTrader"] = {**auto_engine.status(), "processingPaused": True, "pauseReason": "Market is closed; replay/learning mutations paused."}
    return payload


@router.get("/market/news/{symbol}")
async def market_news(symbol: Literal["NIFTY", "SENSEX", "BANKNIFTY"], settings: Settings = Depends(get_settings), client: UpstoxClient = Depends(get_upstox)) -> dict:
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


NIFTY50_STOCKS = [
    "NSE_EQ|HDFCBANK","NSE_EQ|ICICIBANK","NSE_EQ|RELIANCE","NSE_EQ|INFY","NSE_EQ|BHARTIARTL",
    "NSE_EQ|ITC","NSE_EQ|LT","NSE_EQ|TCS","NSE_EQ|AXISBANK","NSE_EQ|SBIN",
    "NSE_EQ|KOTAKBANK","NSE_EQ|WIPRO","NSE_EQ|HCLTECH","NSE_EQ|BAJFINANCE","NSE_EQ|ADANIENT",
    "NSE_EQ|TATAMOTORS","NSE_EQ|NTPC","NSE_EQ|ONGC","NSE_EQ|POWERGRID","NSE_EQ|SUNPHARMA",
    "NSE_EQ|JSWSTEEL","NSE_EQ|TITAN","NSE_EQ|HINDUNILVR","NSE_EQ|NESTLEIND","NSE_EQ|ULTRACEMCO",
    "NSE_EQ|MARUTI","NSE_EQ|BAJAJFINSV","NSE_EQ|DRREDDY","NSE_EQ|CIPLA","NSE_EQ|ADANIPORTS",
    "NSE_EQ|ASIANPAINT","NSE_EQ|EICHERMOT","NSE_EQ|TRENT","NSE_EQ|TATASTEEL","NSE_EQ|BEL",
    "NSE_EQ|HEROMOTOCO","NSE_EQ|HINDALCO","NSE_EQ|COALINDIA","NSE_EQ|SBILIFE","NSE_EQ|HDFCLIFE",
    "NSE_EQ|TECHM","NSE_EQ|APOLLOHOSP","NSE_EQ|MAXHEALTH","NSE_EQ|TATACONSUM","NSE_EQ|JIOFIN",
    "NSE_EQ|SHRIRAMFIN","NSE_EQ|ETERNAL","NSE_EQ|SIEMENS","NSE_EQ|INDUSINDBK","NSE_EQ|BAJAJ-AUTO",
]
BANKNIFTY_STOCKS = [
    "NSE_EQ|HDFCBANK","NSE_EQ|ICICIBANK","NSE_EQ|AXISBANK","NSE_EQ|SBIN","NSE_EQ|KOTAKBANK",
    "NSE_EQ|INDUSINDBK","NSE_EQ|BANDHANBNK","NSE_EQ|FEDERALBNK","NSE_EQ|IDFCFIRSTB",
    "NSE_EQ|PNB","NSE_EQ|BANKBARODA","NSE_EQ|AUBANK",
]
SENSEX30_STOCKS = [
    "NSE_EQ|HDFCBANK","NSE_EQ|RELIANCE","NSE_EQ|ICICIBANK","NSE_EQ|INFY","NSE_EQ|BHARTIARTL",
    "NSE_EQ|ITC","NSE_EQ|TCS","NSE_EQ|AXISBANK","NSE_EQ|SBIN","NSE_EQ|KOTAKBANK",
    "NSE_EQ|LT","NSE_EQ|MARUTI","NSE_EQ|HINDUNILVR","NSE_EQ|BAJFINANCE","NSE_EQ|SUNPHARMA",
    "NSE_EQ|TITAN","NSE_EQ|NESTLEIND","NSE_EQ|ULTRACEMCO","NSE_EQ|POWERGRID","NSE_EQ|NTPC",
    "NSE_EQ|TATAMOTORS","NSE_EQ|DRREDDY","NSE_EQ|WIPRO","NSE_EQ|HCLTECH","NSE_EQ|BAJAJFINSV",
    "NSE_EQ|ADANIENT","NSE_EQ|ADANIPORTS","NSE_EQ|ASIANPAINT","NSE_EQ|TATACONSUM","NSE_EQ|ETERNAL",
]
STOCK_WEIGHTS = {
    "HDFCBANK":13.0,"ICICIBANK":8.5,"RELIANCE":8.0,"INFY":6.0,"BHARTIARTL":4.5,
    "ITC":4.2,"LT":3.8,"TCS":3.5,"AXISBANK":3.2,"SBIN":3.0,"KOTAKBANK":2.8,
    "WIPRO":2.2,"HCLTECH":2.0,"BAJFINANCE":1.9,"ADANIENT":1.8,"TATAMOTORS":1.7,
    "MARUTI":1.6,"NTPC":1.5,"ONGC":1.4,"POWERGRID":1.3,"SUNPHARMA":1.3,"TITAN":1.2,
    "HINDUNILVR":1.1,"NESTLEIND":1.0,"ULTRACEMCO":1.0,"BAJAJFINSV":0.9,"DRREDDY":0.9,
    "CIPLA":0.8,"ADANIPORTS":0.8,"ASIANPAINT":0.8,"INDUSINDBK":0.8,"BANDHANBNK":0.5,
    "FEDERALBNK":0.4,"IDFCFIRSTB":0.4,"PNB":0.4,"BANKBARODA":0.4,"AUBANK":0.4,
}


def _parse_stock_ltp(raw_data: dict) -> list[dict]:
    """Parse Upstox LTP or full-quote response into heatmap items."""
    items = []
    for key, val in raw_data.items():
        parsed = quote_item(key, val or {})
        sym = key.split("|")[-1].split(":")[-1]
        items.append({
            "symbol": sym,
            "instrumentKey": key,
            "ltp": parsed["lastPrice"],
            "prevClose": parsed["previousClose"],
            "changePct": parsed["changePct"],
            "volume": parsed["volume"],
            "weight": STOCK_WEIGHTS.get(sym, 0.3),
            "tone": "bullish" if parsed["changePct"] > 0.5 else "bearish" if parsed["changePct"] < -0.5 else "neutral",
        })
    return sorted(items, key=lambda x: x["weight"], reverse=True)


@router.get("/market/heatmap")
async def market_heatmap(
    index: str = "NIFTY",
    settings: Settings = Depends(get_settings),
    client: UpstoxClient = Depends(get_upstox),
) -> dict:
    """Constituent stock heatmap for NIFTY, SENSEX, or BANKNIFTY.
    Returns price change %, VWAP, high/low, volume and index weight per stock."""
    idx = index.upper()
    items: list[dict] = []
    error: str | None = None
    source = "upstox_constituent_equity"

    try:
        equity_result = await fetch_constituent_heatmap(idx, client)
        if equity_result.get("available"):
            return equity_result
        error = equity_result.get("reason")
        items = equity_result.get("stocks") or []
    except Exception as exc:
        error = str(exc)

    # Sector-index fallback when equity quotes are unavailable
    if len(items) < 10:
        source = "upstox_sector_index_fallback"
        try:
            sector_keys = [k for k in settings.market_snapshot_instrument_list if "INDEX" in k.upper()][:20]
            if not sector_keys:
                sector_keys = [
                    "NSE_INDEX|Nifty 50", "NSE_INDEX|Nifty Bank", "NSE_INDEX|Nifty IT",
                    "NSE_INDEX|Nifty Auto", "NSE_INDEX|Nifty FMCG", "NSE_INDEX|Nifty Pharma",
                    "NSE_INDEX|Nifty Metal", "NSE_INDEX|Nifty PSU Bank", "NSE_INDEX|Nifty Energy",
                    "NSE_INDEX|India VIX", "BSE_INDEX|SENSEX",
                ]
            resp3 = await client.full_market_quote(sector_keys)
            raw3 = resp3.get("data") or {}
            sector_items = []
            for key, val in raw3.items():
                parsed = quote_item(key, val or {})
                sym = key.split("|")[-1].split(":")[-1]
                change_pct = float(parsed["changePct"])
                sector_items.append({
                    "symbol": sym.replace("Nifty ", ""),
                    "instrumentKey": key,
                    "ltp": parsed["lastPrice"],
                    "prevClose": parsed["previousClose"],
                    "changePct": change_pct,
                    "volume": parsed["volume"],
                    "weight": 3.0,
                    "tone": "bullish" if change_pct > 0 else "bearish" if change_pct < 0 else "neutral",
                })
            if sector_items:
                items = sorted(sector_items, key=lambda x: abs(x["changePct"]), reverse=True)
                error = None
        except Exception as exc3:
            error = error or str(exc3)

    if not items:
        return {"index": idx, "available": False, "reason": error or "No data returned by Upstox", "stocks": [], "source": source}

    advancing = sum(1 for i in items if i["changePct"] > 0)
    declining = sum(1 for i in items if i["changePct"] < 0)
    weight_adv = sum(i["weight"] for i in items if i["changePct"] > 0)
    weight_dec = sum(i["weight"] for i in items if i["changePct"] < 0)
    total_weight = weight_adv + weight_dec
    breadth_score = round(weight_adv / total_weight * 100, 1) if total_weight else 50.0

    return {
        "index": idx,
        "available": True,
        "source": source,
        "stockCount": len(items),
        "advancing": advancing,
        "declining": declining,
        "breadthScore": breadth_score,
        "breadthBias": "BULLISH" if breadth_score >= 60 else "BEARISH" if breadth_score <= 40 else "NEUTRAL",
        "stocks": items,
    }


@router.get("/market/movers")
async def market_movers(settings: Settings = Depends(get_settings), client: UpstoxClient = Depends(get_upstox)) -> dict:
    instruments = expanded_market_snapshot_instruments(settings.market_snapshot_instrument_list)
    if not instruments:
        raise HTTPException(status_code=400, detail="MARKET_SNAPSHOT_INSTRUMENT_KEYS is empty.")
    try:
        try:
            payload = await client.full_market_quote_batched(instruments)
        except UpstoxDataError:
            instruments = ["NSE_INDEX|Nifty 50", "BSE_INDEX|SENSEX"]
            payload = await client.full_market_quote(instruments)
    except (UpstoxAuthRequired, UpstoxDataError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return summarize_market_movers(instruments, payload)


@router.get("/institutional/readiness/{symbol}")
async def institutional_readiness(symbol: Literal["NIFTY", "SENSEX", "BANKNIFTY"], engine: RealTimeMarketEngine = Depends(get_market_engine), auto_engine: AutoTraderEngine = Depends(get_auto_trader)) -> dict:
    try:
        snapshot = await engine.snapshot(symbol)
        snapshot["autoTrader"] = auto_engine.status()
        return InstitutionalReadinessEngine().score_snapshot(snapshot)
    except (UpstoxAuthRequired, UpstoxDataError, MarketConfigurationError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/market/snapshot/{symbol}")
async def market_snapshot(symbol: Literal["NIFTY", "SENSEX", "BANKNIFTY"], engine: RealTimeMarketEngine = Depends(get_market_engine)) -> dict:
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
) -> HTMLResponse:
    try:
        token_meta = await auth_service.exchange_code(code)
    except UpstoxAuthError as exc:
        html_err = f"""<!DOCTYPE html><html><head><title>Token Error</title>
        <meta name="viewport" content="width=device-width,initial-scale=1">
        <style>body{{background:#0f172a;color:#f87171;font-family:monospace;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;text-align:center;padding:2rem;}}
        h1{{font-size:1.5rem;}} p{{color:#94a3b8;font-size:.9rem;}} a{{color:#38bdf8;text-decoration:none;}}</style></head>
        <body><div><h1>⚠ Token Error</h1><p>{str(exc)}</p>
        <p style="margin-top:2rem"><a href="https://app.nexusquant.uk/token">← Try Again</a></p></div></body></html>"""
        return HTMLResponse(html_err, status_code=400)
    expires_ist = str(token_meta.get("expiresAtIst") or "")[:19].replace("T", " ")
    html = f"""<!DOCTYPE html><html><head><title>✓ Token Refreshed — NexusQuant</title>
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <meta http-equiv="refresh" content="4;url=https://app.nexusquant.uk">
    <style>
      *{{box-sizing:border-box;margin:0;padding:0;}}
      body{{background:#0f172a;color:#f1f5f9;font-family:-apple-system,system-ui,sans-serif;
        display:flex;align-items:center;justify-content:center;min-height:100vh;padding:1.5rem;}}
      .card{{background:#1e293b;border:1px solid #334155;border-radius:1.5rem;padding:2.5rem 2rem;text-align:center;max-width:380px;width:100%;}}
      .icon{{font-size:3.5rem;margin-bottom:1rem;}}
      h1{{font-size:1.6rem;font-weight:800;color:#10b981;margin-bottom:.5rem;}}
      .expires{{background:#0f172a;border-radius:.75rem;padding:.75rem 1rem;margin:1.25rem 0;font-size:.85rem;color:#94a3b8;}}
      .expires b{{color:#38bdf8;font-family:monospace;}}
      .bar-wrap{{background:#1e293b;border-radius:999px;height:6px;overflow:hidden;margin:.75rem 0;}}
      .bar{{background:#10b981;height:100%;border-radius:999px;animation:fill 4s linear forwards;}}
      @keyframes fill{{from{{width:0}}to{{width:100%}}}}
      p{{font-size:.85rem;color:#64748b;margin-top:.5rem;}}
      a{{color:#38bdf8;text-decoration:none;font-size:.85rem;}}
    </style></head>
    <body><div class="card">
      <div class="icon">✓</div>
      <h1>Token Refreshed</h1>
      <div class="expires">Valid until<br><b>{expires_ist} IST</b></div>
      <div class="bar-wrap"><div class="bar"></div></div>
      <p>Redirecting to NexusQuant...</p>
      <p style="margin-top:1rem"><a href="https://app.nexusquant.uk">Go now →</a></p>
    </div></body></html>"""
    return HTMLResponse(html)


@router.get("/upstox/token/status")
async def upstox_token_status(auth_service: UpstoxAuthService = Depends(get_upstox_auth)) -> dict:
    return await auth_service.token_status()


@router.get("/upstox/login", response_class=HTMLResponse)
async def upstox_login_page(auth_service: UpstoxAuthService = Depends(get_upstox_auth), settings: Settings = Depends(get_settings)) -> HTMLResponse:
    """Morning token refresh shortcut — one tap from phone home screen."""
    try:
        login_url = auth_service.login_url()
    except Exception:
        login_url = None
    token_status = await auth_service.token_status()
    has_token = bool(token_status.get("hasToken"))
    expires_ist = str(token_status.get("expiresAtIst") or "")[:19].replace("T", " ")
    status_color = "#10b981" if has_token else "#f59e0b"
    status_text = f"Valid until {expires_ist} IST" if has_token else "No token — login required"
    status_icon = "✓" if has_token else "⚠"
    btn_text = "Tap to Refresh Upstox Token" if has_token else "Tap to Login with Upstox"
    configured = bool(settings.upstox_api_key and settings.upstox_redirect_uri)
    html = f"""<!DOCTYPE html><html><head><title>NexusQuant — Token Refresh</title>
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <meta name="apple-mobile-web-app-title" content="NQ Token">
    <style>
      *{{box-sizing:border-box;margin:0;padding:0;}}
      body{{background:#0f172a;color:#f1f5f9;font-family:-apple-system,system-ui,sans-serif;
        display:flex;align-items:center;justify-content:center;min-height:100vh;padding:1.5rem;}}
      .card{{background:#1e293b;border:1px solid #334155;border-radius:1.75rem;padding:2.5rem 1.75rem;text-align:center;max-width:360px;width:100%;}}
      .logo{{font-size:.7rem;font-weight:800;letter-spacing:.25em;text-transform:uppercase;color:#475569;margin-bottom:1.5rem;}}
      .status-icon{{font-size:3rem;margin-bottom:.75rem;}}
      .status-text{{font-size:.85rem;color:#94a3b8;background:#0f172a;border-radius:.75rem;padding:.6rem 1rem;margin:.75rem 0;}}
      .status-text b{{color:{status_color};font-family:monospace;}}
      .btn{{display:block;width:100%;padding:1.1rem 1.5rem;background:linear-gradient(135deg,#0891b2,#0e7490);
        color:#fff;font-weight:800;font-size:1rem;border:none;border-radius:1rem;cursor:pointer;
        text-decoration:none;margin-top:1.25rem;letter-spacing:.02em;transition:opacity .15s;}}
      .btn:active{{opacity:.8;}}
      .btn.disabled{{background:#1e293b;border:1px solid #334155;color:#475569;cursor:default;}}
      .sep{{border:none;border-top:1px solid #1e293b;margin:1.25rem 0;}}
      .app-link{{font-size:.8rem;color:#475569;text-decoration:none;}}
      .app-link:hover{{color:#94a3b8;}}
    </style></head>
    <body><div class="card">
      <p class="logo">NexusQuant</p>
      <div class="status-icon">{status_icon}</div>
      <div class="status-text"><b>{status_text}</b></div>
      {"<a href='" + login_url + "' class='btn'>" + btn_text + "</a>" if login_url else "<div class='btn disabled'>Upstox not configured</div>"}
      {"<p style='font-size:.75rem;color:#475569;margin-top:.75rem'>Upstox API key not configured on backend</p>" if not configured else ""}
      <hr class="sep">
      <a href="https://app.nexusquant.uk" class="app-link">← Open trading terminal</a>
    </div></body></html>"""
    return HTMLResponse(html)


@router.get("/deployment/expiry-status")
async def deployment_expiry_status(settings: Settings = Depends(get_settings)) -> dict:
    """Shows configured expiry dates for all trading symbols. Used to detect when weekly rollover is needed."""
    return {
        "symbols": settings.trading_symbol_list,
        "expiries": {sym: settings.expiry_for(sym) for sym in settings.trading_symbol_list},
        "instrumentKeys": {sym: settings.instrument_key_for(sym) for sym in settings.trading_symbol_list},
        "note": "Set NIFTY_EXPIRY_DATE / SENSEX_EXPIRY_DATE / BANKNIFTY_EXPIRY_DATE in env. Leave blank for auto-resolve from option chain.",
    }


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


@router.get("/upstox/token/persist")
async def upstox_token_persist(auth_service: UpstoxAuthService = Depends(get_upstox_auth)) -> dict:
    """Re-persist the current token to all durable stores (file + Redis + memory).

    Call this after manually setting UPSTOX_ACCESS_TOKEN in the environment,
    after copying a token file onto the persistent volume, or any time you want to
    ensure the token is cached across all storage tiers without re-running OAuth.
    """
    result = await auth_service.warm_token_cache()
    status = await auth_service.token_status()
    return {"persisted": result.get("warmed", False), "warmResult": result, "tokenStatus": status}


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
async def market_expiries(symbol: Literal["NIFTY", "SENSEX", "BANKNIFTY"], engine: RealTimeMarketEngine = Depends(get_market_engine), settings: Settings = Depends(get_settings)) -> dict:
    try:
        return await engine.resolve_expiry(symbol, settings.instrument_key_for(symbol), [])
    except (UpstoxAuthRequired, UpstoxDataError, MarketConfigurationError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/upstox/option-chain/{symbol}")
async def option_chain(symbol: Literal["NIFTY", "SENSEX", "BANKNIFTY"], settings: Settings = Depends(get_settings), client: UpstoxClient = Depends(get_upstox), engine: RealTimeMarketEngine = Depends(get_market_engine)) -> dict:
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
    symbol: Literal["NIFTY", "SENSEX", "BANKNIFTY"] = "NIFTY",
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
    for symbol in get_settings().trading_symbol_list:
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
    for symbol in get_settings().trading_symbol_list:
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
    symbol: Literal["NIFTY", "SENSEX", "BANKNIFTY"] = "NIFTY",
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
    for symbol in get_settings().trading_symbol_list:
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
    for symbol in get_settings().trading_symbol_list:
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
    symbol: Literal["NIFTY", "SENSEX", "BANKNIFTY"] = "NIFTY",
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
    for symbol in get_settings().trading_symbol_list:
        try:
            results[symbol] = await trainer.train_option_runner(symbol, target_trades, None, from_date, to_date, 1, max_contracts)
        except (UpstoxAuthRequired, UpstoxDataError) as exc:
            errors[symbol] = str(exc)
    if not results:
        raise HTTPException(status_code=503, detail=errors)
    return {"results": results, "errors": errors}


@router.get("/ai-learning/train-explosive-high-profit")
async def ai_learning_train_explosive_high_profit(
    symbol: Literal["NIFTY", "SENSEX", "BANKNIFTY"] = "NIFTY",
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
    for symbol in get_settings().trading_symbol_list:
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


@router.get("/ai-learning/backtest-missed-today")
async def ai_learning_backtest_missed_today(
    target_trades: int = 500,
    horizon_ticks: int = 60,
    min_profit_points: float = 8.0,
    include_losses: bool = True,
    auto_engine: AutoTraderEngine = Depends(get_auto_trader),
) -> dict:
    return await auto_engine.backtest_missed_trades(
        horizon_ticks=horizon_ticks,
        min_profit_points=min_profit_points,
        include_losses=include_losses,
        target_trades=target_trades,
    )


@router.post("/ai-learning/backtest-missed-today")
async def ai_learning_backtest_missed_today_post(
    target_trades: int = 500,
    horizon_ticks: int = 60,
    min_profit_points: float = 8.0,
    include_losses: bool = True,
    auto_engine: AutoTraderEngine = Depends(get_auto_trader),
) -> dict:
    return await ai_learning_backtest_missed_today(target_trades, horizon_ticks, min_profit_points, include_losses, auto_engine)


@router.get("/ai-learning/backtest-and-train-missed-today")
async def ai_learning_backtest_and_train_missed_today(
    target_trades: int = 500,
    horizon_ticks: int = 60,
    min_profit_points: float = 8.0,
    include_losses: bool = True,
    auto_engine: AutoTraderEngine = Depends(get_auto_trader),
    trainer: HistoricalTrainer = Depends(get_historical_trainer),
) -> dict:
    replay_result = await auto_engine.backtest_and_train_missed_today(target_trades, horizon_ticks, min_profit_points, include_losses)
    historical: dict[str, Any] = {"results": {}, "errors": {}}
    today = date.today().isoformat()
    for symbol in get_settings().trading_symbol_list:
        try:
            historical["results"][symbol] = await trainer.train_option_runner(
                symbol,
                min(300, target_trades),
                None,
                today,
                today,
                1,
                40,
                high_profit_only=True,
            )
        except Exception as exc:
            historical["errors"][symbol] = str(exc)
    return {**replay_result, "historicalOptionTraining": historical}


@router.post("/ai-learning/backtest-and-train-missed-today")
async def ai_learning_backtest_and_train_missed_today_post(
    target_trades: int = 500,
    horizon_ticks: int = 60,
    min_profit_points: float = 8.0,
    include_losses: bool = True,
    auto_engine: AutoTraderEngine = Depends(get_auto_trader),
    trainer: HistoricalTrainer = Depends(get_historical_trainer),
) -> dict:
    return await ai_learning_backtest_and_train_missed_today(target_trades, horizon_ticks, min_profit_points, include_losses, auto_engine, trainer)


@router.get("/ai-learning/train-runner")
async def ai_learning_train_runner(
    symbol: Literal["NIFTY", "SENSEX", "BANKNIFTY"] = "NIFTY",
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
    for symbol in get_settings().trading_symbol_list:
        try:
            results[symbol] = await trainer.train_runner(symbol, target_trades, from_date, to_date)
        except (UpstoxAuthRequired, UpstoxDataError) as exc:
            errors[symbol] = str(exc)
    if not results:
        raise HTTPException(status_code=503, detail=errors)
    return {"results": results, "errors": errors}


@router.post("/ai-learning/train-historical")
async def ai_learning_train_historical(
    symbol: Literal["NIFTY", "SENSEX", "BANKNIFTY"] = "NIFTY",
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
    symbol: Literal["NIFTY", "SENSEX", "BANKNIFTY"] = "NIFTY",
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
async def auto_trader_reset(engine: AutoTraderEngine = Depends(get_auto_trader), preserve_history: bool = True) -> dict:
    """Reset daily trading state. preserve_history=True keeps all-time closed trades for analysis."""
    return engine.reset(preserve_history=preserve_history)


@router.get("/auto-trader/reset")
async def auto_trader_reset_get(engine: AutoTraderEngine = Depends(get_auto_trader), preserve_history: bool = True) -> dict:
    return engine.reset(preserve_history=preserve_history)


@router.get("/auto-trader/daily-report")
async def auto_trader_daily_report(engine: AutoTraderEngine = Depends(get_auto_trader)) -> dict:
    return engine.daily_report()


@router.get("/auto-trader/paper-sessions")
async def auto_trader_paper_sessions(limit: int = 50, engine: AutoTraderEngine = Depends(get_auto_trader)) -> dict:
    return engine.paper_sessions_history(limit)


@router.get("/auto-trader/performance-analysis")
async def auto_trader_performance_analysis(engine: AutoTraderEngine = Depends(get_auto_trader)) -> dict:
    return engine.performance_analysis()


@router.get("/auto-trader/daily-improvement-plan")
async def auto_trader_daily_improvement_plan(engine: AutoTraderEngine = Depends(get_auto_trader)) -> dict:
  """Rolling calibration plan: probability estimates, tier gates, and daily actions for higher PF."""
  return engine._daily_improvement_plan()


@router.get("/auto-trader/missed-runners")
async def auto_trader_missed_runners(engine: AutoTraderEngine = Depends(get_auto_trader)) -> dict:
    """Near-miss runner signals — high-score runners that were blocked. Diagnose missed moves here."""
    missed = list(engine._shared_missed_runners)
    return {
        "count": len(missed),
        "missedRunners": missed[-50:],  # last 50 near-misses
        "note": "Runners with score ≥70 or momentumOverride=True that were blocked by quality/news/cooldown gates.",
    }


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


@alias_router.get("/upstox/token/persist")
async def upstox_token_persist_alias(auth_service: UpstoxAuthService = Depends(get_upstox_auth)) -> dict:
    return await upstox_token_persist(auth_service)


@alias_router.get("/upstox/login", response_class=HTMLResponse)
async def upstox_login_page_alias(auth_service: UpstoxAuthService = Depends(get_upstox_auth), settings: Settings = Depends(get_settings)) -> HTMLResponse:
    return await upstox_login_page(auth_service, settings)


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
