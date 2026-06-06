from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import product
from typing import Any

from app.core.config import Settings
from app.services.historical_trainer import HistoricalTrainer
from app.services.realtime_engine import as_float, as_int
from app.services.upstox_client import UpstoxClient


@dataclass(frozen=True)
class OptionPremiumParams:
    premium_min: float
    premium_max: float
    breakout_pct: float
    volume_multiplier: float
    target_pct: float
    stop_pct: float
    trail_pct: float
    max_hold_bars: int


class OptionPremiumOptimizer:
    """Optimizer for exact option premium candles."""

    def __init__(self, settings: Settings, client: UpstoxClient) -> None:
        self.settings = settings
        self.client = client
        self.trainer = HistoricalTrainer(settings, client, learner=None)  # type: ignore[arg-type]

    async def optimize(
        self,
        symbol: str = "NIFTY",
        target_samples: int = 1000,
        expiry_date: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        max_contracts: int = 40,
        max_param_sets: int = 120,
        objective: str = "high_win_scalp",
    ) -> dict[str, Any]:
        contracts, expiry, warnings = await self._contracts(symbol, expiry_date)
        selected = self.trainer._select_option_contracts(contracts, max_contracts)
        contract_candles = []
        errors = []
        for contract in selected:
            key = contract.get("instrument_key")
            if not key:
                continue
            start, end = self.trainer._contract_history_window(contract, from_date, to_date, __import__("datetime").date.today())
            candles = []
            for chunk_from, chunk_to in self.trainer._date_chunks(start, end, 1):
                try:
                    payload = await self.client.historical_candles(key, "minutes", 1, chunk_to, chunk_from)
                    candles.extend(self.trainer._parse_candles(payload))
                except Exception as exc:
                    errors.append(f"{key} {chunk_from}->{chunk_to}: {exc}")
            if candles:
                contract_candles.append({"contract": contract, "candles": candles, "from": start, "to": end})
        evaluations = []
        for params in self._parameter_grid(max_param_sets):
            trades = []
            for item in contract_candles:
                trades.extend(self._simulate_contract(item["contract"], item["candles"], params, max(0, target_samples - len(trades))))
                if len(trades) >= target_samples:
                    break
            evaluations.append(self._metrics(params, trades, objective))
        viable = [item for item in evaluations if item["trades"] >= max(25, target_samples * 0.05)]
        source = viable or evaluations
        ranked = sorted(source, key=lambda item: item["objectiveScore"], reverse=True)
        return {
            "symbol": symbol,
            "expiry": expiry,
            "contractsWithCandles": len(contract_candles),
            "contractsChecked": [{"instrumentKey": item["contract"].get("instrument_key"), "tradingSymbol": item["contract"].get("trading_symbol") or item["contract"].get("name"), "candles": len(item["candles"]), "from": item["from"], "to": item["to"]} for item in contract_candles],
            "warnings": warnings,
            "errors": errors[-20:],
            "parameterSetsTested": len(evaluations),
            "targetSamples": target_samples,
            "objective": objective,
            "bestBalanced": ranked[0] if ranked else None,
            "bestByProfitFactor": max(source, key=lambda item: item["profitFactor"], default=None),
            "bestByWinRate": max(source, key=lambda item: item["winRate"], default=None),
            "bestByDrawdown": min(source, key=lambda item: item["maxDrawdown"], default=None),
            "top10": ranked[:10],
            "available": bool(contract_candles),
            "note": "Uses exact option premium historical candles. If no contractsWithCandles, Upstox option premium history is unavailable for the selected contracts/date range.",
        }

    async def _contracts(self, symbol: str, expiry_date: str | None) -> tuple[list[dict[str, Any]], str | None, list[str]]:
        underlying = self.settings.instrument_key_for(symbol)
        requested = expiry_date or self.settings.expiry_for(symbol)
        warnings = []
        expiry = requested if self.trainer._valid_date(requested) else None
        if requested and not expiry:
            warnings.append(f"Ignored invalid expiry value: {requested}")
        if expiry:
            try:
                payload = await self.client.option_contracts(underlying, expiry)
                return payload.get("data") or [], expiry, warnings
            except Exception as exc:
                warnings.append(f"Configured expiry {expiry} rejected: {exc}")
        payload = await self.client.option_contracts(underlying)
        contracts = payload.get("data") or []
        expiries = sorted({str(item.get("expiry")) for item in contracts if self.trainer._valid_date(str(item.get("expiry")))})
        expiry = expiries[0] if expiries else None
        return [item for item in contracts if item.get("expiry") == expiry], expiry, warnings

    def _parameter_grid(self, limit: int) -> list[OptionPremiumParams]:
        raw = product(
            [(0, 25), (25, 50), (50, 100), (100, 150), (150, 250), (250, 500)],
            [0.05, 0.1, 0.15, 0.25],
            [1.0, 1.2, 1.5, 2.0],
            [0.15, 0.3, 0.5, 1.0],
            [0.06, 0.08, 0.12, 0.18],
            [0.08, 0.12, 0.18, 0.25],
            [3, 5, 8, 12],
        )
        params = [OptionPremiumParams(r[0], r[1], b, v, t, st, tr, h) for r, b, v, t, st, tr, h in raw]
        if len(params) <= limit:
            return params
        step = max(1, len(params) // limit)
        return params[::step][:limit]

    def _simulate_contract(self, contract: dict[str, Any], candles: list[dict[str, Any]], params: OptionPremiumParams, target: int) -> list[dict[str, Any]]:
        trades = []
        if len(candles) < 20:
            return trades
        for i in range(10, len(candles) - params.max_hold_bars - 1):
            current = candles[i]
            entry = current["close"]
            if not (params.premium_min <= entry < params.premium_max):
                continue
            window = candles[i - 10:i]
            avg_volume = sum(c["volume"] for c in window) / len(window) if window else 0
            if avg_volume and current["volume"] < avg_volume * params.volume_multiplier:
                continue
            high = max(c["high"] for c in window)
            if entry < high * (1 + params.breakout_pct):
                continue
            future = candles[i + 1:i + 1 + params.max_hold_bars]
            exit_price, reason, mfe, mae = self._exit(entry, future, params)
            pnl = exit_price - entry - max(0.05, entry * 0.003)
            trades.append({"pnl": round(pnl, 2), "entry": entry, "exit": exit_price, "exitReason": reason, "mfe": mfe, "mae": mae, "instrumentKey": contract.get("instrument_key"), "strike": contract.get("strike_price")})
            if len(trades) >= target:
                break
        return trades

    def _exit(self, entry: float, future: list[dict[str, Any]], params: OptionPremiumParams) -> tuple[float, str, float, float]:
        stop = entry * (1 - params.stop_pct)
        target = entry * (1 + params.target_pct)
        trail = entry * params.trail_pct
        best = entry
        exit_price = entry
        reason = "time_stop"
        mfe = 0.0
        mae = 0.0
        for candle in future:
            best = max(best, candle["high"])
            mfe = max(mfe, candle["high"] - entry)
            mae = min(mae, candle["low"] - entry)
            if candle["low"] <= stop:
                return round(stop, 2), "stop_loss", round(mfe, 2), round(mae, 2)
            if candle["high"] >= target:
                stop = max(stop, best - trail, entry * 1.02)
                reason = "target_trail"
            if reason == "target_trail" and candle["low"] <= stop:
                return round(stop, 2), "trailing_profit_lock", round(mfe, 2), round(mae, 2)
            exit_price = candle["close"]
        return round(exit_price, 2), reason, round(mfe, 2), round(mae, 2)

    def _metrics(self, params: OptionPremiumParams, trades: list[dict[str, Any]], objective: str) -> dict[str, Any]:
        wins = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] < 0]
        gp = sum(t["pnl"] for t in wins)
        gl = abs(sum(t["pnl"] for t in losses))
        pf = round(gp / gl, 3) if gl else round(gp, 3)
        wr = round(len(wins) / len(trades) * 100, 2) if trades else 0
        dd = self._max_drawdown([t["pnl"] for t in trades])
        balanced = round(min(pf, 5) * 25 + wr * 0.45 - dd * 0.04 + min(len(trades), 1000) * 0.01, 3)
        high_win = round(wr * 1.4 + min(pf, 3) * 20 - dd * 0.06 - (25 if pf < 1.5 else 0), 3)
        score = {"balanced": balanced, "high_win_scalp": high_win, "profit_factor": min(pf, 5) * 30 + wr * 0.2, "low_drawdown": min(pf, 4) * 20 - dd * 0.2}.get(objective, balanced)
        return {"params": asdict(params), "trades": len(trades), "wins": len(wins), "losses": len(losses), "winRate": wr, "profitFactor": pf, "grossProfit": round(gp, 2), "grossLoss": round(gl, 2), "maxDrawdown": round(dd, 2), "objectiveScore": round(score, 3), "objective": objective}

    def _max_drawdown(self, pnls: list[float]) -> float:
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for pnl in pnls:
            equity += pnl
            peak = max(peak, equity)
            max_dd = max(max_dd, peak - equity)
        return max_dd
