from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from itertools import product
from typing import Any

from app.core.config import Settings
from app.services.realtime_engine import as_float, as_int
from app.services.upstox_client import UpstoxClient


@dataclass(frozen=True)
class StrategyParams:
    min_tqs: int
    breakout_atr: float
    volume_multiplier: float
    target_points: float
    stop_points: float
    trail_atr: float


class StrategyOptimizer:
    """Grid-search optimizer using real Upstox historical candles."""

    def __init__(self, settings: Settings, client: UpstoxClient) -> None:
        self.settings = settings
        self.client = client

    async def optimize(
        self,
        symbol: str = "NIFTY",
        target_samples: int = 1000,
        from_date: str | None = None,
        to_date: str | None = None,
        interval: int = 1,
        max_param_sets: int = 96,
        objective: str = "balanced",
    ) -> dict[str, Any]:
        instrument_key = self.settings.instrument_key_for(symbol)
        today = date.today()
        to_date = to_date or today.isoformat()
        from_date = from_date or (today - timedelta(days=180)).isoformat()
        candles: list[dict[str, Any]] = []
        chunks = []
        errors = []
        for chunk_from, chunk_to in self._date_chunks(from_date, to_date, interval):
            try:
                payload = await self.client.historical_candles(instrument_key, "minutes", interval, chunk_to, chunk_from)
                chunk_candles = self._parse_candles(payload)
                candles.extend(chunk_candles)
                chunks.append({"from": chunk_from, "to": chunk_to, "candles": len(chunk_candles)})
                if len(candles) >= target_samples * 8:
                    break
            except Exception as exc:
                errors.append(f"{chunk_from}->{chunk_to}: {exc}")
        param_grid = self._parameter_grid(max_param_sets)
        evaluations = []
        for params in param_grid:
            trades = self._simulate(candles, params, target_samples)
            evaluations.append(self._metrics(params, trades, objective))
        viable = [item for item in evaluations if item["trades"] >= max(50, target_samples * 0.1)]
        ranked = sorted(viable or evaluations, key=lambda item: item["objectiveScore"], reverse=True)
        return {
            "symbol": symbol,
            "instrumentKey": instrument_key,
            "fromDate": from_date,
            "toDate": to_date,
            "candles": len(candles),
            "chunks": chunks,
            "chunkErrors": errors[-10:],
            "parameterSetsTested": len(evaluations),
            "targetSamples": target_samples,
            "objective": objective,
            "recommendedProfiles": self._recommended_profiles(symbol, evaluations),
            "bestBalanced": ranked[0] if ranked else None,
            "bestByProfitFactor": max(viable or evaluations, key=lambda item: item["profitFactor"], default=None),
            "bestByWinRate": max(viable or evaluations, key=lambda item: item["winRate"], default=None),
            "bestByDrawdown": min(viable or evaluations, key=lambda item: item["maxDrawdown"], default=None),
            "top10": ranked[:10],
            "note": "Optimizer uses real Upstox historical candles with deterministic scalp simulation; no fake market data is created.",
        }

    def _parameter_grid(self, limit: int) -> list[StrategyParams]:
        raw = product(
            [68, 72, 76, 80, 84],
            [0.35, 0.5, 0.75, 1.0],
            [1.15, 1.3, 1.5, 2.0],
            [4.0, 5.0, 6.0, 8.0],
            [1.5, 2.0, 2.5, 3.0],
            [0.35, 0.5, 0.75],
        )
        params = [StrategyParams(*item) for item in raw]
        # Deterministic spread across grid to keep endpoint bounded.
        if len(params) <= limit:
            return params
        step = max(1, len(params) // limit)
        return params[::step][:limit]

    def _simulate(self, candles: list[dict[str, Any]], params: StrategyParams, target_samples: int) -> list[dict[str, Any]]:
        trades = []
        if len(candles) < 25:
            return trades
        for index in range(15, len(candles) - 8):
            window = candles[index - 10:index]
            current = candles[index]
            future = candles[index + 1:index + 8]
            atr = sum(abs(candle["high"] - candle["low"]) for candle in window) / len(window)
            if atr <= 0:
                continue
            high = max(candle["high"] for candle in window)
            low = min(candle["low"] for candle in window)
            avg_volume = sum(candle["volume"] for candle in window) / len(window) if window else 0
            volume_ok = current["volume"] >= avg_volume * params.volume_multiplier if avg_volume else True
            previous_close = candles[index - 1]["close"]
            move = abs(current["close"] - previous_close)
            breakout_strength = move / atr
            side = None
            if current["close"] > high and breakout_strength >= params.breakout_atr:
                side = "CALL"
            elif current["close"] < low and breakout_strength >= params.breakout_atr:
                side = "PUT"
            if not side or not volume_ok:
                continue
            tqs = max(35, min(95, 58 + breakout_strength * 10 + 15 + (5 if params.volume_multiplier >= 1.5 else 0)))
            if tqs < params.min_tqs:
                continue
            trade = self._simulate_exit(current["close"], side, future, atr, params)
            trade.update({"tqs": round(tqs), "side": side, "time": current["time"], "volume": current["volume"], "breakoutStrength": round(breakout_strength, 3)})
            trades.append(trade)
            if len(trades) >= target_samples:
                break
        return trades

    def _simulate_exit(self, entry: float, side: str, future: list[dict[str, Any]], atr: float, params: StrategyParams) -> dict[str, Any]:
        target = max(params.target_points, atr * 0.8)
        stop = max(1.0, params.stop_points)
        trail = max(0.75, atr * params.trail_atr)
        stop_price = entry - stop if side == "CALL" else entry + stop
        partial_taken = False
        best_favorable = entry
        exit_price = entry
        exit_reason = "time_stop"
        mfe = 0.0
        mae = 0.0
        for candle in future:
            if side == "CALL":
                best_favorable = max(best_favorable, candle["high"])
                mfe = max(mfe, candle["high"] - entry)
                mae = min(mae, candle["low"] - entry)
                if candle["low"] <= stop_price:
                    exit_price = stop_price
                    exit_reason = "stop_loss"
                    break
                if not partial_taken and candle["high"] >= entry + target:
                    partial_taken = True
                    stop_price = max(stop_price, entry + 0.25)
                if partial_taken:
                    stop_price = max(stop_price, best_favorable - trail)
                exit_price = candle["close"]
            else:
                best_favorable = min(best_favorable, candle["low"])
                mfe = max(mfe, entry - candle["low"])
                mae = min(mae, entry - candle["high"])
                if candle["high"] >= stop_price:
                    exit_price = stop_price
                    exit_reason = "stop_loss"
                    break
                if not partial_taken and candle["low"] <= entry - target:
                    partial_taken = True
                    stop_price = min(stop_price, entry - 0.25)
                if partial_taken:
                    stop_price = min(stop_price, best_favorable + trail)
                exit_price = candle["close"]
        if partial_taken and exit_reason == "time_stop":
            exit_reason = "trailing_profit_lock"
        raw = exit_price - entry if side == "CALL" else entry - exit_price
        cost = 0.35
        return {"entry": round(entry, 2), "exit": round(exit_price, 2), "pnl": round(raw - cost, 2), "exitReason": exit_reason, "mfe": round(mfe, 2), "mae": round(mae, 2)}

    def _metrics(self, params: StrategyParams, trades: list[dict[str, Any]], objective: str = "balanced") -> dict[str, Any]:
        wins = [trade for trade in trades if trade["pnl"] > 0]
        losses = [trade for trade in trades if trade["pnl"] < 0]
        gross_profit = sum(trade["pnl"] for trade in wins)
        gross_loss = abs(sum(trade["pnl"] for trade in losses))
        pf = round(gross_profit / gross_loss, 3) if gross_loss else round(gross_profit, 3)
        win_rate = round(len(wins) / len(trades) * 100, 2) if trades else 0
        max_dd = self._max_drawdown([trade["pnl"] for trade in trades])
        avg_winner = round(gross_profit / len(wins), 2) if wins else 0
        avg_loser = round(gross_loss / len(losses), 2) if losses else 0
        balanced = round((min(pf, 5) * 25) + (win_rate * 0.35) - (max_dd * 0.05) + min(len(trades), 1000) * 0.01, 3)
        high_win = round((win_rate * 1.2) + (min(pf, 3) * 20) - (max_dd * 0.08) + min(len(trades), 1000) * 0.005 - (25 if pf < 1.5 else 0), 3)
        low_dd = round((min(pf, 4) * 20) + (win_rate * 0.25) - (max_dd * 0.2), 3)
        pf_score = round((min(pf, 5) * 30) + (win_rate * 0.2) - (max_dd * 0.05), 3)
        objective_scores = {
            "balanced": balanced,
            "profit_factor": pf_score,
            "win_rate": round(win_rate + min(pf, 2) * 10 - max_dd * 0.05, 3),
            "low_drawdown": low_dd,
            "high_win_scalp": high_win,
        }
        objective_score = objective_scores.get(objective, balanced)
        return {
            "params": asdict(params),
            "trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "winRate": win_rate,
            "profitFactor": pf,
            "grossProfit": round(gross_profit, 2),
            "grossLoss": round(gross_loss, 2),
            "maxDrawdown": round(max_dd, 2),
            "avgWinner": avg_winner,
            "avgLoser": avg_loser,
            "balancedScore": balanced,
            "highWinScalpScore": high_win,
            "lowDrawdownScore": low_dd,
            "profitFactorScore": pf_score,
            "objectiveScore": objective_score,
            "objective": objective,
            "qualityFlags": {
                "tradableProfitFactor": pf >= 1.5,
                "highWinRate": win_rate >= 45,
                "sufficientTrades": len(trades) >= 300,
                "drawdownControlled": max_dd <= 150,
            },
        }

    def _max_drawdown(self, pnls: list[float]) -> float:
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for pnl in pnls:
            equity += pnl
            peak = max(peak, equity)
            max_dd = max(max_dd, peak - equity)
        return max_dd

    def _date_chunks(self, from_date: str, to_date: str, interval: int) -> list[tuple[str, str]]:
        start = date.fromisoformat(from_date)
        end = date.fromisoformat(to_date)
        if start > end:
            start, end = end, start
        max_days = 28 if interval <= 15 else 85
        chunks = []
        cursor = start
        while cursor <= end:
            chunk_end = min(end, cursor + timedelta(days=max_days))
            chunks.append((cursor.isoformat(), chunk_end.isoformat()))
            cursor = chunk_end + timedelta(days=1)
        return chunks

    def _parse_candles(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        raw = (payload.get("data") or {}).get("candles") or (payload.get("data") or {}).get("candle") or []
        parsed = []
        for candle in raw:
            if not isinstance(candle, list) or len(candle) < 6:
                continue
            parsed.append({"time": str(candle[0]), "open": as_float(candle[1]), "high": as_float(candle[2]), "low": as_float(candle[3]), "close": as_float(candle[4]), "volume": as_int(candle[5])})
        return list(reversed(parsed))
