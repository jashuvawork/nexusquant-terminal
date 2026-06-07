from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.core.config import Settings
from app.services.ai_learning import ContinuousAILearner
from app.services.realtime_engine import as_float, as_int
from app.services.upstox_client import UpstoxClient


class HistoricalTrainer:
    def __init__(self, settings: Settings, client: UpstoxClient, learner: ContinuousAILearner) -> None:
        self.settings = settings
        self.client = client
        self.learner = learner

    async def train(
        self,
        symbol: str = "NIFTY",
        target_trades: int | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        interval: int = 1,
    ) -> dict[str, Any]:
        target = target_trades or self.settings.historical_training_target_trades
        instrument_key = self.settings.instrument_key_for(symbol)
        today = date.today()
        to_date = to_date or today.isoformat()
        from_date = from_date or (today - timedelta(days=180)).isoformat()
        candles: list[dict[str, Any]] = []
        samples: list[dict[str, Any]] = []
        chunks: list[dict[str, Any]] = []
        errors: list[str] = []
        for chunk_from, chunk_to in self._date_chunks(from_date, to_date, interval):
            try:
                payload = await self.client.historical_candles(instrument_key, "minutes", interval, chunk_to, chunk_from)
                chunk_candles = self._parse_candles(payload)
                chunk_samples = self._generate_scalp_samples(symbol, chunk_candles, max(0, target - len(samples)))
                candles.extend(chunk_candles)
                samples.extend(chunk_samples)
                chunks.append({"from": chunk_from, "to": chunk_to, "candles": len(chunk_candles), "samples": len(chunk_samples)})
                if len(samples) >= target:
                    break
            except Exception as exc:
                errors.append(f"{chunk_from}->{chunk_to}: {exc}")
                continue
        learning = await self.learner.train_from_historical_samples(samples)
        return {
            "symbol": symbol,
            "instrumentKey": instrument_key,
            "fromDate": from_date,
            "toDate": to_date,
            "chunks": chunks,
            "chunkErrors": errors[-10:],
            "candles": len(candles),
            "targetTrades": target,
            "generatedTrades": len(samples),
            "enoughSamples": len(samples) >= target,
            "learning": learning,
            "note": "Samples are generated from real Upstox historical candles using deterministic scalp rules; no fake market data is created.",
        }

    async def train_runner(
        self,
        symbol: str = "NIFTY",
        target_trades: int | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        interval: int = 1,
    ) -> dict[str, Any]:
        target = target_trades or self.settings.historical_training_target_trades
        instrument_key = self.settings.instrument_key_for(symbol)
        today = date.today()
        to_date = to_date or today.isoformat()
        from_date = from_date or (today - timedelta(days=180)).isoformat()
        candles: list[dict[str, Any]] = []
        samples: list[dict[str, Any]] = []
        chunks: list[dict[str, Any]] = []
        errors: list[str] = []
        for chunk_from, chunk_to in self._date_chunks(from_date, to_date, interval):
            try:
                payload = await self.client.historical_candles(instrument_key, "minutes", interval, chunk_to, chunk_from)
                chunk_candles = self._parse_candles(payload)
                chunk_samples = self.generate_runner_samples(symbol, chunk_candles, max(0, target - len(samples)))
                candles.extend(chunk_candles)
                samples.extend(chunk_samples)
                chunks.append({"from": chunk_from, "to": chunk_to, "candles": len(chunk_candles), "samples": len(chunk_samples)})
                if len(samples) >= target:
                    break
            except Exception as exc:
                errors.append(f"{chunk_from}->{chunk_to}: {exc}")
                continue
        learning = await self.learner.train_from_historical_samples(samples)
        return {
            "symbol": symbol,
            "instrumentKey": instrument_key,
            "strategyType": "EXPLOSIVE_RUNNER_PROXY",
            "fromDate": from_date,
            "toDate": to_date,
            "chunks": chunks,
            "chunkErrors": errors[-10:],
            "candles": len(candles),
            "targetTrades": target,
            "generatedTrades": len(samples),
            "enoughSamples": len(samples) >= target,
            "learning": learning,
            "note": "Runner training uses real Upstox index candles as proxy labels. Exact option premium runner training requires historical option premium candles.",
        }

    async def train_option_runner(
        self,
        symbol: str = "NIFTY",
        target_trades: int | None = None,
        expiry_date: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        interval: int = 1,
        max_contracts: int = 60,
    ) -> dict[str, Any]:
        target = target_trades or self.settings.historical_training_target_trades
        underlying_key = self.settings.instrument_key_for(symbol)
        requested_expiry = expiry_date or self.settings.expiry_for(symbol)
        warnings: list[str] = []
        today = date.today()
        expiry = requested_expiry if self._valid_date(requested_expiry) else None
        if requested_expiry and not expiry:
            warnings.append(f"Ignored invalid expiry value: {requested_expiry}")
        contracts: list[dict[str, Any]] = []
        if expiry:
            try:
                contracts_payload = await self.client.option_contracts(underlying_key, expiry)
                contracts = contracts_payload.get("data") or []
            except Exception as exc:
                warnings.append(f"Configured expiry {expiry} rejected by Upstox: {exc}")
                expiry = None
        if not expiry:
            contracts_payload = await self.client.option_contracts(underlying_key)
            all_contracts = contracts_payload.get("data") or []
            expiries = sorted({str(item.get("expiry")) for item in all_contracts if self._valid_date(str(item.get("expiry")))})
            if not expiries:
                return {"available": False, "reason": f"No Upstox option expiries returned for {symbol}", "samples": 0, "warnings": warnings}
            future_expiries = [item for item in expiries if date.fromisoformat(item) >= today]
            expiry = future_expiries[0] if future_expiries else expiries[0]
            contracts = [item for item in all_contracts if item.get("expiry") == expiry]
        if not contracts:
            return {"available": False, "reason": f"No option contracts returned for {symbol} expiry {expiry}", "samples": 0, "warnings": warnings}
        selected_contracts = self._select_option_contracts(contracts, max_contracts)
        samples: list[dict[str, Any]] = []
        contract_results: list[dict[str, Any]] = []
        errors: list[str] = list(warnings)
        for contract in selected_contracts:
            instrument_key = contract.get("instrument_key")
            if not instrument_key:
                continue
            contract_from, contract_to = self._contract_history_window(contract, from_date, to_date, today)
            contract_candles: list[dict[str, Any]] = []
            contract_samples: list[dict[str, Any]] = []
            for chunk_from, chunk_to in self._date_chunks(contract_from, contract_to, interval):
                try:
                    payload = await self.client.historical_candles(instrument_key, "minutes", interval, chunk_to, chunk_from)
                    chunk_candles = self._parse_candles(payload)
                    chunk_samples = self._generate_option_runner_samples(symbol, contract, chunk_candles, max(0, target - len(samples)))
                    contract_candles.extend(chunk_candles)
                    contract_samples.extend(chunk_samples)
                    samples.extend(chunk_samples)
                    if len(samples) >= target:
                        break
                except Exception as exc:
                    errors.append(f"{instrument_key} {chunk_from}->{chunk_to}: {exc}")
            contract_results.append({
                "instrumentKey": instrument_key,
                "fromDate": contract_from,
                "toDate": contract_to,
                "tradingSymbol": contract.get("trading_symbol") or contract.get("name"),
                "strike": contract.get("strike_price"),
                "optionType": contract.get("option_type"),
                "candles": len(contract_candles),
                "samples": len(contract_samples),
            })
            if len(samples) >= target:
                break
        learning = await self.learner.train_from_historical_samples(samples)
        return {
            "available": bool(samples),
            "trainingMode": "EXACT_OPTION_PREMIUM_CANDLES" if samples else "UNAVAILABLE",
            "symbol": symbol,
            "underlyingInstrumentKey": underlying_key,
            "expiry": expiry,
            "fromDate": from_date or "auto_contract_window",
            "toDate": to_date or "auto_contract_window",
            "targetTrades": target,
            "generatedTrades": len(samples),
            "enoughSamples": len(samples) >= target,
            "contractsChecked": contract_results,
            "errors": errors[-20:],
            "learning": learning,
            "note": "Uses actual Upstox historical candles for option instrument keys. If no samples are produced, Upstox option premium history is unavailable for the selected contracts/date range.",
        }

    def _valid_date(self, value: str | None) -> bool:
        if not value:
            return False
        try:
            date.fromisoformat(str(value))
            return True
        except ValueError:
            return False

    def _contract_history_window(self, contract: dict[str, Any], from_date: str | None, to_date: str | None, today: date) -> tuple[str, str]:
        expiry_raw = contract.get("expiry") or contract.get("expiry_date")
        try:
            expiry = date.fromisoformat(str(expiry_raw)) if expiry_raw else today
        except ValueError:
            expiry = today
        end = date.fromisoformat(to_date) if to_date else min(today, expiry)
        start = date.fromisoformat(from_date) if from_date else end - timedelta(days=28)
        end = min(end, today, expiry)
        if start > end:
            start = max(end - timedelta(days=7), date(2022, 1, 1))
        return start.isoformat(), end.isoformat()

    def _select_option_contracts(self, contracts: list[dict[str, Any]], max_contracts: int) -> list[dict[str, Any]]:
        def sort_key(item: dict[str, Any]) -> tuple[int, float]:
            option_type = str(item.get("option_type") or item.get("optionType") or "")
            strike = float(item.get("strike_price") or item.get("strike") or 0)
            # Prefer index options with available strike; keep CE/PE balanced by sorting around middle later.
            return (0 if option_type in {"CE", "PE", "CALL", "PUT"} else 1, strike)
        ordered = sorted(contracts, key=sort_key)
        if len(ordered) <= max_contracts:
            return ordered
        mid = len(ordered) // 2
        half = max_contracts // 2
        return ordered[max(0, mid - half): min(len(ordered), mid + half)]

    def _generate_option_runner_samples(self, symbol: str, contract: dict[str, Any], candles: list[dict[str, Any]], target: int) -> list[dict[str, Any]]:
        samples: list[dict[str, Any]] = []
        if len(candles) < 20:
            return samples
        instrument_key = contract.get("instrument_key")
        side = "CALL" if str(contract.get("option_type") or "").upper() in {"CE", "CALL"} else "PUT"
        for index in range(12, len(candles) - 8):
            window = candles[index - 10:index]
            current = candles[index]
            future = candles[index + 1:index + 8]
            avg_volume = sum(candle["volume"] for candle in window) / len(window) if window else 0
            high = max(candle["high"] for candle in window)
            low = min(candle["low"] for candle in window)
            atr = sum(abs(candle["high"] - candle["low"]) for candle in window) / len(window)
            if current["close"] <= high or (avg_volume and current["volume"] < avg_volume * 1.2):
                continue
            entry = current["close"]
            if entry <= 0:
                continue
            if not (self.settings.explosive_runner_premium_min <= entry <= self.settings.explosive_runner_premium_max):
                continue
            mfe = max(candle["high"] - entry for candle in future)
            mae = min(candle["low"] - entry for candle in future)
            target_30 = entry * 0.30
            target_50 = entry * 0.50
            target_100 = entry
            best_pct = (mfe / entry) * 100
            stop = max(entry * 0.12, atr * 0.6, 0.5)
            if mfe >= target_100:
                pnl = target_50 + (mfe - target_50) * 0.55
                outcome = "+100pct_runner"
            elif mfe >= target_50:
                pnl = target_30 + (mfe - target_30) * 0.45
                outcome = "+50pct_runner"
            elif mfe >= target_30:
                pnl = target_30 * 0.7
                outcome = "+30pct_runner"
            else:
                pnl = max(mae, -stop)
                outcome = "runner_failed"
            pnl -= max(0.1, entry * 0.003)
            tqs = max(35, min(99, 62 + min(best_pct, 120) * 0.18 + (10 if current["volume"] >= (avg_volume or 0) * 1.5 else 0)))
            samples.append({
                "symbol": symbol,
                "instrumentKey": instrument_key,
                "time": current["time"],
                "side": side,
                "entry": round(entry, 2),
                "pnl": round(pnl, 2),
                "tqs": round(tqs),
                "volume": current["volume"],
                "atr": round(atr, 2),
                "bestMovePct": round(best_pct, 2),
                "regime": "TREND_EXPANSION" if pnl > 0 else "REVERSAL_RISK",
                "strategyType": "EXPLOSIVE_RUNNER_EXACT_OPTION_PREMIUM",
                "outcome": outcome,
            })
            if len(samples) >= target:
                break
        return samples

    def _date_chunks(self, from_date: str, to_date: str, interval: int) -> list[tuple[str, str]]:
        start = date.fromisoformat(from_date)
        end = date.fromisoformat(to_date)
        if start > end:
            start, end = end, start
        max_days = 28 if interval <= 15 else 85
        chunks: list[tuple[str, str]] = []
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
            parsed.append({
                "time": str(candle[0]),
                "open": as_float(candle[1]),
                "high": as_float(candle[2]),
                "low": as_float(candle[3]),
                "close": as_float(candle[4]),
                "volume": as_int(candle[5]),
            })
        return list(reversed(parsed))

    def generate_runner_samples(self, symbol: str, candles: list[dict[str, Any]], target: int) -> list[dict[str, Any]]:
        samples: list[dict[str, Any]] = []
        if len(candles) < 30:
            return samples
        for index in range(20, len(candles) - 10):
            window = candles[index - 15:index]
            future = candles[index + 1:index + 10]
            current = candles[index]
            atr = sum(abs(candle["high"] - candle["low"]) for candle in window) / len(window)
            if atr <= 0:
                continue
            high = max(candle["high"] for candle in window)
            low = min(candle["low"] for candle in window)
            avg_volume = sum(candle["volume"] for candle in window) / len(window) if window else 0
            volume_ok = current["volume"] >= avg_volume * 1.3 if avg_volume else True
            side = None
            if current["close"] > high and volume_ok:
                side = "CALL"
            elif current["close"] < low and volume_ok:
                side = "PUT"
            if not side:
                continue
            entry = current["close"]
            if side == "CALL":
                mfe = max(candle["high"] - entry for candle in future)
                mae = min(candle["low"] - entry for candle in future)
                pnl = max(-2.5, mfe * 0.7 if mfe >= atr * 1.5 else mae)
            else:
                mfe = max(entry - candle["low"] for candle in future)
                mae = min(entry - candle["high"] for candle in future)
                pnl = max(-2.5, mfe * 0.7 if mfe >= atr * 1.5 else mae)
            tqs = max(40, min(98, 62 + (mfe / atr) * 8 + (10 if volume_ok else 0)))
            samples.append({
                "symbol": symbol,
                "time": current["time"],
                "side": side,
                "entry": round(entry, 2),
                "pnl": round(pnl, 2),
                "tqs": round(tqs),
                "volume": current["volume"],
                "atr": round(atr, 2),
                "regime": "TREND_EXPANSION" if pnl > 0 else "REVERSAL_RISK",
                "strategyType": "EXPLOSIVE_RUNNER_PROXY",
            })
            if len(samples) >= target:
                break
        return samples

    def _generate_scalp_samples(self, symbol: str, candles: list[dict[str, Any]], target: int) -> list[dict[str, Any]]:
        samples: list[dict[str, Any]] = []
        if len(candles) < 20:
            return samples
        for index in range(15, len(candles) - 6):
            window = candles[index - 10:index]
            current = candles[index]
            future = candles[index + 1:index + 7]
            high = max(candle["high"] for candle in window)
            low = min(candle["low"] for candle in window)
            avg_volume = sum(candle["volume"] for candle in window) / len(window) if window else 0
            atr = sum(abs(candle["high"] - candle["low"]) for candle in window) / len(window)
            previous_close = candles[index - 1]["close"]
            move = abs(current["close"] - previous_close)
            volume_confirmed = current["volume"] >= avg_volume * 1.15 if avg_volume else True
            breakout_strength = move / atr if atr else 0

            direction = None
            if current["close"] > high and breakout_strength >= 0.35:
                direction = "CALL"
            elif current["close"] < low and breakout_strength >= 0.35:
                direction = "PUT"
            if not direction or not volume_confirmed:
                continue

            entry = current["close"]
            target_points = max(float(self.settings.paper_target_points), atr * 1.2)
            initial_stop = max(float(self.settings.paper_stop_points), atr * 0.65)
            breakeven_shift = float(self.settings.paper_breakeven_shift_points)
            trail_distance = max(2.0, atr * 0.5)
            partial_taken = False
            breakeven_armed = False
            stop_price = entry - initial_stop if direction == "CALL" else entry + initial_stop
            best_favorable = entry
            exit_price = entry
            exit_reason = "time_stop"

            for candle in future:
                if direction == "CALL":
                    best_favorable = max(best_favorable, candle["high"])
                    if candle["low"] <= stop_price:
                        exit_price = stop_price
                        exit_reason = "fast_stop_or_delta_reversal"
                        break
                    if not breakeven_armed and candle["high"] >= entry + breakeven_shift:
                        breakeven_armed = True
                        stop_price = max(stop_price, entry)
                    if not partial_taken and candle["high"] >= entry + target_points:
                        partial_taken = True
                        stop_price = max(stop_price, entry + breakeven_shift * 0.25)
                    if partial_taken:
                        stop_price = max(stop_price, best_favorable - trail_distance)
                    exit_price = candle["close"]
                else:
                    best_favorable = min(best_favorable, candle["low"])
                    if candle["high"] >= stop_price:
                        exit_price = stop_price
                        exit_reason = "fast_stop_or_delta_reversal"
                        break
                    if not breakeven_armed and candle["low"] <= entry - breakeven_shift:
                        breakeven_armed = True
                        stop_price = min(stop_price, entry)
                    if not partial_taken and candle["low"] <= entry - target_points:
                        partial_taken = True
                        stop_price = min(stop_price, entry - breakeven_shift * 0.25)
                    if partial_taken:
                        stop_price = min(stop_price, best_favorable + trail_distance)
                    exit_price = candle["close"]

            if exit_reason == "time_stop" and partial_taken:
                exit_reason = "trailing_profit_lock"
            raw_pnl = (exit_price - entry) if direction == "CALL" else (entry - exit_price)
            # Conservative transaction cost/slippage estimate for scalping.
            pnl = raw_pnl - 0.35
            tqs = max(35, min(95, 58 + breakout_strength * 10 + (15 if volume_confirmed else 0) + (8 if partial_taken else 0)))
            if tqs < 64:
                continue
            regime = "TREND_EXPANSION" if volume_confirmed and partial_taken else "RANGE_ABSORPTION" if pnl >= 0 else "REVERSAL_RISK"
            samples.append({
                "symbol": symbol,
                "time": current["time"],
                "side": direction,
                "entry": round(entry, 2),
                "exit": round(exit_price, 2),
                "exitReason": exit_reason,
                "pnl": round(pnl, 2),
                "tqs": round(tqs),
                "volume": current["volume"],
                "atr": round(atr, 2),
                "regime": regime,
            })
            if len(samples) >= target:
                break
        return samples
