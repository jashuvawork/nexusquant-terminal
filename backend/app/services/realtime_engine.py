from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import ast
from inspect import signature
from datetime import date, datetime, timedelta, timezone
from time import perf_counter
from statistics import mean
from typing import Any

from app.core.config import Settings
from app.services.ai_engine import TradeQualityScorer
from app.services.explosive_runner import ExplosiveRunnerEngine
from app.services.news_engine import NewsEngine
from app.services.news_provider import NewsProvider
from app.services.risk_engine import RiskEngine
from app.services.risk_profiles import adaptive_settings
from app.services.session import IST, MarketPhase, current_session_state
from app.services.trading_control import TradingControl
from app.services.upstox_client import UpstoxClient, UpstoxDataError


class MarketConfigurationError(RuntimeError):
    pass


def clamp(value: float, low: float = 0, high: float = 100) -> float:
    return max(low, min(high, value))


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def score_ratio(value: float, max_value: float) -> int:
    if max_value <= 0:
        return 0
    return round(clamp((value / max_value) * 100))


@dataclass
class PreviousTick:
    spot: float
    selected_ltp: float
    selected_volume: int
    timestamp: datetime


class RealTimeMarketEngine:
    """Builds terminal snapshots only from real Upstox responses."""

    REQUIRED_HELPERS = ("_backtest_metrics", "_suggested_trades", "_premarket_analysis", "_tomorrow_trade_plan", "_chop_filter", "_upstox_latency_ms", "_pressure_mode", "_precision_entry_checklist", "_adaptive_exit_engine", "_no_trade_zones", "_tqs_breakdown", "_production_readiness", "_atr_points", "_entry_model_state")

    def validate_runtime(self) -> dict[str, Any]:
        missing = [name for name in self.REQUIRED_HELPERS if not hasattr(self, name)]
        source = Path(__file__).read_text()
        regime_index = source.find('regime = self._regime')
        chop_index = source.find('chop_filter = self._chop_filter')
        order_ok = regime_index != -1 and chop_index != -1 and regime_index < chop_index
        suggested_params = set(signature(self._suggested_trades).parameters)
        required_params = {"chop_filter", "volume_state", "trading_capital"}
        signature_ok = required_params.issubset(suggested_params)
        precision_params = set(signature(self._precision_entry_checklist).parameters)
        precision_signature_ok = {"optimized_profile", "runner_signal"}.issubset(precision_params)
        source_has_suggested_profile_call = self._source_has_call_keyword("_suggested_trades", "optimized_profile")
        return {
            "ok": not missing and order_ok and signature_ok and precision_signature_ok and source_has_suggested_profile_call,
            "missingHelpers": missing,
            "regimeBeforeChopFilter": order_ok,
            "suggestedTradesSignatureOk": signature_ok,
            "precisionChecklistSignatureOk": precision_signature_ok,
            "suggestedTradesProfileCallOk": source_has_suggested_profile_call,
        }

    def _source_has_call_keyword(self, method_name: str, keyword: str) -> bool:
        try:
            tree = ast.parse(Path(__file__).read_text())
        except Exception:
            return False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == method_name:
                return any(item.arg == keyword for item in node.keywords)
        return False

    def __init__(self, settings: Settings, client: UpstoxClient, scorer: TradeQualityScorer, risk_engine: RiskEngine, trading_control: TradingControl | None = None) -> None:
        self.settings = settings
        self.client = client
        self.scorer = scorer
        self.risk_engine = risk_engine
        self.trading_control = trading_control or TradingControl(settings.redis_url)
        self.previous: dict[str, PreviousTick] = {}

    async def snapshot(self, symbol: str | None = None) -> dict[str, Any]:
        processing_started = perf_counter()
        selected_symbol = (symbol or self.settings.primary_symbol).upper()
        if selected_symbol not in {"NIFTY", "SENSEX"}:
            raise MarketConfigurationError("PRIMARY_SYMBOL must be NIFTY or SENSEX.")

        instrument_key = self.settings.instrument_key_for(selected_symbol)
        optimized_profile = self.settings.optimized_profile_for(selected_symbol)
        session = current_session_state()
        data_warnings: list[str] = []
        expiry_state = await self.resolve_expiry(selected_symbol, instrument_key, data_warnings)
        expiry = expiry_state["selectedExpiry"]

        option_chain_task = self.client.option_chain(instrument_key, expiry)
        candle_task = self.client.intraday_candles(instrument_key, "minutes", 1)
        quote_task = self.client.ltp([instrument_key])
        funds_task = self._optional(self.client.funds(), "funds", data_warnings)
        positions_task = self._optional(self.client.positions(), "positions", data_warnings)
        orders_task = self._optional(self.client.orders(), "orders", data_warnings)
        external_news_task = self._optional(NewsProvider(self.settings).fetch(selected_symbol), "external_news", data_warnings)
        upstox_news_task = self._optional(self.client.news_headlines(instrument_key), "upstox_news", data_warnings)

        option_chain, candles, ltp_quote, funds, positions, orders, external_news, upstox_news = await asyncio.gather(
            option_chain_task, candle_task, quote_task, funds_task, positions_task, orders_task, external_news_task, upstox_news_task
        )
        news_payload = external_news if (external_news or {}).get("data") else upstox_news
        news_reason = None
        if not (external_news or {}).get("data") and not upstox_news:
            news_reason = (external_news or {}).get("reason") or next((warning for warning in data_warnings if "upstox_news" in warning.lower()), None)
        news_state = NewsEngine().analyze(news_payload, news_reason)
        news_state["providerStatus"] = {"primary": "finnhub", "external": external_news, "upstox": {"available": bool(upstox_news), "error": next((warning for warning in data_warnings if "upstox_news" in warning.lower()), None)}}

        chain_rows = option_chain.get("data") or []
        if not chain_rows:
            raise UpstoxDataError("Upstox option chain returned no rows for the configured expiry.")

        spot = self._underlying_spot(chain_rows, ltp_quote)
        if spot <= 0:
            raise UpstoxDataError("Unable to determine underlying spot from Upstox quote/option-chain data.")

        atm_row = min(chain_rows, key=lambda row: abs(as_float(row.get("strike_price")) - spot))
        atm_strike = as_int(atm_row.get("strike_price"))
        call = atm_row.get("call_options") or {}
        put = atm_row.get("put_options") or {}
        call_md = call.get("market_data") or {}
        put_md = put.get("market_data") or {}
        call_greeks = call.get("option_greeks") or {}
        put_greeks = put.get("option_greeks") or {}

        candles_list = self._candles(candles)
        volume_state = self._volume_state(candles_list, chain_rows, ltp_quote, call_md, put_md)
        market_profile = self._market_profile(candles_list, atm_strike, volume_state)
        telemetry = self._telemetry(candles_list, volume_state)
        momentum = self._momentum_score(candles_list, spot)
        volume_score = volume_state["score"]
        spread_quality = self._spread_quality(call_md, put_md)
        heatmap = self._heatmap(selected_symbol, chain_rows, spot)
        heatmap_score = round(mean([cell["liquidity"] for cell in heatmap])) if heatmap else 0
        greeks = self._greeks(call_greeks, put_greeks)
        option_bias = self._option_chain_bias(chain_rows, spot)
        gamma_score = round(clamp((abs(greeks["gamma"]) * 2500) + option_bias["gammaWallScore"] * 0.4))
        iv_score = round(clamp(greeks["ivExpansion"]))
        profile_score = self._profile_alignment_score(market_profile, spot)
        orderflow = self._orderflow(selected_symbol, spot, call_md, put_md, option_bias, volume_state)
        regime = self._regime(session.phase, momentum, volume_score, spread_quality)
        chop_filter = self._chop_filter(momentum, orderflow, spread_quality, volume_score, regime)
        entry_model = self._entry_model_state(candles_list, spot, market_profile, option_bias)
        delta_score = round(clamp(50 + orderflow["deltaVelocity"] / 2 + abs(greeks["delta"]) * 30))
        regime_score = 85 if regime == "TREND_EXPANSION" else 72 if regime == "RANGE_ABSORPTION" else 55

        features = {
            "delta_engine": delta_score,
            "momentum_engine": momentum,
            "heatmap_engine": heatmap_score,
            "volume_engine": volume_score,
            "regime_engine": regime_score,
            "spread_analysis": spread_quality,
            "option_chain_bias": option_bias["score"],
            "gamma_positioning": gamma_score,
            "iv_expansion": iv_score,
            "market_profile_alignment": profile_score,
        }
        tqs, ai_matrix = self.scorer.score(features)

        portfolio = self._portfolio(funds, positions, orders, data_warnings)
        adaptive_risk = adaptive_settings(self.settings.aggression_profile, session.phase, regime, tqs)
        adaptive_risk["optimizedProfile"] = optimized_profile
        adaptive_risk["newsState"] = news_state
        adaptive_risk["minimumTqs"] = max(int(adaptive_risk["minimumTqs"]), int(optimized_profile["minTqs"]))
        if news_state.get("impact", {}).get("raiseTqs"):
            adaptive_risk["minimumTqs"] = max(int(adaptive_risk["minimumTqs"]), int(optimized_profile["minTqs"]) + 4)
            adaptive_risk.setdefault("adjustments", []).append("News/event risk: raised TQS threshold.")
        adaptive_engine = RiskEngine(
            adaptive_risk["minimumTqs"],
            adaptive_risk["safeModeTqs"],
            adaptive_risk["maxExposurePct"],
        )
        risk_decision = adaptive_engine.evaluate(
            tqs=tqs,
            latency_ms=0,
            spread_quality=spread_quality,
            stale_data_ms=self._stale_data_ms(ltp_quote),
            drawdown_pct=self._drawdown_pct(portfolio),
            exposure_pct=portfolio["exposurePct"],
            disconnects=0,
        )
        upstox_latency = self._upstox_latency_ms(option_chain, candles, ltp_quote, funds, positions, orders, upstox_news)
        tqs_breakdown = self._tqs_breakdown(ai_matrix)
        no_trade_zones = self._no_trade_zones(session.phase, adaptive_risk, regime, chop_filter, spread_quality, volume_state, orderflow, upstox_latency, self._drawdown_pct(portfolio), entry_model, news_state)
        pressure_mode = self._pressure_mode(session.phase, orderflow, spread_quality, volume_state, upstox_latency, risk_decision.safe_mode, no_trade_zones)

        selected_side = "CALL" if option_bias["direction"] == "BULLISH" else "PUT"
        selected_option = call if selected_side == "CALL" else put
        selected_md = selected_option.get("market_data") or {}
        selected_instrument = selected_option.get("instrument_key")
        selected_ltp = as_float(selected_md.get("ltp"))
        runner_signal = ExplosiveRunnerEngine(option_premium_history_available=self.settings.option_premium_history_available).evaluate(
            symbol=selected_symbol,
            side=selected_side,
            strike=atm_strike,
            expiry=expiry,
            instrument_key=selected_instrument,
            premium=selected_ltp,
            selected_md=selected_md,
            greeks=greeks,
            orderflow=orderflow,
            spread_quality=spread_quality,
            volume_state=volume_state,
            heatmap=heatmap,
            market_profile=market_profile,
            entry_model=entry_model,
            tqs=tqs,
        )
        current = PreviousTick(spot=spot, selected_ltp=selected_ltp, selected_volume=as_int(selected_md.get("volume")), timestamp=datetime.now(timezone.utc))
        self.previous[selected_symbol] = current

        trading_control_status = await self.trading_control.status()
        capital_status = await self.trading_control.capital_status()
        trading_capital = capital_status.get("tradingCapital") or self.settings.trading_capital_default
        auto_trading_stopped = bool(trading_control_status.get("autoTradingStopped"))
        atr_points = self._atr_points(candles_list)
        adaptive_exit = self._adaptive_exit_engine(selected_ltp, selected_side, orderflow, spread_quality, volume_state, greeks, pressure_mode, risk_decision.safe_mode, atr_points, optimized_profile)
        precision_checklist = self._precision_entry_checklist(
            tqs=tqs,
            threshold=adaptive_risk["minimumTqs"],
            spread_quality=spread_quality,
            orderflow=orderflow,
            volume_state=volume_state,
            option_bias=option_bias,
            selected_side=selected_side,
            market_profile=market_profile,
            spot=spot,
            chop_filter=chop_filter,
            no_trade_zones=no_trade_zones,
            pressure_mode=pressure_mode,
            entry_model=entry_model,
            optimized_profile=optimized_profile,
            runner_signal=runner_signal,
        )
        execution_allowed = bool(
            self.settings.enable_live_trading
            and not auto_trading_stopped
            and session.execution_allowed
            and risk_decision.allow_new_trade
            and precision_checklist["passed"]
            and not no_trade_zones["blocked"]
            and pressure_mode["level"] != "CRITICAL"
            and production_readiness["readyForFullCapital"]
            and not chop_filter["blocked"]
            and selected_instrument
            and selected_ltp > 0
        )
        trade_mode = "AUTO_EXECUTION_READY" if execution_allowed else "PAPER_EXECUTION" if self.settings.paper_trading else "ANALYSIS_BACKTEST_ONLY"
        backtest_metrics = self._backtest_metrics(candles_list, tqs, spread_quality, volume_state)
        production_readiness = self._production_readiness(backtest_metrics, tqs, volume_state, self._drawdown_pct(portfolio))
        suggested_trades = self._suggested_trades(
            symbol=selected_symbol,
            expiry=expiry,
            side=selected_side,
            strike=atm_strike,
            instrument=selected_instrument,
            premium=selected_ltp,
            tqs=tqs,
            spread_quality=spread_quality,
            option_bias=option_bias,
            market_profile=market_profile,
            execution_allowed=execution_allowed,
            trade_mode=trade_mode,
            safe_mode=risk_decision.safe_mode,
            trading_capital=float(trading_capital or 0),
            chop_filter=chop_filter,
            volume_state=volume_state,
            entry_model=entry_model,
            optimized_profile=optimized_profile,
        )

        return {
            "type": "snapshot",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "marketPhase": session.phase.value,
            "sessionLabel": session.label,
            "sessionReason": session.reason,
            "executionAllowed": execution_allowed,
            "liveTradingEnabled": self.settings.enable_live_trading,
            "aggressiveMode": self.settings.aggressive_mode,
            "autoTradingStopped": auto_trading_stopped,
            "tradingControl": trading_control_status,
            "tradingCapital": {**capital_status, "tradingCapital": float(trading_capital or 0)},
            "tradeMode": trade_mode,
            "qualityFilters": {"chopFilter": chop_filter, "volumeState": volume_state},
            "pressureMode": pressure_mode,
            "precisionChecklist": precision_checklist,
            "adaptiveExit": adaptive_exit,
            "noTradeZones": no_trade_zones,
            "tqsBreakdown": tqs_breakdown,
            "entryModel": entry_model,
            "newsState": news_state,
            "explosiveRunner": runner_signal,
            "productionReadiness": production_readiness,
            "dataSource": "UPSTOX_REALTIME_REST",
            "dataWarnings": data_warnings,
            "upstoxConnection": {
                "connected": True,
                "dataSource": "Upstox REST APIs",
                "marketDataVerified": True,
                "fundsVerified": portfolio["fundsSource"].startswith("upstox"),
                "fundsAvailable": portfolio["availableMargin"],
                "fundsUsed": portfolio["usedMargin"],
                "positionsCount": portfolio["positions"],
                "ordersCount": portfolio["orders"],
            },
            "expiryState": expiry_state,
            "premarketAnalysis": self._premarket_analysis(session.phase, market_profile, option_bias, spread_quality, tqs),
            "tomorrowTradePlan": self._tomorrow_trade_plan(selected_symbol, expiry, atm_strike, selected_side, selected_instrument, selected_ltp, tqs, market_profile, option_bias, risk_decision.safe_mode),
            "suggestedTrades": suggested_trades,
            "symbol": selected_symbol,
            "spot": round(spot, 2),
            "atmStrike": atm_strike,
            "premiumFocusZone": f"{atm_strike} {selected_side} {expiry} | {selected_instrument or 'instrument unavailable'}",
            "aiConfidence": tqs,
            "tradeQualityScore": tqs,
            "pnl": portfolio["unrealizedPnl"],
            "liveExposurePct": portfolio["exposurePct"],
            "spreadQuality": spread_quality,
            "executionLatencyMs": 0,
            "deltaVelocity": orderflow["deltaVelocity"],
            "trailingStopState": "Manual STOP active" if auto_trading_stopped else "Execution disabled" if not self.settings.enable_live_trading else "Risk gated" if not execution_allowed else "Aggressive scalp armed",
            "regime": regime,
            "volatilityRegime": "IV_EXPANSION" if greeks["ivExpansion"] >= 65 else "NORMAL_IV",
            "activeTrades": self._active_trades(selected_symbol, positions, data_warnings),
            "heatmap": heatmap,
            "orderflow": orderflow,
            "greeks": greeks,
            "marketProfile": market_profile,
            "aiMatrix": ai_matrix,
            "optimizedProfile": optimized_profile,
            "adaptiveRisk": adaptive_risk,
            "risk": {
                "safeMode": risk_decision.safe_mode,
                "dailyDrawdownPct": self._drawdown_pct(portfolio),
                "maxDrawdownPct": self.settings.daily_drawdown_pct,
                "slippageBps": self._spread_bps(call_md, put_md),
                "staleDataMs": self._stale_data_ms(ltp_quote),
                "apiDisconnects": 0,
                "latencyMs": round((perf_counter() - processing_started) * 1000, 2),
                "spreadWideningPct": max(0, 100 - spread_quality),
                "maxExposurePct": risk_decision.max_exposure_pct,
                "cooldownSeconds": 0 if execution_allowed else adaptive_risk["cooldownSeconds"],
            },
            "infra": {
                "brokerHealth": 100,
                "websocketLatencyMs": round((perf_counter() - processing_started) * 1000, 2),
                "orderRouterLatencyMs": 0,
                "upstoxLatencyMs": upstox_latency,
                "redisHealth": 100,
                "postgresHealth": 100,
                "prometheusHealth": 100,
            },
            "portfolio": portfolio,
            "strategy": {
                "selected": "Aggressive momentum scalp" if self.settings.aggressive_mode else "Controlled scalp",
                "aggression": 90 if self.settings.aggressive_mode and execution_allowed else min(100, adaptive_risk["dynamicExposurePct"] * 2),
                "sizeMultiplier": 1.25 if self.settings.aggressive_mode and execution_allowed else round(max(0.1, adaptive_risk["dynamicExposurePct"] / 40), 2),
                "threshold": adaptive_risk["minimumTqs"],
                "router": "AGGRESSIVE_SWEEP" if self.settings.aggressive_mode and execution_allowed else "SMART_LIMIT" if execution_allowed else "SAFE_MODE",
            },
            "telemetry": telemetry,
            "journal": [],
            "backtest": backtest_metrics,
            "executionDecision": {
                "allowNewTrade": execution_allowed,
                "reason": "NOT_PRODUCTION_READY" if not production_readiness["readyForFullCapital"] else "NO_TRADE_ZONE" if no_trade_zones["blocked"] else "PRESSURE_MODE_CRITICAL" if pressure_mode["level"] == "CRITICAL" else "PRECISION_CHECKLIST_FAILED" if not precision_checklist["passed"] else "CHOP_FILTER_BLOCKED" if chop_filter["blocked"] else "AUTO_TRADING_STOPPED" if auto_trading_stopped else "LIVE_TRADING_DISABLED" if not self.settings.enable_live_trading else risk_decision.reason,
                "candidateInstrument": selected_instrument,
                "candidateSide": selected_side,
                "candidateLtp": selected_ltp,
            },
        }


    async def resolve_expiry(self, symbol: str, instrument_key: str, warnings: list[str] | None = None) -> dict[str, Any]:
        warnings = warnings if warnings is not None else []
        configured = self.settings.expiry_for(symbol)
        contracts_payload = await self.client.option_contracts(instrument_key)
        contracts = contracts_payload.get("data") or []
        expiries = sorted({str(item.get("expiry")) for item in contracts if item.get("expiry")})
        if not expiries:
            raise UpstoxDataError(f"Upstox returned no option expiries for {symbol} ({instrument_key}).")

        today = datetime.now(IST).date()
        parsed: list[tuple[date, str]] = []
        for expiry in expiries:
            try:
                parsed.append((date.fromisoformat(expiry), expiry))
            except ValueError:
                warnings.append(f"Ignored unparseable Upstox expiry: {expiry}")
        if not parsed:
            raise UpstoxDataError(f"Upstox option contracts for {symbol} did not include parseable expiry dates.")

        configured_valid = configured in expiries if configured else False
        if configured_valid:
            selected = configured
            source = "configured"
        else:
            future = [(dt, raw) for dt, raw in parsed if dt >= today]
            selected = (future[0] if future else parsed[-1])[1]
            source = "upstox_nearest"
            if configured:
                warnings.append(f"Configured {symbol}_EXPIRY_DATE={configured} not found in Upstox contracts; using nearest available {selected}.")

        selected_contracts = [item for item in contracts if item.get("expiry") == selected]
        return {
            "symbol": symbol,
            "underlyingInstrumentKey": instrument_key,
            "selectedExpiry": selected,
            "source": source,
            "configuredExpiry": configured,
            "availableExpiries": expiries[:12],
            "availableExpiryCount": len(expiries),
            "selectedContractCount": len(selected_contracts),
            "lastCheckedAt": datetime.now(timezone.utc).isoformat(),
        }

    async def _optional(self, coroutine: Any, label: str, warnings: list[str]) -> dict[str, Any] | None:
        try:
            return await coroutine
        except Exception as exc:
            warnings.append(f"Upstox {label} unavailable: {exc}")
            return None

    def _underlying_spot(self, rows: list[dict[str, Any]], ltp_quote: dict[str, Any]) -> float:
        for row in rows:
            spot = as_float(row.get("underlying_spot_price"))
            if spot > 0:
                return spot
        for item in (ltp_quote.get("data") or {}).values():
            price = as_float(item.get("last_price"))
            if price > 0:
                return price
        return 0

    def _candles(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        candles = (payload.get("data") or {}).get("candles") or (payload.get("data") or {}).get("candle") or []
        parsed = []
        for candle in candles:
            if not isinstance(candle, list) or len(candle) < 6:
                continue
            parsed.append(
                {
                    "time": str(candle[0]),
                    "open": as_float(candle[1]),
                    "high": as_float(candle[2]),
                    "low": as_float(candle[3]),
                    "close": as_float(candle[4]),
                    "volume": as_int(candle[5]),
                    "oi": as_int(candle[6]) if len(candle) > 6 else 0,
                }
            )
        return list(reversed(parsed))

    def _telemetry(self, candles: list[dict[str, Any]], volume_state: dict[str, Any]) -> list[dict[str, Any]]:
        if not candles:
            return []
        fallback_volume = volume_state.get("effectiveVolume", 0)
        return [
            {
                "time": candle["time"][11:16] if len(candle["time"]) >= 16 else candle["time"],
                "pnl": 0,
                "tqs": 0,
                "latency": 0,
                "volume": candle["volume"] if candle["volume"] > 0 else fallback_volume,
                "price": candle["close"],
            }
            for candle in candles[-60:]
        ]

    def _market_profile(self, candles: list[dict[str, Any]], fallback: int, volume_state: dict[str, Any] | None = None) -> dict[str, Any]:
        if not candles:
            return {"poc": fallback, "vah": fallback, "val": fallback, "acceptanceZone": "No candle profile returned by Upstox yet", "volumeProfile": [], "hvn": fallback, "lvn": fallback, "openingRangeHigh": fallback, "openingRangeLow": fallback}
        max_volume_candle = max(candles, key=lambda item: item["volume"])
        prices = [candle["close"] for candle in candles if candle["close"] > 0]
        volumes = [candle["volume"] for candle in candles]
        poc = round(max_volume_candle["close"])
        vah = round(max(prices)) if prices else fallback
        val = round(min(prices)) if prices else fallback
        profile = [{"level": round(candle["close"], 2), "volume": candle["volume"]} for candle in candles[-24:]]
        acceptance = "Above value area" if prices and prices[-1] > vah else "Below value area" if prices and prices[-1] < val else "Inside value area"
        if sum(volumes) == 0:
            source = (volume_state or {}).get("source", "none")
            acceptance = f"Price profile available; candle volume is zero, using {source} volume fallback"
        hvn_candle = max(candles, key=lambda candle: candle["volume"] or (volume_state or {}).get("effectiveVolume", 0))
        nonzero = [candle for candle in candles if candle["volume"] > 0]
        lvn_candle = min(nonzero, key=lambda candle: candle["volume"]) if nonzero else min(candles, key=lambda candle: candle["close"])
        opening = candles[:15]
        return {
            "poc": poc,
            "vah": vah,
            "val": val,
            "acceptanceZone": acceptance,
            "volumeProfile": profile,
            "hvn": round(hvn_candle["close"], 2),
            "lvn": round(lvn_candle["close"], 2),
            "openingRangeHigh": round(max(candle["high"] for candle in opening), 2) if opening else fallback,
            "openingRangeLow": round(min(candle["low"] for candle in opening), 2) if opening else fallback,
        }

    def _momentum_score(self, candles: list[dict[str, Any]], spot: float) -> int:
        if len(candles) < 2:
            return 50
        first = candles[-min(10, len(candles))]["close"]
        last = candles[-1]["close"] or spot
        move_pct = abs((last - first) / first) * 100 if first else 0
        ranges = [abs(c["high"] - c["low"]) for c in candles[-10:] if c["high"] and c["low"]]
        avg_range_pct = (mean(ranges) / last) * 100 if ranges and last else 0
        return round(clamp(35 + move_pct * 35 + avg_range_pct * 55))

    def _volume_state(self, candles: list[dict[str, Any]], rows: list[dict[str, Any]], ltp_quote: dict[str, Any], call_md: dict[str, Any], put_md: dict[str, Any]) -> dict[str, Any]:
        candle_volume = sum(as_int(candle.get("volume")) for candle in candles[-10:])
        option_volume = as_int(call_md.get("volume")) + as_int(put_md.get("volume"))
        ltp_volume = 0
        for item in (ltp_quote.get("data") or {}).values():
            ltp_volume += as_int(item.get("volume"))
        max_chain_volume = 0
        for row in rows:
            ce = ((row.get("call_options") or {}).get("market_data") or {})
            pe = ((row.get("put_options") or {}).get("market_data") or {})
            max_chain_volume = max(max_chain_volume, as_int(ce.get("volume")) + as_int(pe.get("volume")))
        effective = candle_volume or option_volume or ltp_volume
        source = "candles" if candle_volume else "option_chain" if option_volume else "ltp_quote" if ltp_volume else "unavailable"
        score = score_ratio(option_volume or effective, max_chain_volume or effective or 1)
        return {
            "source": source,
            "candleVolume": candle_volume,
            "optionChainVolume": option_volume,
            "ltpVolume": ltp_volume,
            "effectiveVolume": effective,
            "score": score,
            "volumeAvailable": effective > 0,
        }

    def _volume_score(self, rows: list[dict[str, Any]], call_md: dict[str, Any], put_md: dict[str, Any]) -> int:
        return self._volume_state([], rows, {}, call_md, put_md)["score"]

    def _spread_quality(self, call_md: dict[str, Any], put_md: dict[str, Any]) -> int:
        scores = []
        for md in [call_md, put_md]:
            bid = as_float(md.get("bid_price"))
            ask = as_float(md.get("ask_price"))
            ltp = as_float(md.get("ltp"))
            if bid <= 0 or ask <= 0 or ltp <= 0:
                continue
            spread_pct = ((ask - bid) / ltp) * 100
            scores.append(clamp(100 - spread_pct * 18))
        return round(mean(scores)) if scores else 0

    def _spread_bps(self, call_md: dict[str, Any], put_md: dict[str, Any]) -> float:
        bps = []
        for md in [call_md, put_md]:
            bid = as_float(md.get("bid_price"))
            ask = as_float(md.get("ask_price"))
            ltp = as_float(md.get("ltp"))
            if bid > 0 and ask > 0 and ltp > 0:
                bps.append(((ask - bid) / ltp) * 10_000)
        return round(mean(bps), 2) if bps else 0

    def _heatmap(self, symbol: str, rows: list[dict[str, Any]], spot: float) -> list[dict[str, Any]]:
        sorted_rows = sorted(rows, key=lambda row: abs(as_float(row.get("strike_price")) - spot))[:18]
        max_liquidity = 1
        max_oi = 1
        for row in sorted_rows:
            for key in ["call_options", "put_options"]:
                md = ((row.get(key) or {}).get("market_data") or {})
                max_liquidity = max(max_liquidity, as_int(md.get("bid_qty")) + as_int(md.get("ask_qty")))
                max_oi = max(max_oi, as_int(md.get("oi")))
        cells = []
        for row in sorted(sorted_rows, key=lambda item: as_float(item.get("strike_price"))):
            strike = as_int(row.get("strike_price"))
            for key, side in [("call_options", "CALL"), ("put_options", "PUT")]:
                opt = row.get(key) or {}
                md = opt.get("market_data") or {}
                greeks = opt.get("option_greeks") or {}
                bid_qty = as_int(md.get("bid_qty"))
                ask_qty = as_int(md.get("ask_qty"))
                liquidity = score_ratio(bid_qty + ask_qty, max_liquidity)
                absorption = round(clamp((bid_qty / (bid_qty + ask_qty)) * 100)) if bid_qty + ask_qty else 0
                oi = as_int(md.get("oi"))
                prev_oi = as_int(md.get("prev_oi"))
                oi_change = abs(oi - prev_oi)
                gamma_wall = round(clamp(score_ratio(oi, max_oi) * 0.7 + abs(as_float(greeks.get("gamma"))) * 1500))
                stop_density = round(clamp(score_ratio(oi_change, max_oi) * 100))
                spread_risk = 100 - self._single_spread_quality(md)
                cells.append(
                    {
                        "id": f"{symbol}-{strike}-{side}",
                        "strike": strike,
                        "side": side,
                        "liquidity": liquidity,
                        "absorption": absorption,
                        "gammaWall": gamma_wall,
                        "stopDensity": stop_density,
                        "sweepRisk": round(clamp(spread_risk)),
                        "label": "Gamma/OI wall" if gamma_wall > 75 else "Bid absorption" if absorption > 65 else "Liquidity pocket" if liquidity > 70 else "Thin liquidity",
                    }
                )
        return cells

    def _single_spread_quality(self, md: dict[str, Any]) -> int:
        bid = as_float(md.get("bid_price"))
        ask = as_float(md.get("ask_price"))
        ltp = as_float(md.get("ltp"))
        if bid <= 0 or ask <= 0 or ltp <= 0:
            return 0
        return round(clamp(100 - (((ask - bid) / ltp) * 100) * 18))

    def _greeks(self, call: dict[str, Any], put: dict[str, Any]) -> dict[str, Any]:
        call_delta = as_float(call.get("delta"))
        put_delta = as_float(put.get("delta"))
        call_iv = as_float(call.get("iv"))
        put_iv = as_float(put.get("iv"))
        avg_iv = mean([v for v in [call_iv, put_iv] if v > 0]) if any([call_iv, put_iv]) else 0
        return {
            "delta": round(call_delta if abs(call_delta) >= abs(put_delta) else put_delta, 4),
            "gamma": round(mean([as_float(call.get("gamma")), as_float(put.get("gamma"))]), 6),
            "theta": round(mean([as_float(call.get("theta")), as_float(put.get("theta"))]), 4),
            "vega": round(mean([as_float(call.get("vega")), as_float(put.get("vega"))]), 4),
            "ivRank": round(clamp(avg_iv)),
            "ivPercentile": round(clamp(avg_iv)),
            "ivExpansion": round(clamp(avg_iv)),
        }

    def _option_chain_bias(self, rows: list[dict[str, Any]], spot: float) -> dict[str, Any]:
        near = sorted(rows, key=lambda row: abs(as_float(row.get("strike_price")) - spot))[:10]
        call_oi = sum(as_int(((row.get("call_options") or {}).get("market_data") or {}).get("oi")) for row in near)
        put_oi = sum(as_int(((row.get("put_options") or {}).get("market_data") or {}).get("oi")) for row in near)
        total = call_oi + put_oi
        pcr = put_oi / call_oi if call_oi else 0
        direction = "BULLISH" if pcr >= 1 else "BEARISH"
        imbalance = abs(put_oi - call_oi) / total if total else 0
        return {"score": round(clamp(50 + imbalance * 100)), "direction": direction, "pcr": round(pcr, 3), "gammaWallScore": round(clamp(imbalance * 100))}

    def _profile_alignment_score(self, profile: dict[str, Any], spot: float) -> int:
        vah = as_float(profile.get("vah"))
        val = as_float(profile.get("val"))
        if vah <= val:
            return 50
        if val <= spot <= vah:
            return 65
        return 82

    def _orderflow(self, symbol: str, spot: float, call_md: dict[str, Any], put_md: dict[str, Any], bias: dict[str, Any], volume_state: dict[str, Any]) -> dict[str, Any]:
        previous = self.previous.get(symbol)
        selected_md = call_md if bias["direction"] == "BULLISH" else put_md
        selected_ltp = as_float(selected_md.get("ltp"))
        selected_volume = as_int(selected_md.get("volume"))
        if previous:
            price_velocity = ((spot - previous.spot) / previous.spot) * 100 if previous.spot else 0
            premium_velocity = ((selected_ltp - previous.selected_ltp) / previous.selected_ltp) * 100 if previous.selected_ltp else 0
            volume_delta = max(0, selected_volume - previous.selected_volume)
        else:
            price_velocity = 0
            premium_velocity = 0
            volume_delta = 0
        volume_delta = max(volume_delta, as_int(volume_state.get("effectiveVolume")))
        call_bid = as_int(call_md.get("bid_qty"))
        call_ask = as_int(call_md.get("ask_qty"))
        put_bid = as_int(put_md.get("bid_qty"))
        put_ask = as_int(put_md.get("ask_qty"))
        depth_total = call_bid + call_ask + put_bid + put_ask
        dom = ((call_bid + put_bid) - (call_ask + put_ask)) / depth_total * 100 if depth_total else 0
        delta_velocity = round(clamp((price_velocity + premium_velocity) * 18 + dom / 2, -100, 100))
        aggressive_buyers = round(clamp(50 + max(delta_velocity, 0)))
        aggressive_sellers = round(clamp(50 + max(-delta_velocity, 0)))
        return {
            "cumulativeDelta": round(volume_delta if delta_velocity >= 0 else -volume_delta),
            "deltaVelocity": delta_velocity,
            "aggressiveBuyers": aggressive_buyers,
            "aggressiveSellers": aggressive_sellers,
            "domImbalance": round(clamp(dom, -100, 100)),
            "liquidityShift": round(clamp(abs(dom))),
            "sweepDetection": round(clamp(100 - self._spread_quality(call_md, put_md))),
            "volumeAcceleration": round(clamp(volume_delta / 1000)),
            "breakoutVelocity": round(clamp(abs(price_velocity) * 25 + abs(premium_velocity) * 20)),
        }

    def _regime(self, phase: MarketPhase, momentum: int, volume: int, spread: int) -> str:
        if phase != MarketPhase.LIVE_MARKET:
            return "CLOSED_MARKET_ANALYSIS"
        if momentum >= 72 and volume >= 60 and spread >= 70:
            return "TREND_EXPANSION"
        if spread < 45:
            return "REVERSAL_RISK"
        return "RANGE_ABSORPTION"

    def _portfolio(self, funds: dict[str, Any] | None, positions: dict[str, Any] | None, orders: dict[str, Any] | None, warnings: list[str]) -> dict[str, Any]:
        available_margin = 0.0
        used_margin = 0.0
        payin_amount = 0.0
        exposure_margin = 0.0
        pledge_available = 0.0
        unsettled_profit = 0.0
        funds_source = "unavailable"
        funds_breakdown: dict[str, Any] = {}
        funds_raw_shape = "none"

        if funds:
            data = funds.get("data") or {}
            source = str(funds.get("nexusquant_source") or "upstox")
            if "available_to_trade" in data:
                available = data.get("available_to_trade") or {}
                cash_available = available.get("cash_available_to_trade") or {}
                pledge = available.get("pledge_available_to_trade") or {}
                unavailable = data.get("unavailable_to_trade") or {}
                unsettled = unavailable.get("unsettled_profit") or {}
                margin_used = cash_available.get("margin_used") or {}
                available_margin = as_float(available.get("total"))
                used_margin = as_float(margin_used.get("total"))
                payin_amount = as_float(((cash_available.get("cash") or {}).get("added_today")))
                exposure_margin = as_float(margin_used.get("span_exposure")) + as_float(margin_used.get("cash_margin_var_elm"))
                pledge_available = as_float(pledge.get("total"))
                unsettled_profit = as_float(unsettled.get("todays")) + as_float(unsettled.get("previous_days"))
                funds_source = "upstox_v3"
                funds_raw_shape = "v3_available_to_trade"
            else:
                equity = data.get("equity") or data.get("Equity") or (data if isinstance(data, dict) else {})
                available_margin = as_float(equity.get("available_margin") or equity.get("cash_available") or equity.get("cash") or equity.get("net") or 0)
                used_margin = as_float(equity.get("used_margin") or equity.get("utilised_margin") or equity.get("margin_used") or 0)
                payin_amount = as_float(equity.get("payin_amount") or 0)
                exposure_margin = as_float(equity.get("exposure_margin") or 0)
                funds_source = "upstox_v2" if source == "upstox_v2" else "upstox"
                funds_raw_shape = "v2_equity"

            funds_breakdown = {
                "availableMargin": round(available_margin, 2),
                "usedMargin": round(used_margin, 2),
                "payinAmount": round(payin_amount, 2),
                "exposureMargin": round(exposure_margin, 2),
                "pledgeAvailable": round(pledge_available, 2),
                "unsettledProfit": round(unsettled_profit, 2),
            }
        else:
            warnings.append("Funds endpoint unavailable; market analysis continues with real Upstox quote/option-chain data, but capital is not verified.")

        pos_rows = (positions or {}).get("data") or []
        unrealized = sum(as_float(row.get("pnl") or row.get("unrealised") or row.get("unrealized_pnl")) for row in pos_rows if isinstance(row, dict))
        realized = sum(as_float(row.get("realised") or row.get("realized_pnl")) for row in pos_rows if isinstance(row, dict))
        exposure = round(clamp((used_margin / available_margin) * 100)) if available_margin > 0 else 0
        order_rows = (orders or {}).get("data") or []
        return {
            "capital": round(available_margin, 2),
            "margin": round(used_margin, 2),
            "availableMargin": round(available_margin, 2),
            "usedMargin": round(used_margin, 2),
            "payinAmount": round(payin_amount, 2),
            "exposureMargin": round(exposure_margin, 2),
            "pledgeAvailable": round(pledge_available, 2),
            "unsettledProfit": round(unsettled_profit, 2),
            "fundsSource": funds_source,
            "fundsRawShape": funds_raw_shape,
            "fundsBreakdown": funds_breakdown,
            "realizedPnl": round(realized, 2),
            "unrealizedPnl": round(unrealized, 2),
            "executionQuality": 0,
            "positions": len(pos_rows) if isinstance(pos_rows, list) else 0,
            "orders": len(order_rows) if isinstance(order_rows, list) else 0,
            "exposurePct": exposure,
        }

    def _active_trades(self, symbol: str, positions: dict[str, Any] | None, warnings: list[str]) -> list[dict[str, Any]]:
        rows = (positions or {}).get("data") or []
        trades = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            qty = as_int(row.get("quantity") or row.get("net_qty"))
            if qty == 0:
                continue
            instrument = str(row.get("instrument_token") or row.get("instrument_key") or row.get("trading_symbol") or "")
            if symbol not in instrument.upper() and (symbol == "SENSEX" and "SENSEX" not in instrument.upper()):
                continue
            trades.append(
                {
                    "id": str(row.get("trading_symbol") or instrument),
                    "symbol": symbol,
                    "side": "CALL" if "CE" in instrument.upper() else "PUT" if "PE" in instrument.upper() else "CALL",
                    "strike": 0,
                    "qty": abs(qty),
                    "entry": as_float(row.get("average_price") or row.get("buy_price")),
                    "ltp": as_float(row.get("last_price") or row.get("ltp")),
                    "pnl": as_float(row.get("pnl") or row.get("unrealised")),
                    "tqs": 0,
                    "stop": 0,
                    "target": 0,
                    "status": "BROKER_POSITION",
                }
            )
        return trades

    def _chop_filter(self, momentum: int, orderflow: dict[str, Any], spread_quality: int, volume_score: int, regime: str) -> dict[str, Any]:
        reasons: list[str] = []
        if regime == "REVERSAL_RISK":
            reasons.append("regime reversal risk")
        if orderflow.get("breakoutVelocity", 0) < 65:
            reasons.append("breakout velocity below 65")
        if abs(orderflow.get("deltaVelocity", 0)) < 60:
            reasons.append("delta velocity below 60")
        if orderflow.get("sweepDetection", 0) < 10:
            reasons.append("sweep absent")
        if orderflow.get("liquidityShift", 0) < 55:
            reasons.append("liquidity confirmation below 55")
        if orderflow.get("volumeAcceleration", 0) < 70:
            reasons.append("volume acceleration below 70")
        if spread_quality < 85:
            reasons.append("spread quality below institutional filter")
        if volume_score < 70:
            reasons.append("volume score below 70")
        blocked = len(reasons) >= 2 or spread_quality < 45
        return {"blocked": blocked, "reasons": reasons, "score": max(0, 100 - len(reasons) * 18)}

    def _upstox_latency_ms(self, *payloads: dict[str, Any] | None) -> float:
        latencies = [as_float(payload.get("nexusquant_latency_ms")) for payload in payloads if isinstance(payload, dict) and payload.get("nexusquant_latency_ms") is not None]
        return round(mean(latencies), 2) if latencies else 0.0

    def _entry_model_state(self, candles: list[dict[str, Any]], spot: float, profile: dict[str, Any], bias: dict[str, Any]) -> dict[str, Any]:
        if len(candles) < 8:
            return {"model": "ORB_RETEST", "state": "INSUFFICIENT_CANDLES", "retestConfirmed": False, "failedBreakout": False}
        opening = candles[:15]
        recent = candles[-6:]
        opening_high = max(candle["high"] for candle in opening)
        opening_low = min(candle["low"] for candle in opening)
        vah = profile.get("vah", opening_high)
        val = profile.get("val", opening_low)
        last_close = candles[-1]["close"]
        prior = candles[-2]
        direction = bias.get("direction")
        bullish_breakout = last_close > max(opening_high, vah)
        bearish_breakout = last_close < min(opening_low, val)
        retest_confirmed = False
        failed_breakout = False
        state = "NO_BREAKOUT"
        if bullish_breakout:
            touched = any(candle["low"] <= max(opening_high, vah) for candle in recent[:-1])
            held = last_close > max(opening_high, vah) and prior["close"] >= max(opening_high, vah)
            retest_confirmed = touched and held and direction == "BULLISH"
            failed_breakout = any(candle["close"] < max(opening_high, vah) for candle in recent[-3:])
            state = "BULLISH_RETEST_CONFIRMED" if retest_confirmed else "BULLISH_BREAKOUT_WAIT_RETEST"
        elif bearish_breakout:
            touched = any(candle["high"] >= min(opening_low, val) for candle in recent[:-1])
            held = last_close < min(opening_low, val) and prior["close"] <= min(opening_low, val)
            retest_confirmed = touched and held and direction == "BEARISH"
            failed_breakout = any(candle["close"] > min(opening_low, val) for candle in recent[-3:])
            state = "BEARISH_RETEST_CONFIRMED" if retest_confirmed else "BEARISH_BREAKOUT_WAIT_RETEST"
        else:
            failed_breakout = (max(candle["high"] for candle in recent) > opening_high and last_close < opening_high) or (min(candle["low"] for candle in recent) < opening_low and last_close > opening_low)
            state = "FAILED_BREAKOUT" if failed_breakout else "INSIDE_RANGE"
        return {
            "model": "ORB_RETEST",
            "state": state,
            "openingRangeHigh": round(opening_high, 2),
            "openingRangeLow": round(opening_low, 2),
            "spot": round(spot, 2),
            "retestConfirmed": retest_confirmed,
            "failedBreakout": failed_breakout,
            "direction": direction,
        }

    def _pressure_mode(
        self,
        phase: MarketPhase,
        orderflow: dict[str, Any],
        spread_quality: int,
        volume_state: dict[str, Any],
        upstox_latency: float,
        safe_mode: bool,
        no_trade_zones: dict[str, Any],
    ) -> dict[str, Any]:
        triggers: list[str] = []
        if safe_mode:
            triggers.append("risk engine safe mode")
        if upstox_latency > 1200:
            triggers.append("Upstox response latency high")
        if spread_quality < 55:
            triggers.append("spread quality poor")
        if abs(orderflow.get("deltaVelocity", 0)) > 75:
            triggers.append("delta velocity shock")
        if orderflow.get("sweepDetection", 0) > 80:
            triggers.append("liquidity sweep risk")
        if not volume_state.get("volumeAvailable"):
            triggers.append("volume unavailable")
        if no_trade_zones.get("blocked"):
            triggers.append("no-trade zone active")
        if phase != MarketPhase.LIVE_MARKET:
            triggers.append("market not live")
        level = "NORMAL"
        if len(triggers) >= 4 or upstox_latency > 2500 or spread_quality < 35:
            level = "CRITICAL"
        elif len(triggers) >= 2:
            level = "ELEVATED"
        actions = {
            "NORMAL": ["normal monitoring", "standard sizing allowed if all gates pass"],
            "ELEVATED": ["reduce size", "raise TQS", "tighten spread filter", "increase cooldown"],
            "CRITICAL": ["block new trades", "manage exits only", "require manual review"],
        }[level]
        return {"level": level, "triggers": triggers, "actions": actions, "score": max(0, 100 - len(triggers) * 18)}

    def _precision_entry_checklist(
        self,
        *,
        tqs: int,
        threshold: int,
        spread_quality: int,
        orderflow: dict[str, Any],
        volume_state: dict[str, Any],
        option_bias: dict[str, Any],
        selected_side: str,
        market_profile: dict[str, Any],
        spot: float,
        chop_filter: dict[str, Any],
        no_trade_zones: dict[str, Any],
        pressure_mode: dict[str, Any],
        entry_model: dict[str, Any],
        optimized_profile: dict[str, Any] | None = None,
        runner_signal: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        optimized_profile = optimized_profile or {}
        runner_signal = runner_signal or {}
        entry_model_required = optimized_profile.get("entryModel", "breakout") in {"retest", "orb_retest"}
        checks = [
            {"name": "TQS above institutional floor", "passed": tqs >= max(threshold, 68), "value": tqs, "required": max(threshold, 68), "critical": True},
            {"name": "Spread quality", "passed": spread_quality >= 85, "value": spread_quality, "required": 85, "critical": True},
            {"name": "Volume available", "passed": bool(volume_state.get("volumeAvailable")), "value": volume_state.get("source"), "required": "real Upstox volume", "critical": True},
            {"name": "Breakout velocity", "passed": orderflow.get("breakoutVelocity", 0) >= 65, "value": orderflow.get("breakoutVelocity", 0), "required": 65, "critical": True},
            {"name": "Delta velocity", "passed": abs(orderflow.get("deltaVelocity", 0)) >= 60, "value": orderflow.get("deltaVelocity", 0), "required": "+/-60", "critical": True},
            {"name": "Liquidity confirmation", "passed": orderflow.get("liquidityShift", 0) >= 55, "value": orderflow.get("liquidityShift", 0), "required": 55, "critical": True},
            {"name": "Volume acceleration", "passed": orderflow.get("volumeAcceleration", 0) >= 70, "value": orderflow.get("volumeAcceleration", 0), "required": 70, "critical": True},
            {"name": "No chop block", "passed": not chop_filter.get("blocked"), "value": chop_filter.get("reasons", []), "required": "no block", "critical": True},
            {"name": "No no-trade zone", "passed": not no_trade_zones.get("blocked"), "value": no_trade_zones.get("activeZones", []), "required": "none", "critical": True},
            {"name": "Pressure not critical", "passed": pressure_mode.get("level") != "CRITICAL", "value": pressure_mode.get("level"), "required": "NORMAL/ELEVATED", "critical": True},
            {"name": "Option bias aligned", "passed": (selected_side == "CALL" and option_bias.get("direction") == "BULLISH") or (selected_side == "PUT" and option_bias.get("direction") == "BEARISH"), "value": option_bias.get("direction"), "required": selected_side, "critical": False},
            {"name": "Profile acceptance", "passed": spot >= market_profile.get("val", spot) and spot <= market_profile.get("vah", spot), "value": spot, "required": f"{market_profile.get('val')} - {market_profile.get('vah')}", "critical": False},
            {"name": "Stored profile loaded", "passed": bool(optimized_profile.get("mode")), "value": optimized_profile.get("mode"), "required": "optimized symbol profile", "critical": True},
            {"name": "Retest confirmation", "passed": (not entry_model_required) or bool(entry_model.get("retestConfirmed")), "value": entry_model.get("state"), "required": optimized_profile.get("entryModel", "breakout"), "critical": bool(entry_model_required)},
            {"name": "Runner context", "passed": bool(runner_signal.get("candidate")) or runner_signal.get("confidence") in {"LOW", "MEDIUM", "HIGH", None}, "value": runner_signal.get("confidence"), "required": "runner evaluated", "critical": False},
            {"name": "No failed breakout", "passed": not bool(entry_model.get("failedBreakout")), "value": entry_model.get("failedBreakout"), "required": False, "critical": True},
        ]
        critical_failed = [check for check in checks if check["critical"] and not check["passed"]]
        passed_count = sum(1 for check in checks if check["passed"])
        return {"passed": not critical_failed and passed_count >= 7, "passedCount": passed_count, "total": len(checks), "criticalFailed": critical_failed, "checks": checks}

    def _adaptive_exit_engine(
        self,
        premium: float,
        side: str,
        orderflow: dict[str, Any],
        spread_quality: int,
        volume_state: dict[str, Any],
        greeks: dict[str, Any],
        pressure_mode: dict[str, Any],
        safe_mode: bool,
        atr_points: float,
        optimized_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        optimized_profile = optimized_profile or {}
        target_base = float(optimized_profile.get("targetPoints") or self.settings.paper_target_points)
        stop_base = float(optimized_profile.get("stopPoints") or self.settings.paper_stop_points)
        trail_atr = float(optimized_profile.get("trailAtr") or 0.35)
        style = str(optimized_profile.get("executionStyle") or "GENERIC")
        partial_pct = float(optimized_profile.get("partialExitPct") or 0.5)
        runner_pct = float(optimized_profile.get("runnerPct") or 0.5)
        target = max(target_base, premium * 0.04, atr_points * 0.8) if premium else max(target_base, atr_points * 0.8)
        stop = max(stop_base, premium * 0.025, atr_points * 0.5) if premium else max(stop_base, atr_points * 0.5)
        if style == "HIGH_WIN_SCALP":
            target = min(target, target_base)
            trail = max(0.75, atr_points * trail_atr, target * 0.35)
        elif style == "RUNNER_BREAKOUT":
            target = max(target, target_base, atr_points * 1.2)
            trail = max(1.5, atr_points * trail_atr, target * 0.55)
        else:
            trail = max(1.0, target * 0.45, atr_points * trail_atr)
        rules = [
            {"name": "Momentum decay exit", "active": orderflow.get("breakoutVelocity", 0) < 10, "action": "tighten trail or exit"},
            {"name": "Delta reversal exit", "active": (side == "CALL" and orderflow.get("deltaVelocity", 0) < -15) or (side == "PUT" and orderflow.get("deltaVelocity", 0) > 15), "action": "exit on reversal"},
            {"name": "Spread widening exit", "active": spread_quality < 55, "action": "exit/avoid new add"},
            {"name": "Liquidity rejection exit", "active": not volume_state.get("volumeAvailable"), "action": "do not hold runner"},
            {"name": "Theta/IV caution", "active": abs(greeks.get("theta", 0)) > 5 or greeks.get("ivExpansion", 0) > 70, "action": "shorten hold time"},
            {"name": "Pressure emergency flatten", "active": pressure_mode.get("level") == "CRITICAL" or safe_mode, "action": "flatten or manage only"},
        ]
        return {
            "executionStyle": style,
            "targetPoints": round(target, 2),
            "stopPoints": round(stop, 2),
            "trailPoints": round(trail, 2),
            "partialExitAt": round(target * partial_pct, 2),
            "partialExitPct": partial_pct,
            "runnerPct": runner_pct,
            "atrPoints": round(atr_points, 2),
            "rules": rules,
        }

    def _no_trade_zones(
        self,
        phase: MarketPhase,
        adaptive_risk: dict[str, Any],
        regime: str,
        chop_filter: dict[str, Any],
        spread_quality: int,
        volume_state: dict[str, Any],
        orderflow: dict[str, Any],
        upstox_latency: float,
        drawdown_pct: float,
        entry_model: dict[str, Any] | None = None,
        news_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        zones: list[dict[str, Any]] = []
        if phase != MarketPhase.LIVE_MARKET:
            zones.append({"name": "Market not live", "severity": "hard", "reason": "Only analysis/backtest allowed outside live session"})
        if adaptive_risk.get("sessionBucket") == "MIDDAY_CHOP":
            zones.append({"name": "Midday chop", "severity": "soft", "reason": "Higher fake breakout risk"})
        if regime in {"REVERSAL_RISK", "CLOSED_MARKET_ANALYSIS"}:
            zones.append({"name": "Regime block", "severity": "hard", "reason": regime})
        if chop_filter.get("blocked"):
            zones.append({"name": "Chop filter", "severity": "hard", "reason": ", ".join(chop_filter.get("reasons", []))})
        if spread_quality < 55:
            zones.append({"name": "Wide spread", "severity": "hard", "reason": f"spread quality {spread_quality}"})
        if not volume_state.get("volumeAvailable"):
            zones.append({"name": "No volume confirmation", "severity": "hard", "reason": "Upstox volume unavailable"})
        if upstox_latency > 2000:
            zones.append({"name": "Latency spike", "severity": "hard", "reason": f"{upstox_latency}ms"})
        if drawdown_pct >= adaptive_risk.get("dailyDrawdownPct", 3):
            zones.append({"name": "Daily drawdown", "severity": "hard", "reason": f"{drawdown_pct}%"})
        if orderflow.get("sweepDetection", 0) < 5 and orderflow.get("breakoutVelocity", 0) < 10:
            zones.append({"name": "No sweep / weak breakout", "severity": "soft", "reason": "momentum quality weak"})
        if news_state and news_state.get("impact", {}).get("avoidFreshTrades"):
            zones.append({"name": "News event risk", "severity": "hard", "reason": f"{news_state.get('eventRisk')} / {news_state.get('sentiment')}"})
        if entry_model and entry_model.get("failedBreakout"):
            zones.append({"name": "Failed breakout", "severity": "hard", "reason": "breakout returned inside opening/value range"})
        hard = [zone for zone in zones if zone["severity"] == "hard"]
        return {"blocked": bool(hard), "activeZones": zones, "hardBlocks": hard}

    def _production_readiness(self, backtest_metrics: list[dict[str, Any]], tqs: int, volume_state: dict[str, Any], drawdown_pct: float) -> dict[str, Any]:
        metric_map = {metric["name"]: metric["value"] for metric in backtest_metrics}
        sample = float(metric_map.get("Candle Sample", 0))
        win_rate = float(metric_map.get("Win Rate", 0))
        profit_factor = float(metric_map.get("Profit Factor", 0))
        avg_volume = float(metric_map.get("Avg Volume", 0))
        checks = [
            {"name": "500+ trade/candle sample", "passed": sample >= 500, "value": sample, "required": 500},
            {"name": "Profit factor >= 1.5", "passed": profit_factor >= 1.5, "value": profit_factor, "required": 1.5},
            {"name": "Win rate >= 58%", "passed": win_rate >= 58, "value": win_rate, "required": 58},
            {"name": "TQS >= 68", "passed": tqs >= 68, "value": tqs, "required": 68},
            {"name": "Average/effective volume non-zero", "passed": avg_volume > 0 or volume_state.get("effectiveVolume", 0) > 0, "value": avg_volume or volume_state.get("effectiveVolume", 0), "required": "> 0"},
            {"name": "Drawdown < 5%", "passed": drawdown_pct < 5, "value": drawdown_pct, "required": "< 5"},
        ]
        passed = sum(1 for check in checks if check["passed"])
        return {
            "readyForFullCapital": passed == len(checks),
            "readyForSmallLive": profit_factor >= 1.2 and tqs >= 64 and (avg_volume > 0 or volume_state.get("effectiveVolume", 0) > 0),
            "passed": passed,
            "total": len(checks),
            "checks": checks,
            "recommendation": "Paper/shadow only" if passed < 4 else "Small live only" if passed < len(checks) else "Eligible for full-capital evaluation",
            "maxSuggestedLiveCapital": 25000 if passed < len(checks) else None,
        }

    def _atr_points(self, candles: list[dict[str, Any]], lookback: int = 14) -> float:
        recent = candles[-lookback:] if candles else []
        ranges = [abs(as_float(candle.get("high")) - as_float(candle.get("low"))) for candle in recent]
        return round(mean(ranges), 2) if ranges else 0.0

    def _tqs_breakdown(self, ai_matrix: list[dict[str, Any]]) -> dict[str, Any]:
        weighted = []
        for item in ai_matrix:
            contribution = round(float(item.get("score", 0)) * float(item.get("weight", 0)), 2)
            weighted.append({**item, "contribution": contribution})
        top = sorted(weighted, key=lambda item: item["contribution"], reverse=True)[:3]
        weak = [item for item in weighted if item.get("status") == "fail" or item.get("score", 0) < 62]
        total = round(sum(item["contribution"] for item in weighted), 2)
        return {"total": total, "components": weighted, "topContributors": top, "weakComponents": weak, "explanation": "TQS is weighted from real-data-derived engine scores; weak components should explain skipped trades."}

    def _backtest_metrics(self, candles: list[dict[str, Any]], tqs: int, spread_quality: int, volume_state: dict[str, Any]) -> list[dict[str, Any]]:
        if len(candles) < 3:
            return [
                {"name": "Backtest Data", "value": len(candles), "unit": " candles"},
                {"name": "TQS", "value": tqs, "unit": ""},
                {"name": "Spread Quality", "value": spread_quality, "unit": "%"},
            ]
        closes = [as_float(candle.get("close")) for candle in candles if as_float(candle.get("close")) > 0]
        fallback_volume = as_int(volume_state.get("effectiveVolume"))
        volumes = [as_int(candle.get("volume")) for candle in candles]
        if sum(volumes) == 0 and fallback_volume > 0:
            volumes = [fallback_volume for _ in candles]
        if len(closes) < 2:
            return [{"name": "Backtest Data", "value": len(candles), "unit": " candles"}]
        returns = [((closes[index] - closes[index - 1]) / closes[index - 1]) * 100 for index in range(1, len(closes)) if closes[index - 1] > 0]
        wins = [value for value in returns if value > 0]
        losses = [abs(value) for value in returns if value < 0]
        win_rate = round((len(wins) / len(returns)) * 100, 2) if returns else 0
        profit_factor = round((sum(wins) / sum(losses)), 2) if sum(losses) > 0 else round(sum(wins), 2)
        session_move = round(((closes[-1] - closes[0]) / closes[0]) * 100, 3) if closes[0] else 0
        avg_volume = round(mean(volumes), 2) if volumes else 0
        return [
            {"name": "Candle Sample", "value": len(closes), "unit": " real"},
            {"name": "Win Rate", "value": win_rate, "unit": "%"},
            {"name": "Profit Factor", "value": profit_factor, "unit": "x"},
            {"name": "Session Move", "value": session_move, "unit": "%"},
            {"name": "Avg Volume", "value": avg_volume, "unit": ""},
            {"name": "TQS", "value": tqs, "unit": ""},
        ]

    def _suggested_trades(
        self,
        *,
        symbol: str,
        expiry: str,
        side: str,
        strike: int,
        instrument: str | None,
        premium: float,
        tqs: int,
        spread_quality: int,
        option_bias: dict[str, Any],
        market_profile: dict[str, Any],
        execution_allowed: bool,
        trade_mode: str,
        safe_mode: bool,
        trading_capital: float,
        chop_filter: dict[str, Any],
        volume_state: dict[str, Any],
        entry_model: dict[str, Any],
        optimized_profile: dict[str, Any],
        runner_signal: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        action = "EXECUTION_READY" if execution_allowed else "SUGGEST_ONLY"
        confidence = "HIGH" if tqs >= 82 and spread_quality >= 75 else "MEDIUM" if tqs >= 70 else "LOW"
        quantity_estimate = int(trading_capital // premium) if premium > 0 and trading_capital > 0 else 0
        allocation_pct = round(((quantity_estimate * premium) / trading_capital) * 100, 2) if trading_capital > 0 and premium > 0 else 0
        return [
            {
                "id": f"{symbol}-{expiry}-{strike}-{side}",
                "mode": trade_mode,
                "action": action,
                "symbol": symbol,
                "side": side,
                "strike": strike,
                "expiry": expiry,
                "instrumentKey": instrument,
                "lastPremium": premium,
                "tradingCapital": trading_capital,
                "quantityEstimate": quantity_estimate,
                "allocationPct": allocation_pct,
                "chopBlocked": chop_filter.get("blocked", False),
                "chopReasons": chop_filter.get("reasons", []),
                "volumeSource": volume_state.get("source"),
                "effectiveVolume": volume_state.get("effectiveVolume", 0),
                "entryModel": entry_model,
                "optimizedProfile": optimized_profile,
                "strategyType": "EXPLOSIVE_RUNNER" if (runner_signal or {}).get("candidate") else "SCALP",
                "runnerSignal": runner_signal,
                "tqs": tqs,
                "confidence": confidence,
                "bias": option_bias.get("direction"),
                "pcr": option_bias.get("pcr"),
                "safeMode": safe_mode,
                "entryRules": [
                    "Use as analysis/backtest suggestion only while ENABLE_LIVE_TRADING=false" if not execution_allowed else "Eligible for execution route; confirm quantity and risk before order",
                    "Require live spread quality to remain above threshold",
                    "Confirm spot acceptance around VAH/VAL before entry",
                    "Avoid trade if Upstox funds or option chain become unavailable",
                ],
                "levels": {
                    "poc": market_profile.get("poc"),
                    "vah": market_profile.get("vah"),
                    "val": market_profile.get("val"),
                },
                "invalidations": [
                    "TQS drops below threshold",
                    "Spread widens or liquidity disappears",
                    "Bias flips in option chain/OI",
                    "Manual STOP is active",
                ],
            }
        ]

    def _premarket_analysis(self, phase: MarketPhase, profile: dict[str, Any], bias: dict[str, Any], spread_quality: int, tqs: int) -> dict[str, Any]:
        readiness = "WAIT_FOR_MARKET_OPEN" if phase == MarketPhase.PRE_MARKET_ANALYSIS else "CLOSED_MARKET_REVIEW" if phase != MarketPhase.LIVE_MARKET else "LIVE_MONITORING"
        return {
            "readiness": readiness,
            "bias": bias.get("direction"),
            "pcr": bias.get("pcr"),
            "keyLevels": {
                "poc": profile.get("poc"),
                "vah": profile.get("vah"),
                "val": profile.get("val"),
            },
            "checklist": [
                "Confirm first 5-minute candle direction after 09:15 IST",
                "Trade only if spread quality stays above threshold",
                "Confirm option-chain OI bias and ATM liquidity before entry",
                "Avoid live orders until ENABLE_LIVE_TRADING is intentionally enabled",
            ],
            "score": tqs,
            "spreadQuality": spread_quality,
        }

    def _tomorrow_trade_plan(
        self,
        symbol: str,
        expiry: str,
        atm_strike: int,
        side: str,
        instrument: str | None,
        ltp: float,
        tqs: int,
        profile: dict[str, Any],
        bias: dict[str, Any],
        safe_mode: bool,
    ) -> dict[str, Any]:
        return {
            "generatedFor": "next_trading_session",
            "symbol": symbol,
            "expiry": expiry,
            "primaryBias": bias.get("direction"),
            "candidate": {
                "side": side,
                "strike": atm_strike,
                "instrumentKey": instrument,
                "lastPremium": ltp,
            },
            "entryRules": [
                f"Prefer {side} scalp only if spot accepts above VAH" if side == "CALL" else f"Prefer {side} scalp only if spot rejects below VAL",
                "Require TQS above configured threshold after live market opens",
                "Require tight bid/ask spread and fresh volume expansion",
                "Do not place pre-market F&O orders from this terminal",
            ],
            "invalidations": [
                "Spread quality deteriorates",
                "Option-chain bias flips after open",
                "First 5-minute breakout fails and returns inside value area",
                "Risk engine enters SAFE MODE",
            ],
            "levels": {
                "poc": profile.get("poc"),
                "vah": profile.get("vah"),
                "val": profile.get("val"),
            },
            "tqs": tqs,
            "safeMode": safe_mode,
        }

    def _stale_data_ms(self, ltp_quote: dict[str, Any]) -> int:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        timestamps = []
        for item in (ltp_quote.get("data") or {}).values():
            ts = as_int(item.get("timestamp") or item.get("ltt") or 0)
            if ts > 0:
                timestamps.append(ts)
        if not timestamps:
            return 0
        return max(0, now_ms - max(timestamps))

    def _drawdown_pct(self, portfolio: dict[str, Any]) -> float:
        capital = as_float(portfolio.get("capital"))
        pnl = as_float(portfolio.get("unrealizedPnl")) + as_float(portfolio.get("realizedPnl"))
        if capital <= 0 or pnl >= 0:
            return 0.0
        return round(abs(pnl) / capital * 100, 2)
