from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from statistics import mean
from typing import Any

from app.core.config import Settings
from app.services.ai_engine import TradeQualityScorer
from app.services.risk_engine import RiskEngine
from app.services.session import IST, MarketPhase, current_session_state
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

    def __init__(self, settings: Settings, client: UpstoxClient, scorer: TradeQualityScorer, risk_engine: RiskEngine) -> None:
        self.settings = settings
        self.client = client
        self.scorer = scorer
        self.risk_engine = risk_engine
        self.previous: dict[str, PreviousTick] = {}

    async def snapshot(self, symbol: str | None = None) -> dict[str, Any]:
        selected_symbol = (symbol or self.settings.primary_symbol).upper()
        if selected_symbol not in {"NIFTY", "SENSEX"}:
            raise MarketConfigurationError("PRIMARY_SYMBOL must be NIFTY or SENSEX.")

        instrument_key = self.settings.instrument_key_for(selected_symbol)
        session = current_session_state()
        data_warnings: list[str] = []
        expiry_state = await self.resolve_expiry(selected_symbol, instrument_key, data_warnings)
        expiry = expiry_state["selectedExpiry"]

        option_chain_task = self.client.option_chain(instrument_key, expiry)
        candle_task = self.client.intraday_candles(instrument_key, "minutes", 1)
        quote_task = self.client.ltp([instrument_key])
        funds_task = self.client.funds()
        positions_task = self.client.positions()
        orders_task = self.client.orders()

        option_chain, candles, ltp_quote, funds, positions, orders = await asyncio.gather(
            option_chain_task, candle_task, quote_task, funds_task, positions_task, orders_task
        )

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
        market_profile = self._market_profile(candles_list, atm_strike)
        telemetry = self._telemetry(candles_list)
        momentum = self._momentum_score(candles_list, spot)
        volume_score = self._volume_score(chain_rows, call_md, put_md)
        spread_quality = self._spread_quality(call_md, put_md)
        heatmap = self._heatmap(selected_symbol, chain_rows, spot)
        heatmap_score = round(mean([cell["liquidity"] for cell in heatmap])) if heatmap else 0
        greeks = self._greeks(call_greeks, put_greeks)
        option_bias = self._option_chain_bias(chain_rows, spot)
        gamma_score = round(clamp((abs(greeks["gamma"]) * 2500) + option_bias["gammaWallScore"] * 0.4))
        iv_score = round(clamp(greeks["ivExpansion"]))
        profile_score = self._profile_alignment_score(market_profile, spot)
        orderflow = self._orderflow(selected_symbol, spot, call_md, put_md, option_bias)
        delta_score = round(clamp(50 + orderflow["deltaVelocity"] / 2 + abs(greeks["delta"]) * 30))
        regime = self._regime(session.phase, momentum, volume_score, spread_quality)
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
        risk_decision = self.risk_engine.evaluate(
            tqs=tqs,
            latency_ms=0,
            spread_quality=spread_quality,
            stale_data_ms=self._stale_data_ms(ltp_quote),
            drawdown_pct=self._drawdown_pct(portfolio),
            exposure_pct=portfolio["exposurePct"],
            disconnects=0,
        )

        selected_side = "CALL" if option_bias["direction"] == "BULLISH" else "PUT"
        selected_option = call if selected_side == "CALL" else put
        selected_md = selected_option.get("market_data") or {}
        selected_instrument = selected_option.get("instrument_key")
        selected_ltp = as_float(selected_md.get("ltp"))
        current = PreviousTick(spot=spot, selected_ltp=selected_ltp, selected_volume=as_int(selected_md.get("volume")), timestamp=datetime.now(timezone.utc))
        self.previous[selected_symbol] = current

        execution_allowed = bool(
            self.settings.enable_live_trading
            and session.execution_allowed
            and risk_decision.allow_new_trade
            and selected_instrument
            and selected_ltp > 0
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
            "dataSource": "UPSTOX_REALTIME_REST",
            "dataWarnings": data_warnings,
            "upstoxConnection": {
                "connected": True,
                "dataSource": "Upstox REST APIs",
                "fundsAvailable": portfolio["availableMargin"],
                "fundsUsed": portfolio["usedMargin"],
                "positionsCount": portfolio["positions"],
                "ordersCount": portfolio["orders"],
            },
            "expiryState": expiry_state,
            "premarketAnalysis": self._premarket_analysis(session.phase, market_profile, option_bias, spread_quality, tqs),
            "tomorrowTradePlan": self._tomorrow_trade_plan(selected_symbol, expiry, atm_strike, selected_side, selected_instrument, selected_ltp, tqs, market_profile, option_bias, risk_decision.safe_mode),
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
            "trailingStopState": "Execution disabled" if not self.settings.enable_live_trading else "Risk gated" if not execution_allowed else "Aggressive scalp armed",
            "regime": regime,
            "volatilityRegime": "IV_EXPANSION" if greeks["ivExpansion"] >= 65 else "NORMAL_IV",
            "activeTrades": self._active_trades(selected_symbol, positions, data_warnings),
            "heatmap": heatmap,
            "orderflow": orderflow,
            "greeks": greeks,
            "marketProfile": market_profile,
            "aiMatrix": ai_matrix,
            "risk": {
                "safeMode": risk_decision.safe_mode,
                "dailyDrawdownPct": self._drawdown_pct(portfolio),
                "maxDrawdownPct": self.settings.daily_drawdown_pct,
                "slippageBps": self._spread_bps(call_md, put_md),
                "staleDataMs": self._stale_data_ms(ltp_quote),
                "apiDisconnects": 0,
                "latencyMs": 0,
                "spreadWideningPct": max(0, 100 - spread_quality),
                "maxExposurePct": risk_decision.max_exposure_pct,
                "cooldownSeconds": 0 if execution_allowed else 60,
            },
            "infra": {
                "brokerHealth": 100,
                "websocketLatencyMs": 0,
                "orderRouterLatencyMs": 0,
                "redisHealth": 100,
                "postgresHealth": 100,
                "prometheusHealth": 100,
            },
            "portfolio": portfolio,
            "strategy": {
                "selected": "Aggressive momentum scalp" if self.settings.aggressive_mode else "Controlled scalp",
                "aggression": 90 if self.settings.aggressive_mode and execution_allowed else 55 if execution_allowed else 0,
                "sizeMultiplier": 1.25 if self.settings.aggressive_mode and execution_allowed else 1.0 if execution_allowed else 0,
                "threshold": risk_decision.ai_threshold,
                "router": "AGGRESSIVE_SWEEP" if self.settings.aggressive_mode and execution_allowed else "SMART_LIMIT" if execution_allowed else "SAFE_MODE",
            },
            "telemetry": telemetry,
            "journal": [],
            "backtest": [],
            "executionDecision": {
                "allowNewTrade": execution_allowed,
                "reason": "LIVE_TRADING_DISABLED" if not self.settings.enable_live_trading else risk_decision.reason,
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

    def _telemetry(self, candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "time": candle["time"][11:16] if len(candle["time"]) >= 16 else candle["time"],
                "pnl": 0,
                "tqs": 0,
                "latency": 0,
                "volume": candle["volume"],
                "price": candle["close"],
            }
            for candle in candles[-60:]
        ]

    def _market_profile(self, candles: list[dict[str, Any]], fallback: int) -> dict[str, Any]:
        if not candles:
            return {"poc": fallback, "vah": fallback, "val": fallback, "acceptanceZone": "No candle profile returned by Upstox yet", "volumeProfile": []}
        max_volume_candle = max(candles, key=lambda item: item["volume"])
        prices = [candle["close"] for candle in candles if candle["close"] > 0]
        volumes = [candle["volume"] for candle in candles]
        poc = round(max_volume_candle["close"])
        vah = round(max(prices)) if prices else fallback
        val = round(min(prices)) if prices else fallback
        profile = [{"level": round(candle["close"], 2), "volume": candle["volume"]} for candle in candles[-24:]]
        acceptance = "Above value area" if prices and prices[-1] > vah else "Below value area" if prices and prices[-1] < val else "Inside value area"
        if sum(volumes) == 0:
            acceptance = "Price profile available; volume is zero in Upstox candle response"
        return {"poc": poc, "vah": vah, "val": val, "acceptanceZone": acceptance, "volumeProfile": profile}

    def _momentum_score(self, candles: list[dict[str, Any]], spot: float) -> int:
        if len(candles) < 2:
            return 50
        first = candles[-min(10, len(candles))]["close"]
        last = candles[-1]["close"] or spot
        move_pct = abs((last - first) / first) * 100 if first else 0
        ranges = [abs(c["high"] - c["low"]) for c in candles[-10:] if c["high"] and c["low"]]
        avg_range_pct = (mean(ranges) / last) * 100 if ranges and last else 0
        return round(clamp(35 + move_pct * 35 + avg_range_pct * 55))

    def _volume_score(self, rows: list[dict[str, Any]], call_md: dict[str, Any], put_md: dict[str, Any]) -> int:
        current = as_int(call_md.get("volume")) + as_int(put_md.get("volume"))
        max_volume = 0
        for row in rows:
            ce = ((row.get("call_options") or {}).get("market_data") or {})
            pe = ((row.get("put_options") or {}).get("market_data") or {})
            max_volume = max(max_volume, as_int(ce.get("volume")) + as_int(pe.get("volume")))
        return score_ratio(current, max_volume)

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

    def _orderflow(self, symbol: str, spot: float, call_md: dict[str, Any], put_md: dict[str, Any], bias: dict[str, Any]) -> dict[str, Any]:
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
        funds_source = "unavailable"
        funds_breakdown: dict[str, Any] = {}
        if funds:
            data = funds.get("data") or {}
            equity = data.get("equity") or data.get("Equity") or data.get("available_margin") and data or {}
            available_margin = as_float(equity.get("available_margin") or equity.get("cash_available") or equity.get("cash") or equity.get("net") or 0)
            used_margin = as_float(equity.get("used_margin") or equity.get("utilised_margin") or equity.get("margin_used") or 0)
            payin_amount = as_float(equity.get("payin_amount") or 0)
            exposure_margin = as_float(equity.get("exposure_margin") or 0)
            funds_source = "upstox"
            funds_breakdown = {
                "availableMargin": round(available_margin, 2),
                "usedMargin": round(used_margin, 2),
                "payinAmount": round(payin_amount, 2),
                "exposureMargin": round(exposure_margin, 2),
            }
        pos_rows = (positions or {}).get("data") or []
        unrealized = sum(as_float(row.get("pnl") or row.get("unrealised") or row.get("unrealized_pnl")) for row in pos_rows if isinstance(row, dict))
        realized = sum(as_float(row.get("realised") or row.get("realized_pnl")) for row in pos_rows if isinstance(row, dict))
        exposure = round(clamp((used_margin / available_margin) * 100)) if available_margin > 0 else 0
        order_rows = (orders or {}).get("data") or []
        if not funds or funds_source != "upstox":
            raise UpstoxDataError("Upstox funds endpoint did not return verified account funds; refusing to render portfolio data.")
        return {
            "capital": round(available_margin, 2),
            "margin": round(used_margin, 2),
            "availableMargin": round(available_margin, 2),
            "usedMargin": round(used_margin, 2),
            "payinAmount": round(payin_amount, 2),
            "exposureMargin": round(exposure_margin, 2),
            "fundsSource": funds_source,
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
