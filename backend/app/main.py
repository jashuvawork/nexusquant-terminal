from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest
from starlette.responses import Response
from starlette.websockets import WebSocketState

from app.api.routes import alias_router, router
from app.core.config import get_settings
from app.services.ai_engine import TradeQualityScorer
from app.services.auto_trader import AutoTraderEngine
from app.services.event_journal import EventJournal
from app.services.institutional_readiness import InstitutionalReadinessEngine
from app.services.realtime_engine import MarketConfigurationError, RealTimeMarketEngine
from app.services.risk_engine import RiskEngine
from app.services.storage import AnalyticsStorage
from app.services.trading_control import TradingControl
from app.services.upstox_auth import UpstoxAuthService
from app.services.upstox_client import UpstoxAuthRequired, UpstoxClient, UpstoxDataError

settings = get_settings()
scorer = TradeQualityScorer()
risk_engine = RiskEngine(settings.ai_score_threshold, settings.safe_mode_threshold, settings.max_exposure_pct)
auth_service = UpstoxAuthService(
    api_key=settings.upstox_api_key,
    api_secret=settings.upstox_api_secret,
    redirect_uri=settings.upstox_redirect_uri,
    redis_url=settings.redis_url,
    access_token=settings.upstox_access_token,
)
upstox_client = UpstoxClient(settings.upstox_api_key, settings.upstox_api_secret, auth_service)
trading_control = TradingControl(settings.redis_url)
auto_trader = AutoTraderEngine(settings, trading_control)
market_engine = RealTimeMarketEngine(settings, upstox_client, scorer, risk_engine, trading_control)
storage = AnalyticsStorage(settings.database_url, settings.redis_url)
event_journal = EventJournal(settings.database_url)

SNAPSHOTS_STREAMED = Counter("nexusquant_snapshots_streamed_total", "Real Upstox market snapshots streamed to clients")
STREAM_ERRORS = Counter("nexusquant_stream_errors_total", "Market stream status/error messages sent to clients")
ACTIVE_WS = Gauge("nexusquant_active_websocket_clients", "Active WebSocket terminal clients")
LATEST_TQS = Gauge("nexusquant_latest_trade_quality_score", "Latest Trade Quality Score")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await storage.connect()
    await event_journal.connect()
    yield
    await event_journal.close()
    await storage.close()


app = FastAPI(title=settings.app_name, version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
app.include_router(alias_router)


@app.get("/health")
async def health() -> dict[str, str | bool]:
    token_status = await auth_service.token_status()
    return {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.environment,
        "upstoxConfigured": token_status["configured"],
        "upstoxTokenPresent": token_status["hasToken"],
    }


@app.get("/metrics")
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


async def stream_status(websocket: WebSocket, status: str, message: str, *, record_error: bool = True) -> bool:
    if websocket.client_state != WebSocketState.CONNECTED or websocket.application_state != WebSocketState.CONNECTED:
        return False
    if record_error:
        STREAM_ERRORS.inc()
        await event_journal.record("API_ERROR", f"{status}: {message}", severity="ERROR", payload={"status": status, "message": message}, event_key=f"API_ERROR:{status}:{message}")
    try:
        await websocket.send_json({"type": "status", "status": status, "message": message})
        return True
    except (RuntimeError, WebSocketDisconnect):
        return False




async def emit_journal_events(payload: dict) -> list[dict]:
    emitted: list[dict] = []
    for symbol, snapshot in (payload.get("snapshots") or {}).items():
        for trade in snapshot.get("suggestedTrades") or []:
            event = await event_journal.record(
                "SIGNAL",
                f"Signal generated: {symbol} {trade.get('strike')} {trade.get('side')} TQS {trade.get('tqs')}",
                symbol=symbol,
                severity="INFO",
                payload=trade,
                event_key=f"SIGNAL:{trade.get('id')}:{snapshot.get('timestamp')}",
            )
            if event:
                emitted.append(event)
        precision = snapshot.get("precisionChecklist") or {}
        if precision and not precision.get("passed"):
            event = await event_journal.record(
                "REJECTION",
                "Trade rejected by precision checklist",
                symbol=symbol,
                severity="WARN",
                payload=precision,
                event_key=f"REJECTION:PRECISION:{symbol}:{snapshot.get('timestamp')}",
            )
            if event:
                emitted.append(event)
        risk = snapshot.get("risk") or {}
        if risk.get("safeMode"):
            event = await event_journal.record(
                "RISK_GATE",
                "SAFE MODE activated",
                symbol=symbol,
                severity="WARN",
                payload=risk,
                event_key=f"RISK:SAFE:{symbol}:{snapshot.get('timestamp')}",
            )
            if event:
                emitted.append(event)
        no_trade = snapshot.get("noTradeZones") or {}
        if no_trade.get("blocked"):
            event = await event_journal.record(
                "REJECTION",
                "Trade rejected by no-trade zone detector",
                symbol=symbol,
                severity="WARN",
                payload=no_trade,
                event_key=f"REJECTION:NO_TRADE:{symbol}:{snapshot.get('timestamp')}",
            )
            if event:
                emitted.append(event)
        latency = (snapshot.get("infra") or {}).get("upstoxLatencyMs") or 0
        if latency and latency > 1200:
            event = await event_journal.record(
                "LATENCY_SPIKE",
                f"Upstox latency spike {latency}ms",
                symbol=symbol,
                severity="WARN",
                payload=snapshot.get("infra") or {},
                event_key=f"LATENCY:{symbol}:{snapshot.get('timestamp')}",
            )
            if event:
                emitted.append(event)
        adaptive_exit = snapshot.get("adaptiveExit") or {}
        for rule in adaptive_exit.get("rules") or []:
            if rule.get("active"):
                event = await event_journal.record(
                    "EXIT_RULE",
                    f"Adaptive exit rule active: {rule.get('name')}",
                    symbol=symbol,
                    severity="INFO",
                    payload=rule,
                    event_key=f"EXIT_RULE:{symbol}:{rule.get('name')}:{snapshot.get('timestamp')}",
                )
                if event:
                    emitted.append(event)
    auto = payload.get("autoTrader") or {}
    for trade in auto.get("openPaperTrades") or []:
        event = await event_journal.record("ENTRY", "Paper trade entered", symbol=trade.get("symbol"), severity="INFO", payload=trade, event_key=f"ENTRY:{trade.get('id')}")
        if event:
            emitted.append(event)
    for trade in auto.get("closedPaperTrades") or []:
        event = await event_journal.record("EXIT", "Paper trade closed", symbol=trade.get("symbol"), severity="INFO", payload=trade, event_key=f"EXIT:{trade.get('id')}:{trade.get('exitedAt')}")
        if event:
            emitted.append(event)
    for skipped in auto.get("skippedSignals") or []:
        event = await event_journal.record("REJECTION", f"Signal skipped: {skipped.get('reason')}", severity="INFO", payload=skipped, event_key=f"SKIP:{skipped.get('candidate')}:{skipped.get('reason')}")
        if event:
            emitted.append(event)
    return emitted

async def build_multi_symbol_snapshot() -> dict:
    symbols = ["NIFTY", "SENSEX"]
    results = await asyncio.gather(*(market_engine.snapshot(symbol) for symbol in symbols), return_exceptions=True)
    snapshots: dict[str, dict] = {}
    errors: dict[str, str] = {}
    for symbol, result in zip(symbols, results, strict=True):
        if isinstance(result, Exception):
            errors[symbol] = str(result)
        else:
            snapshots[symbol] = result
            await storage.persist_snapshot(result)

    if not snapshots:
        message = "; ".join(f"{symbol}: {error}" for symbol, error in errors.items()) or "No Upstox market snapshots available."
        raise UpstoxDataError(message)

    primary_symbol = settings.primary_symbol.upper()
    primary = snapshots.get(primary_symbol) or snapshots.get("NIFTY") or next(iter(snapshots.values()))
    execution_candidates = []
    for symbol, snapshot in snapshots.items():
        for trade in snapshot.get("suggestedTrades") or []:
            execution_candidates.append({"symbol": symbol, **trade})

    payload = {
        **primary,
        "type": "multi_snapshot",
        "displaySymbol": primary.get("symbol"),
        "backgroundSymbols": symbols,
        "snapshots": snapshots,
        "symbolErrors": errors,
        "executionCandidates": execution_candidates,
    }
    payload["autoTrader"] = await auto_trader.process(payload)
    payload["institutionalReadiness"] = InstitutionalReadinessEngine().score_snapshot(payload)
    await emit_journal_events(payload)
    payload["eventJournal"] = await event_journal.recent(50)
    return payload

async def receive_client_heartbeats(websocket: WebSocket) -> None:
    while True:
        message = await websocket.receive_text()
        if message.upper() in {"CLOSE", "DISCONNECT"}:
            break


@app.websocket("/ws/market")
async def market_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    ACTIVE_WS.inc()
    receiver_task = asyncio.create_task(receive_client_heartbeats(websocket))
    ticks_sent = 0
    heartbeat_elapsed = 0.0
    await stream_status(websocket, "CONNECTED", "WebSocket connected. Heartbeat active; streaming real Upstox snapshots when available.", record_error=False)
    try:
        while True:
            if receiver_task.done():
                break
            try:
                snapshot = await build_multi_symbol_snapshot()
                LATEST_TQS.set(snapshot["tradeQualityScore"])
                SNAPSHOTS_STREAMED.inc(len(snapshot.get("snapshots", {})) or 1)
                ticks_sent += 1
                snapshot["streamMeta"] = {"transport": "websocket", "ticksSent": ticks_sent, "heartbeatSeconds": settings.websocket_heartbeat_seconds}
                if websocket.client_state != WebSocketState.CONNECTED or websocket.application_state != WebSocketState.CONNECTED:
                    break
                await websocket.send_json(snapshot)
            except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
                break
            except UpstoxAuthRequired as exc:
                if not await stream_status(websocket, "UPSTOX_AUTH_REQUIRED", str(exc), record_error=False):
                    break
            except MarketConfigurationError as exc:
                if not await stream_status(websocket, "CONFIGURATION_REQUIRED", str(exc)):
                    break
            except UpstoxDataError as exc:
                if not await stream_status(websocket, "UPSTOX_DATA_ERROR", str(exc)):
                    break
            except Exception as exc:
                if not await stream_status(websocket, "STREAM_ERROR", f"Unexpected stream error: {exc}"):
                    break

            heartbeat_elapsed += settings.websocket_send_interval_seconds
            if heartbeat_elapsed >= settings.websocket_heartbeat_seconds:
                heartbeat_elapsed = 0.0
                if not await stream_status(websocket, "HEARTBEAT", f"Heartbeat OK. Ticks processed: {ticks_sent}", record_error=False):
                    break
            await asyncio.sleep(settings.websocket_send_interval_seconds)
    except WebSocketDisconnect:
        pass
    finally:
        receiver_task.cancel()
        with suppress(Exception):
            await receiver_task
        ACTIVE_WS.dec()
