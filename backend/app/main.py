from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager, suppress
from datetime import datetime, time
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest
from starlette.responses import Response
from starlette.websockets import WebSocketState

from app.api.routes import alias_router, router
from app.core.config import get_settings
from app.services.ai_engine import TradeQualityScorer
from app.services.auto_trader import AutoTraderEngine
from app.services.event_journal import EventJournal
from app.services.institutional_readiness import InstitutionalReadinessEngine
from app.services.market_movers import summarize_market_movers
from app.services.instrument_keys import expanded_market_snapshot_instruments
from app.services.realtime_engine import MarketConfigurationError, RealTimeMarketEngine
from app.services.risk_engine import RiskEngine
from app.services.session import IST, MarketPhase, current_session_state
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
    token_file=settings.upstox_token_file,
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
MARKET_SNAPSHOT_CACHE: dict = {"available": False, "reason": "not_refreshed_yet"}
_market_snapshot_tick = 0.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    await storage.connect()
    await event_journal.connect()
    await auth_service.warm_token_cache()
    monitor_task = asyncio.create_task(background_market_monitor()) if settings.background_market_monitor_enabled else None
    daily_reset_task = asyncio.create_task(daily_market_open_reset())
    yield
    daily_reset_task.cancel()
    with suppress(BaseException):
        await daily_reset_task
    if monitor_task:
        monitor_task.cancel()
        with suppress(BaseException):
            await monitor_task
    await event_journal.close()
    await storage.close()


app = FastAPI(title=settings.app_name, version="0.2.0", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1000)
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
        "backendCommit": os.getenv("NEXUSQUANT_BACKEND_COMMIT", "unknown"),
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

async def _load_symbol_snapshot(symbol: str) -> tuple[str, dict[str, Any] | None, str | None]:
    last_error: str | None = None
    for attempt in range(2):
        try:
            return symbol, await market_engine.snapshot(symbol), None
        except Exception as exc:
            last_error = str(exc)
            if attempt == 0 and ("429" in last_error or "Too Many Request" in last_error):
                await asyncio.sleep(1.5)
                continue
    stale = market_engine.stale_snapshot(symbol)
    if stale:
        return symbol, stale, None
    return symbol, None, last_error or "snapshot failed"


async def build_multi_symbol_snapshot(*, include_auto_trader: bool | None = None) -> dict:
    symbols = settings.trading_symbol_list
    loaded = await asyncio.gather(*(_load_symbol_snapshot(symbol) for symbol in symbols))
    snapshots: dict[str, dict] = {}
    errors: dict[str, str] = {}
    for symbol, snapshot, error in loaded:
        if snapshot is not None:
            snapshots[symbol] = snapshot
            await storage.persist_snapshot(snapshot)
        elif error:
            errors[symbol] = error

    if not snapshots:
        message = "; ".join(f"{symbol}: {error}" for symbol, error in errors.items()) or "No Upstox market snapshots available."
        raise UpstoxDataError(message)

    primary_symbol = settings.primary_symbol.upper()
    primary = snapshots.get(primary_symbol) or snapshots.get("NIFTY") or next(iter(snapshots.values()))
    execution_candidates = []
    seen_instruments: set[str] = set()
    for symbol, snapshot in snapshots.items():
        for trade in snapshot.get("suggestedTrades") or []:
            instrument_key = str(trade.get("instrumentKey") or "")
            if instrument_key and instrument_key in seen_instruments:
                continue
            if instrument_key:
                seen_instruments.add(instrument_key)
            execution_candidates.append({"symbol": symbol, **trade})

    payload = {
        **primary,
        "type": "multi_snapshot",
        "displaySymbol": primary.get("symbol"),
        "backgroundSymbols": symbols,
        "snapshots": snapshots,
        "symbolErrors": errors,
        "executionCandidates": execution_candidates,
        "marketSnapshot": MARKET_SNAPSHOT_CACHE,
    }
    session_live = any((snapshot.get("marketPhase") == "LIVE_MARKET") for snapshot in snapshots.values())
    should_process_auto = session_live if include_auto_trader is None else include_auto_trader
    payload["autoTrader"] = await auto_trader.process(payload) if should_process_auto else auto_trader.status()
    if not should_process_auto:
        payload["autoTrader"] = {**payload["autoTrader"], "processingPaused": True, "pauseReason": "Market is closed; replay/learning mutations paused."}
    payload["institutionalReadiness"] = InstitutionalReadinessEngine().score_snapshot(payload)
    await emit_journal_events(payload)
    payload["eventJournal"] = await event_journal.recent(50)
    return payload


async def daily_market_open_reset() -> None:
    """Auto-resets paper trades at 09:15 IST every trading day so each session starts clean.
    Preserves closed trade history for rolling analysis."""
    import logging
    log = logging.getLogger("nexusquant.daily_reset")
    last_reset_date: str | None = None
    while True:
        try:
            now_ist = datetime.now(IST)
            today = now_ist.date().isoformat()
            # Reset at 09:15 IST (market open) if not already done today
            if (now_ist.hour == 9 and now_ist.minute >= 15 and last_reset_date != today):
                auto_trader.reset(preserve_history=True)
                last_reset_date = today
                log.info(f"[daily_reset] Auto-reset at market open — {today} 09:15 IST. History preserved.")
        except Exception as exc:
            pass
        await asyncio.sleep(30)  # check every 30s


def _parse_ist_hhmm(value: str) -> time:
    hour_str, minute_str = value.strip().split(":", 1)
    return time(int(hour_str), int(minute_str))


def background_monitor_in_schedule(now: datetime | None = None) -> bool:
    """True when background monitor should run (default 08:30–16:00 IST, Mon–Fri)."""
    if not settings.background_monitor_schedule_enabled:
        return True
    current = (now or datetime.now(IST)).astimezone(IST)
    if current.weekday() >= 5:
        return False
    start = _parse_ist_hhmm(settings.background_monitor_start_ist)
    end = _parse_ist_hhmm(settings.background_monitor_end_ist)
    return start <= current.time() <= end


async def background_market_monitor() -> None:
    """Continuously evaluates runner/paper candidates even when no UI is open.
    When schedule is disabled (default), runs 24/7. Paper trades open/close
    automatically during LIVE_MARKET hours (09:15–15:30 IST)."""
    import logging
    log = logging.getLogger("nexusquant.monitor")
    global _market_snapshot_tick
    tick_count = 0
    while True:
        try:
            if not background_monitor_in_schedule():
                await asyncio.sleep(60)
                continue
            _market_snapshot_tick += max(1.0, float(settings.market_poll_seconds or 1))
            if settings.market_snapshot_monitor_enabled and _market_snapshot_tick >= max(1.0, float(settings.market_snapshot_poll_seconds or 5)):
                _market_snapshot_tick = 0.0
                await refresh_market_snapshot_cache()
            session = current_session_state()
            if session.phase != MarketPhase.LIVE_MARKET:
                await asyncio.sleep(max(15.0, float(settings.market_poll_seconds or 1)))
                continue
            payload = await build_multi_symbol_snapshot(include_auto_trader=True)
            tqs = payload.get("tradeQualityScore", 0)
            LATEST_TQS.set(tqs)
            SNAPSHOTS_STREAMED.inc(len(payload.get("snapshots", {})) or 1)
            at = payload.get("autoTrader", {})
            open_t = len(at.get("openPaperTrades") or [])
            closed_t = len(at.get("closedPaperTrades") or [])
            tick_count += 1
            if tick_count % 60 == 0:  # log every ~5min (60 × 5s)
                log.info(f"[BG monitor] TQS={tqs} open={open_t} closed={closed_t} phase={session.phase.value}")
        except (UpstoxAuthRequired, MarketConfigurationError, UpstoxDataError) as exc:
            pass  # expected when market closed or token missing
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning(f"[BG monitor] unexpected error: {exc}")
        await asyncio.sleep(max(1.0, float(settings.market_poll_seconds or 1)))


async def refresh_market_snapshot_cache() -> dict:
    global MARKET_SNAPSHOT_CACHE
    instruments = expanded_market_snapshot_instruments(settings.market_snapshot_instrument_list)
    if not instruments:
        MARKET_SNAPSHOT_CACHE = {"available": False, "reason": "MARKET_SNAPSHOT_INSTRUMENT_KEYS is empty"}
        return MARKET_SNAPSHOT_CACHE
    try:
        payload = await upstox_client.full_market_quote_batched(instruments)
        MARKET_SNAPSHOT_CACHE = {"available": True, **summarize_market_movers(instruments, payload)}
    except Exception as exc:
        fallback = ["NSE_INDEX|Nifty 50", "BSE_INDEX|SENSEX"]
        try:
            payload = await upstox_client.full_market_quote(fallback)
            MARKET_SNAPSHOT_CACHE = {"available": True, "fallbackReason": str(exc), **summarize_market_movers(fallback, payload)}
        except Exception as fallback_exc:
            MARKET_SNAPSHOT_CACHE = {"available": False, "reason": str(fallback_exc), "configuredInstruments": instruments}
    return MARKET_SNAPSHOT_CACHE

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
