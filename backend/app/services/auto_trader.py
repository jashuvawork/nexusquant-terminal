from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import uuid4

from app.core.config import Settings
from app.services.ai_learning import ContinuousAILearner
from app.services.paper_session_manager import PaperSessionManager
from app.services.risk_profiles import paper_session_adjustments
from app.services.session import MarketPhase
from app.services.trading_control import TradingControl


ORDER_STATES = [
    "SIGNAL_GENERATED",
    "RISK_CHECKED",
    "PAPER_OPENED",
    "ORDER_SUBMITTED",
    "ORDER_ACCEPTED",
    "PARTIAL_FILL",
    "FILLED",
    "REJECTED",
    "MODIFIED",
    "CANCELLED",
    "EXITED",
]


@dataclass
class LifecycleEvent:
    state: str
    timestamp: str
    reason: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class PaperTrade:
    id: str
    symbol: str
    side: str
    strike: int
    expiry: str
    instrument_key: str | None
    entry_price: float
    quantity: int
    entry_tqs: int
    spread_cost: float
    slippage_estimate: float
    charges_estimate: float
    opened_at: str
    mode: str
    strategy_type: str = "SCALP"
    paper_session_id: str = ""
    target_points: float = 0.0
    stop_points: float = 0.0
    breakeven_shift_points: float = 0.0
    trail_points: float = 0.0
    status: str = "OPEN"
    exit_price: float | None = None
    exit_reason: str | None = None
    exited_at: str | None = None
    pnl: float = 0.0
    best_price: float = 0.0
    breakeven_armed: bool = False
    partial_exit_taken: bool = False
    lifecycle: list[LifecycleEvent] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> PaperTrade:
        lifecycle = [
            LifecycleEvent(
                state=str(event.get("state") or ""),
                timestamp=str(event.get("timestamp") or ""),
                reason=str(event.get("reason") or ""),
                payload=dict(event.get("payload") or {}),
            )
            for event in (payload.get("lifecycle") or [])
            if isinstance(event, dict)
        ]
        return cls(
            id=str(payload.get("id") or uuid4()),
            symbol=str(payload.get("symbol") or ""),
            side=str(payload.get("side") or ""),
            strike=int(payload.get("strike") or 0),
            expiry=str(payload.get("expiry") or ""),
            instrument_key=payload.get("instrumentKey"),
            entry_price=float(payload.get("entryPrice") or 0),
            quantity=int(payload.get("quantity") or 1),
            entry_tqs=int(payload.get("entryTqs") or 0),
            spread_cost=float(payload.get("spreadCost") or 0),
            slippage_estimate=float(payload.get("slippageEstimate") or 0),
            charges_estimate=float(payload.get("chargesEstimate") or 0),
            opened_at=str(payload.get("openedAt") or datetime.now(timezone.utc).isoformat()),
            mode=str(payload.get("mode") or "paper"),
            strategy_type=str(payload.get("strategyType") or "SCALP"),
            paper_session_id=str(payload.get("paperSessionId") or ""),
            target_points=float(payload.get("targetPoints") or 0),
            stop_points=float(payload.get("stopPoints") or 0),
            breakeven_shift_points=float(payload.get("breakevenShiftPoints") or 0),
            trail_points=float(payload.get("trailPoints") or 0),
            status=str(payload.get("status") or "OPEN"),
            exit_price=payload.get("exitPrice"),
            exit_reason=payload.get("exitReason"),
            exited_at=payload.get("exitedAt"),
            pnl=float(payload.get("pnl") or 0),
            best_price=float(payload.get("bestPrice") or payload.get("entryPrice") or 0),
            breakeven_armed=bool(payload.get("breakevenArmed")),
            partial_exit_taken=bool(payload.get("partialExitTaken")),
            lifecycle=lifecycle,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "side": self.side,
            "strike": self.strike,
            "expiry": self.expiry,
            "instrumentKey": self.instrument_key,
            "entryPrice": self.entry_price,
            "quantity": self.quantity,
            "entryTqs": self.entry_tqs,
            "spreadCost": self.spread_cost,
            "slippageEstimate": self.slippage_estimate,
            "chargesEstimate": self.charges_estimate,
            "openedAt": self.opened_at,
            "mode": self.mode,
            "strategyType": self.strategy_type,
            "paperSessionId": self.paper_session_id,
            "targetPoints": round(self.target_points, 2),
            "stopPoints": round(self.stop_points, 2),
            "breakevenShiftPoints": round(self.breakeven_shift_points, 2),
            "trailPoints": round(self.trail_points, 2),
            "status": self.status,
            "exitPrice": self.exit_price,
            "exitReason": self.exit_reason,
            "exitedAt": self.exited_at,
            "pnl": round(self.pnl, 2),
            "bestPrice": self.best_price,
            "breakevenArmed": self.breakeven_armed,
            "partialExitTaken": self.partial_exit_taken,
            "lifecycle": [event.__dict__ for event in self.lifecycle],
        }


class AutoTraderEngine:
    """Paper trading, replay, lifecycle, sizing and online-learning coordinator.

    This layer does not fabricate market data. It consumes snapshots produced by
    the Upstox-only market engine and records decisions/outcomes around those
    real-data-derived signals.
    """

    _shared_replay_buffer: deque[dict[str, Any]] = deque(maxlen=20_000)
    _shared_open_paper: dict[str, PaperTrade] = {}
    _shared_closed_paper: deque[PaperTrade] = deque(maxlen=2_000)
    _shared_lifecycle_events: deque[LifecycleEvent] = deque(maxlen=5_000)
    _shared_learning_samples = 0
    _shared_learning_score = 50.0
    _shared_last_learning_update: str | None = None
    _shared_recent_signal_times: dict[str, float] = {}

    def __init__(self, settings: Settings, trading_control: TradingControl, learner: ContinuousAILearner | None = None) -> None:
        self.settings = settings
        self.trading_control = trading_control
        self.learner = learner or ContinuousAILearner(settings.redis_url, settings.ai_learning_enabled)
        self.paper_sessions = PaperSessionManager(settings)
        self.replay_buffer = AutoTraderEngine._shared_replay_buffer
        self.open_paper = AutoTraderEngine._shared_open_paper
        self.closed_paper = AutoTraderEngine._shared_closed_paper
        self.lifecycle_events = AutoTraderEngine._shared_lifecycle_events
        self._load_replay_file()
        self._load_paper_trades_file()

    async def process(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        replay_item = {"timestamp": now, "payload": self._compact_snapshot(payload)}
        self.replay_buffer.append(replay_item)
        self._append_replay_file(replay_item)
        trading_control = await self.trading_control.status()
        capital = await self.trading_control.capital_status()
        trading_capital = float(capital.get("tradingCapital") or self.settings.trading_capital_default or 0)
        session_adj = self._paper_session_settings(payload)
        candidates = payload.get("executionCandidates") or []
        snapshots = payload.get("snapshots") or {payload.get("symbol", "NIFTY"): payload}
        cached_snapshot = self._all_snapshots_cached(snapshots)
        signal_cooldown_seconds = int(session_adj.get("duplicateCooldownSeconds") or self.settings.paper_duplicate_signal_cooldown_seconds)
        market_phase = str(payload.get("marketPhase") or MarketPhase.LIVE_MARKET.value)

        pre_trade_psychology = self._psychology_report([], [], self._paper_risk_halt(trading_capital), session_adj)
        exits = self._update_open_paper(snapshots, pre_trade_psychology, session_adj)
        rotation_event = self._maybe_rotate_paper_session(snapshots, session_adj, trading_capital)
        risk_halt = self._paper_risk_halt(trading_capital, session_adj)
        pre_trade_psychology = self._psychology_report([], [], risk_halt, session_adj)

        signal_events = []
        skipped = []
        for candidate in candidates:
            event = self._signal_event(candidate, payload)
            signal_events.append(event)
            self.lifecycle_events.append(event)
            signal_id = str(candidate.get("id") or "")
            if cached_snapshot:
                skipped.append({"candidate": signal_id, "reason": "cached snapshot; paper open skipped to avoid duplicate training sample"})
                continue
            if self._recent_signal_active(signal_id, signal_cooldown_seconds):
                skipped.append({"candidate": signal_id, "reason": "duplicate signal cooldown active"})
                continue
            if session_adj.get("blockNewPaperTrades") and not self._session_entry_allowed(candidate, session_adj, market_phase):
                skipped.append({"candidate": candidate.get("id"), "reason": session_adj.get("blockReason") or "session gate blocked"})
                continue
            quality = self._pre_trade_quality(candidate, session_adj, market_phase)
            if quality["blocked"]:
                skipped.append({"candidate": candidate.get("id"), "reason": quality["reason"], "quality": quality})
                if not (self.settings.paper_trading and self.settings.shadow_trade_all_signals and quality.get("paperEligible")):
                    continue
                quality = {**quality, "shadowOverride": True, "reason": f"SHADOW PAPER despite rejection: {quality['reason']}"}
            if trading_control.get("autoTradingStopped") and self.settings.paper_trading_respects_stop:
                skipped.append({"candidate": candidate.get("id"), "reason": "manual stop active", "quality": quality})
                continue
            if risk_halt["blocked"]:
                skipped.append({"candidate": candidate.get("id"), "reason": risk_halt["reason"], "quality": quality})
                continue
            if pre_trade_psychology.get("tradePermission") in {"WAIT", "BLOCK_NEW_TRADES"} and not self._is_tradeable_explosive_runner(candidate, market_phase):
                skipped.append({"candidate": candidate.get("id"), "reason": f"psychology gate: {pre_trade_psychology.get('tradePermission')}", "quality": quality})
                continue
            if self.settings.paper_trading or not self.settings.enable_live_trading:
                opened = self._open_paper_trade(candidate, quality, self._available_capital(trading_capital), trading_capital, session_adj, market_phase)
                if opened:
                    signal_events.append(opened.lifecycle[-1])
        online_learning = await self.learner.update_from_tick(payload, exits, "live" if self.settings.enable_live_trading and not self.settings.paper_trading else "paper")
        self._learn_every_tick(payload, exits)
        profit_lock = self.profit_lock_status(capital.get("tradingCapital", 0), session_adj)
        psychology = self._psychology_report(candidates, skipped, risk_halt, session_adj)
        return {
            "paperTrading": self.settings.paper_trading,
            "shadowTradeAllSignals": self.settings.shadow_trade_all_signals,
            "paperTradingRespectsStop": self.settings.paper_trading_respects_stop,
            "liveTradingEnabled": self.settings.enable_live_trading,
            "autoTradingStopped": bool(trading_control.get("autoTradingStopped")),
            "capital": capital,
            "signalsThisTick": len(candidates),
            "skippedSignals": skipped[-10:],
            "openPaperTrades": [trade.to_dict() for trade in self.open_paper.values()],
            "closedPaperTrades": [trade.to_dict() for trade in list(self.closed_paper)[-25:]],
            "orderLifecycle": [event.__dict__ for event in list(self.lifecycle_events)[-50:]],
            "replay": {"storedSnapshots": len(self.replay_buffer), "latestTimestamp": now},
            "exitEngine": {
                "rules": ["momentum decay", "delta reversal", "spread widening", "liquidity rejection", "time stop", "trailing profit lock", "partial exit placeholder", "emergency flatten"],
                "exitsThisTick": exits,
            },
            "slippageModel": self._slippage_summary(candidates),
            "positionSizing": self._position_sizing_summary(candidates, capital.get("tradingCapital", 0)),
            "sessionAdjustments": session_adj,
            "profitLock": profit_lock,
            "paperRiskHalt": risk_halt,
            "psychology": psychology,
            "onlineLearning": online_learning,
            "dailyReport": self.daily_report(),
            "paperSessions": self.paper_sessions.status_payload(self._session_report()),
            "sessionRotation": rotation_event,
        }

    def status(self) -> dict[str, Any]:
        return {
            "paperTrading": self.settings.paper_trading,
            "shadowTradeAllSignals": self.settings.shadow_trade_all_signals,
            "paperTradingRespectsStop": self.settings.paper_trading_respects_stop,
            "openPaperTrades": [trade.to_dict() for trade in self.open_paper.values()],
            "closedPaperTrades": [trade.to_dict() for trade in list(self.closed_paper)[-25:]],
            "orderLifecycle": [event.__dict__ for event in list(self.lifecycle_events)[-50:]],
            "replay": {"storedSnapshots": len(self.replay_buffer)},
            "profitLock": self.profit_lock_status(),
            "paperRiskHalt": self._paper_risk_halt(),
            "psychology": self._psychology_report([], [], self._paper_risk_halt()),
            "onlineLearning": self.learner.status_from_state(),
            "dailyReport": self.daily_report(),
            "paperSessions": self.paper_sessions.status_payload(self._session_report()),
        }

    def paper_sessions_history(self, limit: int = 50) -> dict[str, Any]:
        return self.paper_sessions.list_sessions(limit)

    def reset(self) -> dict[str, Any]:
        self.replay_buffer.clear()
        self.open_paper.clear()
        self.closed_paper.clear()
        self.lifecycle_events.clear()
        AutoTraderEngine._shared_recent_signal_times.clear()
        self._persist_paper_trades_file()
        AutoTraderEngine._shared_learning_samples = 0
        AutoTraderEngine._shared_learning_score = 50.0
        AutoTraderEngine._shared_last_learning_update = None
        return {"reset": True, "status": self.status()}

    def replay(self, limit: int = 250) -> dict[str, Any]:
        return {"snapshots": list(self.replay_buffer)[-limit:], "count": min(limit, len(self.replay_buffer))}

    def _load_replay_file(self) -> None:
        if self.replay_buffer or not self.settings.paper_replay_file:
            return
        path = Path(self.settings.paper_replay_file)
        if not path.exists():
            return
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            for line in lines[-self.replay_buffer.maxlen:]:
                if not line.strip():
                    continue
                item = json.loads(line)
                if isinstance(item, dict) and item.get("payload"):
                    self.replay_buffer.append(item)
        except Exception:
            return

    def _append_replay_file(self, item: dict[str, Any]) -> None:
        if not self.settings.paper_replay_file:
            return
        try:
            path = Path(self.settings.paper_replay_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(item, separators=(",", ":")) + "\n")
            limit = max(1000, int(self.settings.paper_replay_persist_limit))
            # Keep the replay file bounded so a long paper session does not grow indefinitely.
            lines = path.read_text(encoding="utf-8").splitlines()
            if len(lines) > limit:
                path.write_text("\n".join(lines[-limit:]) + "\n", encoding="utf-8")
        except Exception:
            return

    def _load_paper_trades_file(self) -> None:
        if (self.open_paper or self.closed_paper) or not self.settings.paper_trades_file:
            return
        path = Path(self.settings.paper_trades_file)
        if not path.exists():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return
            for item in payload.get("open") or []:
                if not isinstance(item, dict):
                    continue
                trade = PaperTrade.from_dict(item)
                if trade.status == "OPEN":
                    self.open_paper[trade.id] = trade
            limit = max(100, int(self.settings.paper_trades_persist_limit))
            for item in (payload.get("closed") or [])[-limit:]:
                if not isinstance(item, dict):
                    continue
                trade = PaperTrade.from_dict(item)
                if trade.status == "EXITED":
                    self.closed_paper.append(trade)
        except Exception:
            return

    def _persist_paper_trades_file(self) -> None:
        if not self.settings.paper_trades_file:
            return
        try:
            limit = max(100, int(self.settings.paper_trades_persist_limit))
            closed = [trade.to_dict() for trade in list(self.closed_paper)[-limit:]]
            payload = {
                "updatedAt": datetime.now(timezone.utc).isoformat(),
                "currentSessionId": self.paper_sessions.current_id(),
                "open": [trade.to_dict() for trade in self.open_paper.values()],
                "closed": closed,
            }
            path = Path(self.settings.paper_trades_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
            temp.replace(path)
            path.chmod(0o600)
        except Exception:
            return

    async def backtest_missed_trades(
        self,
        *,
        horizon_ticks: int = 60,
        min_profit_points: float = 8.0,
        include_losses: bool = True,
        target_trades: int = 500,
    ) -> dict[str, Any]:
        snapshots = list(self.replay_buffer)
        samples = self._build_replay_training_samples(
            snapshots,
            horizon_ticks=horizon_ticks,
            min_profit_points=min_profit_points,
            include_losses=include_losses,
            target_trades=target_trades,
        )
        wins = [sample for sample in samples if float(sample.get("pnl") or 0) > 0]
        losses = [sample for sample in samples if float(sample.get("pnl") or 0) < 0]
        capital = float(self.settings.trading_capital_default or 0)
        simulated_pnl = 0.0
        trade_rows: list[dict[str, Any]] = []
        for sample in samples:
            entry = float(sample.get("entry") or 0)
            quantity = max(1, int(sample.get("quantity") or 1))
            unit_pnl = float(sample.get("pnl") or 0)
            trade_pnl = round(unit_pnl * quantity, 2)
            simulated_pnl += trade_pnl
            if sample.get("outcome") in {"missed_profitable_move", "missed_momentum_runner"}:
                trade_rows.append({
                    "time": sample.get("time"),
                    "symbol": sample.get("symbol"),
                    "side": sample.get("side"),
                    "strike": sample.get("strike"),
                    "entry": entry,
                    "bestMovePoints": sample.get("bestMovePoints"),
                    "simulatedPnl": trade_pnl,
                    "strategyType": sample.get("strategyType"),
                    "momentumSurge": sample.get("momentumSurge"),
                    "wouldBlockReason": sample.get("wouldBlockReason"),
                })
        gross_profit = sum(float(sample.get("pnl") or 0) * max(1, int(sample.get("quantity") or 1)) for sample in wins)
        gross_loss = abs(sum(float(sample.get("pnl") or 0) * max(1, int(sample.get("quantity") or 1)) for sample in losses))
        pf = round(gross_profit / gross_loss, 3) if gross_loss else round(gross_profit, 3)
        win_rate = round((len(wins) / len(samples)) * 100, 2) if samples else 0.0
        return {
            "available": bool(samples),
            "replaySnapshots": len(snapshots),
            "missedTrades": len(trade_rows),
            "samples": len(samples),
            "wins": len(wins),
            "losses": len(losses),
            "winRatePct": win_rate,
            "profitFactor": pf,
            "simulatedNetPnl": round(simulated_pnl, 2),
            "simulatedNetPnlPct": round(simulated_pnl / capital * 100, 2) if capital > 0 else 0.0,
            "grossProfit": round(gross_profit, 2),
            "grossLoss": round(gross_loss, 2),
            "runnerMissed": sum(1 for row in trade_rows if "RUNNER" in str(row.get("strategyType") or "")),
            "momentumMissed": sum(1 for row in trade_rows if row.get("momentumSurge")),
            "topMissed": sorted(trade_rows, key=lambda row: float(row.get("simulatedPnl") or 0), reverse=True)[:15],
            "horizonTicks": horizon_ticks,
            "minProfitPoints": min_profit_points,
        }

    async def backtest_and_train_missed_today(
        self,
        target_trades: int = 500,
        horizon_ticks: int = 60,
        min_profit_points: float = 8.0,
        include_losses: bool = True,
    ) -> dict[str, Any]:
        backtest = await self.backtest_missed_trades(
            horizon_ticks=horizon_ticks,
            min_profit_points=min_profit_points,
            include_losses=include_losses,
            target_trades=target_trades,
        )
        training = await self.train_replay_opportunities(target_trades, horizon_ticks, min_profit_points, include_losses)
        calibration = self._learning_calibration()
        return {
            **backtest,
            "training": training,
            "appliedCalibration": calibration,
            "effectiveMomentumMinScore": round(self._runner_min_score({"momentumSurge": True, "momentumAligned": True}), 2),
            "effectiveRunnerMinScore": round(self._runner_min_score({}), 2),
            "note": "Backtest labels missed replay opportunities; training nudges AI calibration toward momentum/chart-aligned winners.",
        }

    async def train_replay_opportunities(
        self,
        target_trades: int = 500,
        horizon_ticks: int = 60,
        min_profit_points: float = 8.0,
        include_losses: bool = True,
    ) -> dict[str, Any]:
        snapshots = list(self.replay_buffer)
        samples = self._build_replay_training_samples(
            snapshots,
            horizon_ticks=horizon_ticks,
            min_profit_points=min_profit_points,
            include_losses=include_losses,
            target_trades=target_trades,
        )
        learning = await self.learner.train_from_historical_samples(samples)
        wins = [sample for sample in samples if float(sample.get("pnl") or 0) > 0]
        losses = [sample for sample in samples if float(sample.get("pnl") or 0) < 0]
        return {
            "available": bool(samples),
            "trainingMode": "TODAY_REPLAY_MISSED_OPPORTUNITIES",
            "targetTrades": target_trades,
            "samplesAdded": len(samples),
            "wins": len(wins),
            "losses": len(losses),
            "winRatePct": round((len(wins) / len(samples)) * 100, 2) if samples else 0.0,
            "grossProfit": round(sum(float(sample.get("pnl") or 0) for sample in wins), 2),
            "grossLoss": round(abs(sum(float(sample.get("pnl") or 0) for sample in losses)), 2),
            "chartAlignedWins": sum(1 for sample in wins if sample.get("chartAligned")),
            "runnerSamples": sum(1 for sample in samples if "RUNNER" in str(sample.get("strategyType") or "")),
            "momentumSamples": sum(1 for sample in samples if sample.get("momentumSurge")),
            "horizonTicks": horizon_ticks,
            "minProfitPoints": min_profit_points,
            "learning": learning,
            "note": "Replay training labels today's candidates by future option premium movement. It trains both missed winners and avoid/wait losers with chart context.",
        }

    def _build_replay_training_samples(
        self,
        snapshots: list[dict[str, Any]],
        *,
        horizon_ticks: int,
        min_profit_points: float,
        include_losses: bool,
        target_trades: int,
    ) -> list[dict[str, Any]]:
        samples: list[dict[str, Any]] = []
        seen: set[str] = set()
        horizon_ticks = max(1, int(horizon_ticks))
        min_profit_points = max(0.5, float(min_profit_points))
        target_trades = max(1, int(target_trades))
        closed_ids = {trade.id for trade in self.closed_paper}
        open_ids = set(self.open_paper.keys())

        for index, replay_item in enumerate(snapshots):
            payload = replay_item.get("payload") or {}
            timestamp = str(replay_item.get("timestamp") or payload.get("timestamp") or "")
            minute_bucket = timestamp[:16]
            for candidate in self._iter_replay_candidates(payload):
                candidate_id = str(candidate.get("id") or "")
                instrument = str(candidate.get("instrumentKey") or "")
                entry = float(candidate.get("lastPremium") or candidate.get("premium") or 0)
                if entry <= 0 or not (candidate_id or instrument):
                    continue
                if candidate_id in closed_ids or candidate_id in open_ids:
                    continue
                dedupe_key = f"{candidate_id or instrument}:{minute_bucket}"
                if dedupe_key in seen:
                    continue
                future_prices = self._future_candidate_prices(snapshots, index, horizon_ticks, candidate_id, instrument)
                if not future_prices:
                    continue
                seen.add(dedupe_key)
                best = max(future_prices)
                worst = min(future_prices)
                final = future_prices[-1]
                mfe = best - entry
                mae = worst - entry
                quantity = max(1, int(candidate.get("quantityEstimate") or candidate.get("lotSize") or 1))
                costs_per_unit = self._charges_estimate(entry, final, quantity) / quantity
                costs_per_unit += max(0.0, entry * 0.004) + max(0.05, entry * 0.002)
                chart_bias = candidate.get("chartBias")
                side = candidate.get("side")
                chart_aligned = chart_bias in {"CALL", "PUT"} and side == chart_bias
                runner = candidate.get("runnerSignal") or candidate
                runner_score = float(runner.get("score") or candidate.get("score") or 0)
                metrics = runner.get("metrics") or {}
                breakout = float(metrics.get("breakoutVelocity") or 0)
                delta_velocity = abs(float(metrics.get("deltaVelocity") or 0))
                premium_velocity = float(metrics.get("premiumVelocity") or 0)
                momentum_surge = bool(runner.get("momentumSurge") or candidate.get("momentumSurge") or premium_velocity >= float(self.settings.explosive_runner_momentum_premium_velocity_pct))
                momentum_aligned = bool(runner.get("momentumAligned") or candidate.get("momentumAligned"))
                strategy_type = str(candidate.get("strategyType") or ("EXPLOSIVE_RUNNER" if "RUNNER" in candidate_id else "REPLAY_CANDIDATE"))

                if mfe >= min_profit_points:
                    pnl = mfe - costs_per_unit
                    outcome = "missed_momentum_runner" if momentum_surge and momentum_aligned else "missed_profitable_move"
                elif include_losses:
                    pnl = min(final - entry, mae) - costs_per_unit
                    outcome = "avoid_or_wait"
                else:
                    continue

                regime = "TREND_EXPANSION" if pnl > 0 and (chart_aligned or momentum_aligned) and breakout >= 55 else "REVERSAL_RISK" if pnl < 0 else "RANGE_ABSORPTION"
                would_block = []
                if candidate.get("chopBlocked"):
                    would_block.append("chop filter")
                if chart_bias in {"CALL", "PUT"} and side in {"CALL", "PUT"} and side != chart_bias and not momentum_aligned:
                    would_block.append("chart conflict")
                if runner_score < float(self.settings.explosive_runner_min_score) and strategy_type == "EXPLOSIVE_RUNNER":
                    would_block.append("runner score")

                samples.append({
                    "symbol": candidate.get("symbol"),
                    "instrumentKey": instrument,
                    "time": timestamp,
                    "side": side,
                    "strike": candidate.get("strike"),
                    "entry": round(entry, 2),
                    "pnl": round(pnl, 2),
                    "quantity": quantity,
                    "tqs": round(float(candidate.get("tqs") or runner_score or 0)),
                    "chartBias": chart_bias,
                    "chartTrend": candidate.get("chartTrend"),
                    "chartAligned": chart_aligned,
                    "runnerScore": runner_score,
                    "breakoutVelocity": breakout,
                    "deltaVelocity": delta_velocity,
                    "premiumVelocity": premium_velocity,
                    "momentumSurge": momentum_surge,
                    "momentumAligned": momentum_aligned,
                    "bestMovePoints": round(mfe, 2),
                    "worstMovePoints": round(mae, 2),
                    "regime": regime,
                    "strategyType": strategy_type,
                    "outcome": outcome,
                    "wouldBlockReason": ", ".join(would_block) if would_block else None,
                })
                if len(samples) >= target_trades:
                    break
            if len(samples) >= target_trades:
                break
        return samples

    def _iter_replay_candidates(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for candidate in payload.get("executionCandidates") or []:
            candidates.append(candidate)
        nested = payload.get("snapshots") or {}
        if isinstance(nested, dict):
            for snapshot in nested.values():
                if not isinstance(snapshot, dict):
                    continue
                for runner in snapshot.get("explosiveRunnerWatchlist") or []:
                    if not isinstance(runner, dict):
                        continue
                    candidates.append({
                        "id": runner.get("id"),
                        "symbol": runner.get("symbol") or snapshot.get("symbol"),
                        "side": runner.get("side"),
                        "strike": runner.get("strike"),
                        "expiry": runner.get("expiry"),
                        "instrumentKey": runner.get("instrumentKey"),
                        "lastPremium": runner.get("lastPremium") or runner.get("premium"),
                        "strategyType": "EXPLOSIVE_RUNNER",
                        "runnerSignal": runner,
                        "chartBias": (snapshot.get("chartAnalysis") or {}).get("bias"),
                        "chartTrend": (snapshot.get("chartAnalysis") or {}).get("trend"),
                        "tqs": runner.get("score"),
                        "momentumSurge": runner.get("momentumSurge"),
                        "momentumAligned": runner.get("momentumAligned"),
                        "score": runner.get("score"),
                    })
        return candidates

    def _future_candidate_prices(self, snapshots: list[dict[str, Any]], index: int, horizon_ticks: int, candidate_id: str, instrument: str) -> list[float]:
        prices: list[float] = []
        for future in snapshots[index + 1:index + 1 + horizon_ticks]:
            payload = future.get("payload") or {}
            for candidate in self._iter_replay_candidates(payload):
                same_id = candidate_id and str(candidate.get("id") or "") == candidate_id
                same_instrument = instrument and str(candidate.get("instrumentKey") or "") == instrument
                if same_id or same_instrument:
                    price = float(candidate.get("lastPremium") or candidate.get("premium") or 0)
                    if price > 0:
                        prices.append(price)
                    break
        return prices

    def profit_lock_status(self, capital: float | None = None, session_adj: dict[str, Any] | None = None) -> dict[str, Any]:
        session_adj = session_adj or {}
        capital = float(capital or self.settings.trading_capital_default or 0)
        rotation_enabled = bool(self.settings.paper_session_rotation_enabled)
        if rotation_enabled:
            session_report = self._session_report()
            net = float(session_report.get("netPnl") or 0)
            session_id = session_report.get("sessionId")
            session_number = session_report.get("sessionNumber")
        else:
            report = self.daily_report()
            net = float(report.get("grossProfit", 0)) - float(report.get("grossLoss", 0))
            session_id = report.get("sessionId")
            session_number = report.get("sessionNumber")
        tiers = [
            {
                "name": "fallback",
                "pct": float(session_adj.get("profitTargetFallbackPct") or self.settings.profit_target_fallback_pct),
            },
            {
                "name": "secondary",
                "pct": float(session_adj.get("profitTargetSecondaryPct") or self.settings.profit_target_secondary_pct),
            },
            {
                "name": "primary",
                "pct": float(session_adj.get("profitTargetPrimaryPct") or self.settings.profit_target_primary_pct),
            },
        ]
        achieved = []
        for tier in tiers:
            target_amount = capital * float(tier["pct"]) / 100 if capital else 0
            if capital and net >= target_amount:
                achieved.append({**tier, "amount": round(target_amount, 2)})
        active = achieved[-1] if achieved else None
        locked_profit = float(active["amount"]) * (self.settings.profit_lock_retain_pct / 100) if active else 0
        giveback = max(0, net - locked_profit) if active else 0
        block_new = bool(active and net <= locked_profit)
        if rotation_enabled and self.settings.paper_trading:
            block_new = False
        if rotation_enabled and active:
            message = (
                f"{active['name']} profit tier reached; session saves and restarts automatically"
                if active["name"] == "primary"
                else f"{active['name']} profit tier reached; session rotation active"
            )
        elif active and active["name"] == "primary":
            message = "Primary profit locked; only trade from giveback buffer"
        elif active:
            message = f"{active['name']} profit tier locked"
        else:
            message = "Profit target not locked yet"
        return {
            "capital": capital,
            "netPnl": round(net, 2),
            "tiers": [{**tier, "amount": round(capital * float(tier["pct"]) / 100, 2) if capital else 0} for tier in tiers],
            "activeTier": active,
            "lockedProfit": round(locked_profit, 2),
            "givebackAvailable": round(giveback, 2),
            "blockNewTrades": block_new,
            "message": message,
            "sessionId": session_id,
            "sessionNumber": session_number,
            "sessionRotationEnabled": rotation_enabled,
        }

    def _session_closed_trades(self) -> list[PaperTrade]:
        session_id = self.paper_sessions.current_id()
        return [trade for trade in self.closed_paper if (trade.paper_session_id or session_id) == session_id]

    def _session_report(self) -> dict[str, Any]:
        return self.paper_sessions.build_report(self._session_closed_trades())

    def daily_report(self) -> dict[str, Any]:
        session_report = self._session_report()
        day_aggregate = self.paper_sessions.day_aggregate()
        trades = self._session_closed_trades()
        losses = [trade for trade in trades if trade.pnl < 0]
        max_drawdown = self._max_drawdown([trade.pnl for trade in trades])
        return {
            "totalSignals": len(self.lifecycle_events),
            "paperTrades": session_report.get("paperTrades", 0),
            "openTrades": len(self.open_paper),
            "wins": session_report.get("wins", 0),
            "losses": session_report.get("losses", 0),
            "winRate": session_report.get("winRate", 0),
            "grossProfit": session_report.get("grossProfit", 0),
            "grossLoss": session_report.get("grossLoss", 0),
            "profitFactor": session_report.get("profitFactor", 0),
            "netPnl": session_report.get("netPnl", 0),
            "maxDrawdown": round(max_drawdown, 2),
            "sessionId": session_report.get("sessionId"),
            "sessionNumber": session_report.get("sessionNumber"),
            "dayAggregate": day_aggregate,
            "completedSessionsToday": len(self.paper_sessions.completed_today()),
            "reasonForLosses": self._loss_reasons(losses),
        }

    def _maybe_rotate_paper_session(self, snapshots: dict[str, Any], session_adj: dict[str, Any], trading_capital: float) -> dict[str, Any] | None:
        if not self.settings.paper_session_rotation_enabled:
            return None
        session_report = self._session_report()
        decision = self.paper_sessions.evaluate_rotation(session_report, session_adj)
        if decision.get("dailyHalt"):
            return {"rotated": False, "dailyHalt": True, "reason": decision.get("reason")}
        if not decision.get("shouldRotate"):
            return None
        return self._rotate_paper_session(str(decision.get("endReason") or "ROTATE"), decision.get("reason"), snapshots, session_adj, trading_capital)

    def _rotate_paper_session(
        self,
        end_reason: str,
        detail: str | None,
        snapshots: dict[str, Any],
        session_adj: dict[str, Any],
        trading_capital: float,
    ) -> dict[str, Any]:
        flattened = self._flatten_open_paper_for_session_end(snapshots, f"session rotation: {detail or end_reason}")
        session_report = self._session_report()
        closed = self.paper_sessions.close_session(
            session_report,
            end_reason,
            {"detail": detail, "flattenedOpenTrades": len(flattened)},
        )
        AutoTraderEngine._shared_recent_signal_times.clear()
        self.paper_sessions.start_session(f"after_{end_reason.lower()}")
        self._persist_paper_trades_file()
        return {
            "rotated": True,
            "endedSession": closed,
            "newSession": self.paper_sessions.current(),
            "reason": detail or end_reason,
        }

    def _flatten_open_paper_for_session_end(self, snapshots: dict[str, Any], reason: str) -> list[dict[str, Any]]:
        price_by_instrument: dict[str, float] = {}
        for snapshot in snapshots.values():
            for candidate in (snapshot.get("suggestedTrades") or []) + (snapshot.get("explosiveRunnerWatchlist") or []) + (snapshot.get("paperPriceWatch") or []):
                instrument = str(candidate.get("instrumentKey") or "")
                premium = float(candidate.get("lastPremium") or candidate.get("premium") or 0)
                if instrument and premium > 0:
                    price_by_instrument[instrument] = premium
        flattened: list[dict[str, Any]] = []
        for trade_id, trade in list(self.open_paper.items()):
            current = price_by_instrument.get(str(trade.instrument_key or ""), trade.entry_price)
            trade.status = "EXITED"
            trade.exit_price = current
            trade.exit_reason = reason
            trade.exited_at = datetime.now(timezone.utc).isoformat()
            charges = self._charges_estimate(trade.entry_price, current, trade.quantity)
            trade.charges_estimate = charges
            trade.pnl = ((current - trade.entry_price - trade.spread_cost - trade.slippage_estimate) * trade.quantity) - charges
            trade.lifecycle.append(LifecycleEvent("EXITED", trade.exited_at, reason, {"exit": current, "pnl": trade.pnl, "sessionEnd": True}))
            self.closed_paper.append(trade)
            del self.open_paper[trade_id]
            flattened.append(trade.to_dict())
        if flattened:
            self._persist_paper_trades_file()
        return flattened

    def learning_status(self) -> dict[str, Any]:
        return {
            "enabled": self.settings.ai_learning_enabled,
            "mode": "online_second_by_second_calibration",
            "samples": AutoTraderEngine._shared_learning_samples,
            "score": round(AutoTraderEngine._shared_learning_score, 2),
            "lastUpdatedAt": AutoTraderEngine._shared_last_learning_update,
            "note": "Updates every snapshot tick from real-data-derived signals and paper outcomes; not a persisted offline ML retrain yet.",
        }

    def _signal_event(self, candidate: dict[str, Any], payload: dict[str, Any]) -> LifecycleEvent:
        return LifecycleEvent(
            state="SIGNAL_GENERATED",
            timestamp=datetime.now(timezone.utc).isoformat(),
            reason="Signal generated from Upstox-derived TQS and option-chain setup",
            payload={
                "id": candidate.get("id"),
                "symbol": candidate.get("symbol"),
                "tqs": candidate.get("tqs"),
                "mode": candidate.get("mode"),
                "marketPhase": payload.get("marketPhase"),
            },
        )

    def _paper_session_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.settings.paper_session_adjustments_enabled:
            return {
                "sessionBucket": "DISABLED",
                "sessionNote": "Session auto-adjustments disabled.",
                "blockNewPaperTrades": False,
                "blockReason": None,
                "middayRunnerBypassScore": 90.0,
                "minEntryTqs": max(int(self.settings.nifty_opt_min_tqs), int(self.settings.sensex_opt_min_tqs)),
                "minRunnerScore": float(self.settings.explosive_runner_min_score),
                "allocationPctMultiplier": 1.0,
                "effectiveAllocationPct": float(self.settings.paper_trade_allocation_pct),
                "duplicateCooldownSeconds": int(self.settings.paper_duplicate_signal_cooldown_seconds),
                "targetPointsMultiplier": 1.0,
                "stopPointsMultiplier": 1.0,
                "maxHoldSeconds": int(self.settings.max_paper_trade_seconds),
                "adjustments": [],
            }
        phase_raw = str(payload.get("marketPhase") or MarketPhase.LIVE_MARKET.value)
        try:
            phase = MarketPhase(phase_raw)
        except ValueError:
            phase = MarketPhase.LIVE_MARKET
        regime = str(payload.get("regime") or payload.get("strategy", {}).get("router") or "NORMAL")
        return paper_session_adjustments(
            self.settings.aggression_profile,
            phase,
            regime,
            base_min_tqs=max(int(self.settings.nifty_opt_min_tqs), int(self.settings.sensex_opt_min_tqs)),
            base_runner_score=float(self.settings.explosive_runner_min_score),
            base_allocation_pct=float(self.settings.paper_trade_allocation_pct),
            base_duplicate_cooldown=int(self.settings.paper_duplicate_signal_cooldown_seconds),
            base_target_points=float(self.settings.paper_target_points),
            base_stop_points=float(self.settings.paper_stop_points),
            base_max_hold_seconds=int(self.settings.max_paper_trade_seconds),
            open_drive_profit_fallback_pct=float(self.settings.open_drive_profit_target_fallback_pct),
            open_drive_profit_secondary_pct=float(self.settings.open_drive_profit_target_secondary_pct),
            open_drive_profit_primary_pct=float(self.settings.open_drive_profit_target_primary_pct),
            open_drive_profit_stop_pct=float(self.settings.open_drive_profit_stop_pct),
            open_drive_allocation_boost=float(self.settings.open_drive_allocation_multiplier),
        )

    def _is_momentum_aligned_runner(self, candidate: dict[str, Any], runner: dict[str, Any]) -> bool:
        side = str(candidate.get("side") or runner.get("side") or "").upper()
        bias = str(runner.get("directionalBias") or candidate.get("directionalBias") or "").upper()
        if not runner.get("momentumSurge") or not runner.get("momentumAligned"):
            return False
        if side == "CALL" and bias == "BULLISH":
            return True
        if side == "PUT" and bias == "BEARISH":
            return True
        return False

    def _learning_calibration(self) -> dict[str, float]:
        calibration = (self.learner.status_from_state()).get("calibration") or {}
        return {
            "runnerScoreBias": float(calibration.get("runnerScoreBias") or 0),
            "momentumReward": float(calibration.get("momentumReward") or 0),
            "chopPenalty": float(calibration.get("chopPenalty") or 0),
        }

    def _runner_min_score(self, runner: dict[str, Any]) -> float:
        calibration = self._learning_calibration()
        bias = calibration["runnerScoreBias"] + (calibration["momentumReward"] if runner.get("momentumSurge") else 0)
        if runner.get("momentumSurge") and runner.get("momentumAligned"):
            base = float(self.settings.explosive_runner_momentum_min_score)
        else:
            base = float(self.settings.explosive_runner_min_score)
        return max(65.0, base - bias)

    def _is_tradeable_explosive_runner(self, candidate: dict[str, Any], market_phase: str | None = None) -> bool:
        if not self.settings.explosive_runner_enabled:
            return False
        phase = str(market_phase or MarketPhase.LIVE_MARKET.value)
        if phase != MarketPhase.LIVE_MARKET.value:
            return False
        if candidate.get("strategyType") != "EXPLOSIVE_RUNNER":
            return False
        runner = candidate.get("runnerSignal") or {}
        momentum_runner = self._is_momentum_aligned_runner(candidate, runner)
        if not momentum_runner and not self.settings.paper_always_trade_explosive_runners:
            return False
        if runner.get("candidate") is False and not momentum_runner:
            return False
        score = float(runner.get("score") or candidate.get("tqs") or 0)
        if score < self._runner_min_score(runner):
            return False
        premium = float(candidate.get("lastPremium") or runner.get("premium") or runner.get("lastPremium") or 0)
        if premium <= 0:
            return False
        premium_min = float(self.settings.explosive_runner_premium_min)
        premium_max = float(self.settings.explosive_runner_premium_max)
        return premium_min <= premium <= premium_max

    def _session_entry_allowed(self, candidate: dict[str, Any], session_adj: dict[str, Any], market_phase: str | None = None) -> bool:
        if self._is_tradeable_explosive_runner(candidate, market_phase):
            return True
        if not session_adj.get("blockNewPaperTrades"):
            return True
        return False

    def _pre_trade_quality(self, candidate: dict[str, Any], session_adj: dict[str, Any] | None = None, market_phase: str | None = None) -> dict[str, Any]:
        session_adj = session_adj or self._paper_session_settings({})
        premium = float(candidate.get("lastPremium") or 0)
        quantity = int(candidate.get("quantityEstimate") or candidate.get("lotSize") or 1)
        spread_cost = max(0.0, premium * 0.004)
        slippage = max(0.05, premium * 0.002)
        charges = self._charges_estimate(premium, premium, quantity)
        charges_per_unit = charges / quantity if quantity > 0 else 0.0
        required_move = spread_cost + slippage + charges_per_unit + self.settings.min_required_move_points
        reasons = []
        runner = candidate.get("runnerSignal") or {}
        runner_score = float(runner.get("score") or 0)
        chart_bias = str(candidate.get("chartBias") or "")
        side = str(candidate.get("side") or "")
        tradeable_runner = self._is_tradeable_explosive_runner(candidate, market_phase)
        momentum_runner = self._is_momentum_aligned_runner(candidate, runner)
        if premium <= 0:
            reasons.append("missing premium")
        if candidate.get("chopBlocked") and not tradeable_runner:
            reasons.append("chop filter blocked")
        if tradeable_runner:
            volume_state = runner.get("volumeState") or {}
            if candidate.get("effectiveVolume", 0) <= 0 and not volume_state.get("volumeAvailable"):
                reasons.append("missing effective volume")
        else:
            min_entry_tqs = int(session_adj.get("minEntryTqs") or max(int(self.settings.nifty_opt_min_tqs), int(self.settings.sensex_opt_min_tqs)))
            if candidate.get("tqs", 0) < min_entry_tqs:
                reasons.append(f"TQS below session threshold ({min_entry_tqs})")
            if candidate.get("effectiveVolume", 0) <= 0:
                reasons.append("missing effective volume")
            if not momentum_runner:
                if chart_bias in {"CALL", "PUT"} and side in {"CALL", "PUT"} and side != chart_bias:
                    reasons.append(f"chart trend conflict: {chart_bias} bias vs {side} trade")
                if chart_bias == "WAIT":
                    reasons.append("chart analysis says wait")
            min_runner_score = float(session_adj.get("minRunnerScore") or self.settings.explosive_runner_min_score)
            if momentum_runner:
                min_runner_score = min(min_runner_score, float(self.settings.explosive_runner_momentum_min_score))
            if candidate.get("strategyType") == "EXPLOSIVE_RUNNER" and runner_score < min_runner_score:
                reasons.append(f"runner score below session threshold ({min_runner_score:g})")
            if required_move > self.settings.min_required_move_points * 1.4:
                reasons.append("spread/slippage cost too high for 5-point scalp")
        return {
            "blocked": bool(reasons),
            "paperEligible": not bool(reasons) or tradeable_runner,
            "reason": ", ".join(reasons) if reasons else "quality accepted",
            "spreadCost": round(spread_cost, 2),
            "slippageEstimate": round(slippage, 2),
            "chargesEstimate": round(charges, 2),
            "chargesPerUnit": round(charges_per_unit, 4),
            "minimumRequiredMove": round(required_move, 2),
        }

    def _open_paper_trade(
        self,
        candidate: dict[str, Any],
        quality: dict[str, Any],
        available_capital: float | None = None,
        trading_capital: float | None = None,
        session_adj: dict[str, Any] | None = None,
        market_phase: str | None = None,
    ) -> PaperTrade | None:
        session_adj = session_adj or {}
        tradeable_runner = self._is_tradeable_explosive_runner(candidate, market_phase)
        trade_id = str(candidate.get("id") or uuid4())
        if trade_id in self.open_paper:
            return None
        if self._recent_signal_active(trade_id):
            return None
        premium = float(candidate.get("lastPremium") or 0)
        lot_size = max(1, int(candidate.get("lotSize") or 1))
        desired_quantity = int(candidate.get("quantityEstimate") or lot_size)
        if available_capital is not None and premium > 0:
            capital = max(0.0, float(trading_capital or 0))
            allocation_pct = float(self.settings.paper_trade_allocation_pct) * float(session_adj.get("allocationPctMultiplier") or 1.0)
            if tradeable_runner:
                allocation_pct = max(allocation_pct, float(self.settings.paper_trade_allocation_pct))
            target_allocation = capital * max(0.0, allocation_pct) / 100 if capital > 0 else max(0.0, available_capital)
            min_allocation_pct = float(self.settings.paper_min_trade_allocation_pct)
            if tradeable_runner:
                min_allocation_pct = min(min_allocation_pct, float(self.settings.paper_min_trade_allocation_pct))
            min_allocation = capital * max(0.0, min_allocation_pct) / 100 if capital > 0 else 0.0
            usable_capital = min(max(0.0, available_capital), target_allocation)
            affordable_lots = int(usable_capital // (premium * lot_size))
            quantity = min(desired_quantity, affordable_lots * lot_size)
            if quantity * premium < min_allocation:
                return None
        else:
            quantity = desired_quantity
        if quantity < lot_size:
            return None
        risk_plan = self._paper_risk_plan(candidate, quality, premium, session_adj)
        charges = self._charges_estimate(premium, premium, max(1, quantity))
        trade = PaperTrade(
            id=trade_id,
            symbol=str(candidate.get("symbol")),
            side=str(candidate.get("side")),
            strike=int(candidate.get("strike") or 0),
            expiry=str(candidate.get("expiry")),
            instrument_key=candidate.get("instrumentKey"),
            entry_price=premium,
            quantity=max(1, quantity),
            entry_tqs=int(candidate.get("tqs") or 0),
            spread_cost=float(quality["spreadCost"]),
            slippage_estimate=float(quality["slippageEstimate"]),
            charges_estimate=float(quality.get("chargesEstimate") or charges),
            opened_at=datetime.now(timezone.utc).isoformat(),
            mode=str(candidate.get("mode")),
            strategy_type=str(candidate.get("strategyType") or "SCALP"),
            paper_session_id=self.paper_sessions.current_id(),
            target_points=risk_plan["targetPoints"],
            stop_points=risk_plan["stopPoints"],
            breakeven_shift_points=risk_plan["breakevenShiftPoints"],
            trail_points=risk_plan["trailPoints"],
            best_price=premium,
        )
        trade.lifecycle.extend([
            LifecycleEvent("RISK_CHECKED", trade.opened_at, quality["reason"], {**quality, "riskPlan": risk_plan}),
            LifecycleEvent("PAPER_OPENED", trade.opened_at, "Shadow trade opened; no broker order placed", {"entry": trade.entry_price}),
        ])
        self.open_paper[trade.id] = trade
        self._persist_paper_trades_file()
        return trade

    def _update_open_paper(self, snapshots: dict[str, Any], psychology: dict[str, Any] | None = None, session_adj: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        session_adj = session_adj or {}
        session_max_hold = int(session_adj.get("maxHoldSeconds") or self.settings.max_paper_trade_seconds)
        exits = []
        price_by_id: dict[str, dict[str, Any]] = {}
        price_by_instrument: dict[str, dict[str, Any]] = {}
        for snapshot in snapshots.values():
            for candidate in snapshot.get("suggestedTrades") or []:
                self._index_price_payload(candidate, price_by_id, price_by_instrument)
            for candidate in snapshot.get("explosiveRunnerWatchlist") or []:
                self._index_price_payload(candidate, price_by_id, price_by_instrument)
            for candidate in snapshot.get("paperPriceWatch") or []:
                self._index_price_payload(candidate, price_by_id, price_by_instrument)
        for trade_id, trade in list(self.open_paper.items()):
            candidate = price_by_id.get(trade_id) or price_by_instrument.get(str(trade.instrument_key or ""))
            if not candidate:
                continue
            current = float((candidate or {}).get("lastPremium") or (candidate or {}).get("premium") or 0)
            if current <= 0:
                continue
            trade.best_price = max(trade.best_price or trade.entry_price, current)
            age = self._age_seconds(trade.opened_at)
            reason = None
            profile = (candidate or {}).get("optimizedProfile") or {}
            target_points = float(trade.target_points or profile.get("targetPoints") or self.settings.paper_target_points)
            stop_points = float(trade.stop_points or profile.get("stopPoints") or self.settings.paper_stop_points)
            partial_exit_at = max(1.0, target_points * float(profile.get("partialExitPct") or 0.6))
            breakeven_shift = float(trade.breakeven_shift_points or self.settings.paper_breakeven_shift_points)
            stop_points, max_hold_seconds, psych_exit_reason = self._psychology_exit_adjustments(stop_points, psychology, session_max_hold)
            style = str(profile.get("executionStyle") or "GENERIC")
            if style == "RUNNER_BREAKOUT":
                target_points = max(target_points, self.settings.paper_target_points * 1.2)
            elif style == "HIGH_WIN_SCALP":
                target_points = max(target_points, self.settings.paper_target_points)
            if not trade.breakeven_armed and trade.best_price >= trade.entry_price + breakeven_shift:
                trade.breakeven_armed = True
                trade.lifecycle.append(LifecycleEvent("MODIFIED", datetime.now(timezone.utc).isoformat(), "breakeven stop armed", {"breakevenAt": trade.entry_price, "bestPrice": trade.best_price}))
            if not trade.partial_exit_taken and trade.best_price >= trade.entry_price + partial_exit_at:
                trade.partial_exit_taken = True
                trade.lifecycle.append(LifecycleEvent("PARTIAL_FILL", datetime.now(timezone.utc).isoformat(), "partial exit threshold reached in paper model", {"partialExitAt": round(partial_exit_at, 2), "bestPrice": trade.best_price}))
            if current >= trade.entry_price + target_points:
                reason = "trailing profit lock / target extension"
            elif trade.breakeven_armed and current <= trade.entry_price:
                reason = "breakeven protection after +8 move"
            elif current <= trade.entry_price - stop_points:
                reason = psych_exit_reason or "momentum decay or delta reversal stop"
            elif age >= max_hold_seconds:
                reason = "psychology shortened time stop" if max_hold_seconds < self.settings.max_paper_trade_seconds else "time stop"
            elif self._should_chop_exit(trade, candidate, age):
                reason = "liquidity rejection / chop filter exit"
            if reason:
                trade.status = "EXITED"
                trade.exit_price = current
                trade.exit_reason = reason
                trade.exited_at = datetime.now(timezone.utc).isoformat()
                charges = self._charges_estimate(trade.entry_price, current, trade.quantity)
                trade.charges_estimate = charges
                trade.pnl = ((current - trade.entry_price - trade.spread_cost - trade.slippage_estimate) * trade.quantity) - charges
                trade.lifecycle.append(LifecycleEvent("EXITED", trade.exited_at, reason, {"exit": current, "pnl": trade.pnl, "charges": charges}))
                self.closed_paper.append(trade)
                AutoTraderEngine._shared_recent_signal_times[trade_id] = monotonic()
                self.lifecycle_events.extend(trade.lifecycle[-1:])
                del self.open_paper[trade_id]
                exits.append(trade.to_dict())
        if exits:
            self._persist_paper_trades_file()
        return exits

    def _learn_every_tick(self, payload: dict[str, Any], exits: list[dict[str, Any]]) -> None:
        if not self.settings.ai_learning_enabled:
            return
        AutoTraderEngine._shared_learning_samples += 1
        tqs = float(payload.get("tradeQualityScore") or 50)
        outcome_boost = sum(1 if item.get("pnl", 0) > 0 else -1 for item in exits)
        AutoTraderEngine._shared_learning_score = max(0, min(100, (AutoTraderEngine._shared_learning_score * 0.98) + (tqs * 0.02) + outcome_boost))
        AutoTraderEngine._shared_last_learning_update = datetime.now(timezone.utc).isoformat()

    def _slippage_summary(self, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        qualities = [self._pre_trade_quality(candidate) for candidate in candidates]
        estimates = [quality["slippageEstimate"] for quality in qualities]
        return {
            "averageExpectedSlippage": round(sum(estimates) / len(estimates), 2) if estimates else 0,
            "averageEstimatedCharges": round(sum(float(quality.get("chargesEstimate") or 0) for quality in qualities) / len(qualities), 2) if qualities else 0,
            "minimumRequiredMovePoints": self.settings.min_required_move_points,
            "model": "premium_spread_slippage_plus_india_options_charges",
        }

    def _position_sizing_summary(self, candidates: list[dict[str, Any]], capital: float) -> dict[str, Any]:
        return {
            "capital": capital,
            "paperTradeAllocationPct": self.settings.paper_trade_allocation_pct,
            "paperMinTradeAllocationPct": self.settings.paper_min_trade_allocation_pct,
            "candidates": [
                {
                    "id": candidate.get("id"),
                    "quantityEstimate": candidate.get("quantityEstimate", 0),
                    "lotSize": candidate.get("lotSize"),
                    "estimatedLots": candidate.get("estimatedLots"),
                    "allocationPct": candidate.get("allocationPct", 0),
                    "tqs": candidate.get("tqs"),
                }
                for candidate in candidates[:10]
            ],
        }

    def _compact_snapshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        compact_snapshots: dict[str, Any] = {}
        for symbol, snapshot in (payload.get("snapshots") or {}).items():
            if not isinstance(snapshot, dict):
                continue
            compact_snapshots[symbol] = {
                "symbol": snapshot.get("symbol"),
                "chartAnalysis": snapshot.get("chartAnalysis"),
                "explosiveRunnerWatchlist": (snapshot.get("explosiveRunnerWatchlist") or [])[:12],
                "paperPriceWatch": (snapshot.get("paperPriceWatch") or [])[:12],
            }
        return {
            "type": payload.get("type"),
            "timestamp": payload.get("timestamp"),
            "displaySymbol": payload.get("displaySymbol") or payload.get("symbol"),
            "tradeQualityScore": payload.get("tradeQualityScore"),
            "marketPhase": payload.get("marketPhase"),
            "executionCandidates": payload.get("executionCandidates", [])[:12],
            "snapshots": compact_snapshots,
        }

    def _index_price_payload(self, payload: dict[str, Any], by_id: dict[str, dict[str, Any]], by_instrument: dict[str, dict[str, Any]]) -> None:
        if not isinstance(payload, dict):
            return
        premium = float(payload.get("lastPremium") or payload.get("premium") or 0)
        if premium <= 0:
            return
        item_id = payload.get("id")
        instrument = payload.get("instrumentKey")
        if item_id:
            by_id[str(item_id)] = payload
        if instrument:
            instrument_key = str(instrument)
            existing = by_instrument.get(instrument_key)
            if existing and existing.get("chopBlocked") and not payload.get("chopBlocked"):
                payload = {**payload, "chopBlocked": False}
            by_instrument[instrument_key] = payload

    def _should_chop_exit(self, trade: PaperTrade, candidate: dict[str, Any] | None, age_seconds: float) -> bool:
        if not candidate or not candidate.get("chopBlocked"):
            return False
        if trade.strategy_type == "EXPLOSIVE_RUNNER":
            return False
        min_hold = max(0, int(self.settings.paper_min_hold_before_chop_exit_seconds))
        return age_seconds >= min_hold

    def _available_capital(self, trading_capital: float) -> float:
        used = sum(max(0.0, trade.entry_price) * max(0, trade.quantity) for trade in self.open_paper.values())
        return max(0.0, float(trading_capital or 0) - used)

    def _paper_risk_halt(self, trading_capital: float | None = None, session_adj: dict[str, Any] | None = None) -> dict[str, Any]:
        session_adj = session_adj or {}
        capital = float(trading_capital or self.settings.trading_capital_default or 0)
        session_report = self._session_report()
        day_aggregate = self.paper_sessions.day_aggregate()
        session_net = float(session_report.get("netPnl") or 0)
        day_net = float(day_aggregate.get("netPnl") or 0)
        consecutive_losses = int(session_report.get("consecutiveLosses") or 0)
        loss_pct = abs(day_net) / capital * 100 if capital > 0 and day_net < 0 else 0.0
        session_loss_pct = abs(session_net) / capital * 100 if capital > 0 and session_net < 0 else 0.0
        reasons = []
        day_loss_amount = abs(day_net) if day_net < 0 else 0.0
        max_loss_amount = float(self.settings.paper_max_daily_loss_amount or 0)
        rotation_enabled = bool(self.settings.paper_session_rotation_enabled)
        if max_loss_amount > 0 and day_loss_amount >= max_loss_amount:
            reasons.append(f"paper daily loss ₹{day_loss_amount:,.0f} >= ₹{max_loss_amount:,.0f}")
        elif loss_pct >= float(self.settings.paper_max_daily_loss_pct):
            reasons.append(f"paper daily loss {loss_pct:.2f}% >= {self.settings.paper_max_daily_loss_pct:.2f}%")
        if not rotation_enabled and consecutive_losses >= int(self.settings.paper_max_consecutive_losses):
            reasons.append(f"{consecutive_losses} consecutive paper losses")
        profit_target_pct = float(session_adj.get("sessionProfitStopPct") or self.settings.paper_daily_profit_stop_pct)
        session_profit_pct = float(session_report.get("profitPct") or 0)
        if not rotation_enabled and session_profit_pct >= profit_target_pct:
            reasons.append(f"session profit {session_profit_pct:.2f}% >= {profit_target_pct:.2f}%")
        return {
            "blocked": bool(reasons),
            "reason": "; ".join(reasons) if reasons else None,
            "netPnl": round(session_net, 2),
            "dayNetPnl": round(day_net, 2),
            "lossPct": round(loss_pct, 2),
            "sessionLossPct": round(session_loss_pct, 2),
            "lossAmount": round(day_loss_amount, 2),
            "consecutiveLosses": consecutive_losses,
            "sessionProfitPct": round(session_profit_pct, 2),
            "profitTargetPct": profit_target_pct,
            "maxDailyLossPct": self.settings.paper_max_daily_loss_pct,
            "maxDailyLossAmount": max_loss_amount,
            "maxConsecutiveLosses": self.settings.paper_max_consecutive_losses,
            "sessionRotationEnabled": rotation_enabled,
        }

    def _psychology_exit_adjustments(self, stop_points: float, psychology: dict[str, Any] | None = None, session_max_hold: int | None = None) -> tuple[float, int, str | None]:
        psychology = psychology or {}
        state = str(psychology.get("state") or "CALM_AND_SELECTIVE")
        permission = str(psychology.get("tradePermission") or "A_PLUS_ONLY")
        max_hold = int(session_max_hold or self.settings.max_paper_trade_seconds)
        adjusted_stop = float(stop_points)
        reason = None
        if permission == "BLOCK_NEW_TRADES" or state == "HALT_AND_REVIEW":
            adjusted_stop = min(adjusted_stop, max(1.0, stop_points * 0.45))
            max_hold = min(max_hold, 60)
            reason = "psychology halt defensive stop"
        elif permission == "WAIT" or state == "DEFENSIVE":
            adjusted_stop = min(adjusted_stop, max(1.25, stop_points * 0.6))
            max_hold = min(max_hold, 90)
            reason = "psychology defensive stop"
        elif state == "CAUTIOUS":
            adjusted_stop = min(adjusted_stop, max(1.5, stop_points * 0.75))
            max_hold = min(max_hold, 150)
            reason = "psychology cautious stop"
        return round(adjusted_stop, 2), max_hold, reason

    def _psychology_report(
        self,
        candidates: list[dict[str, Any]],
        skipped: list[dict[str, Any]],
        risk_halt: dict[str, Any],
        session_adj: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session_adj = session_adj or {}
        daily_profit_stop_pct = float(session_adj.get("sessionProfitStopPct") or self.settings.paper_daily_profit_stop_pct)
        report = self._session_report()
        closed = self._session_closed_trades()
        open_count = len(self.open_paper)
        paper_trades = int(report.get("paperTrades") or 0)
        losses = int(report.get("losses") or 0)
        wins = int(report.get("wins") or 0)
        profit_factor = float(report.get("profitFactor") or 0)
        win_rate = float(report.get("winRate") or 0)
        net_pnl = float(report.get("netPnl") or 0)
        capital = float(self.settings.trading_capital_default or 0)
        profit_pct = float(report.get("profitPct") or 0)
        rotation_enabled = bool(self.settings.paper_session_rotation_enabled)
        total_signals = int(report.get("totalSignals") or 0)
        skipped_reasons = [str(item.get("reason") or "") for item in skipped]
        chart_conflicts = sum(1 for reason in skipped_reasons if "chart trend conflict" in reason)
        chop_skips = sum(1 for reason in skipped_reasons if "chop" in reason.lower())
        duplicate_skips = sum(1 for reason in skipped_reasons if "duplicate" in reason.lower() or "cached" in reason.lower())
        consecutive_losses = int(risk_halt.get("consecutiveLosses") or 0)
        loss_pct = float(risk_halt.get("lossPct") or 0)
        recent_losses = [trade for trade in closed[-5:] if trade.pnl < 0]
        recent_wins = [trade for trade in closed[-5:] if trade.pnl > 0]

        emotional_risks: list[str] = []
        behavioral_findings: list[str] = []
        coach_actions: list[str] = []

        if risk_halt.get("blocked"):
            emotional_risks.append("revenge_trading_risk")
            behavioral_findings.append(f"Paper risk halt active: {risk_halt.get('reason')}")
            coach_actions.append("Stop new entries. Review last 3 losses before allowing the system to resume.")
        if profit_pct >= daily_profit_stop_pct:
            behavioral_findings.append(f"Session paper profit target achieved: {profit_pct:.2f}% >= {daily_profit_stop_pct:.2f}%")
            coach_actions.append(
                "Session profit target hit; rotation will archive this session and start a fresh one."
                if rotation_enabled
                else "Stop trading for the day. Protect the achieved profit and avoid greed trades."
            )
        if consecutive_losses >= 2:
            emotional_risks.append("loss_chasing")
            behavioral_findings.append(f"{consecutive_losses} consecutive losses detected.")
            coach_actions.append("Require chart alignment plus runner score >= 90 for the next trade.")
        if total_signals >= 500 and paper_trades < max(1, total_signals * 0.02):
            behavioral_findings.append("Good patience: many weak signals are being skipped.")
        elif paper_trades > 12 and profit_factor < 1:
            emotional_risks.append("overtrading")
            behavioral_findings.append("Too many paper trades without profit factor confirmation.")
            coach_actions.append("Reduce frequency: only A+ chart-aligned runner setups should be accepted.")
        if chart_conflicts:
            emotional_risks.append("contrarian_impulse")
            behavioral_findings.append(f"{chart_conflicts} current candidates conflict with chart bias.")
            coach_actions.append("Do not fight chart bias unless option tape is exceptional.")
        if chop_skips:
            behavioral_findings.append(f"{chop_skips} current candidates rejected due to chop/weak quality.")
            coach_actions.append("Wait for breakout + delta velocity confirmation; avoid boredom trades.")
        if open_count > 2:
            emotional_risks.append("exposure_anxiety")
            behavioral_findings.append(f"{open_count} paper trades open; exposure may dilute decision quality.")
            coach_actions.append("Avoid adding another trade until one position closes.")
        if losses > wins and profit_factor < 1:
            emotional_risks.append("confidence_drift")
            coach_actions.append("Judge the system by process quality, not one trade. Keep risk halt rules active.")
        if recent_wins and recent_losses:
            behavioral_findings.append("Mixed recent outcomes; avoid increasing aggression after a win.")

        discipline_score = 100
        discipline_score -= min(35, consecutive_losses * 12)
        discipline_score -= min(25, int(loss_pct * 8))
        discipline_score -= 15 if "overtrading" in emotional_risks else 0
        discipline_score -= 10 if open_count > 2 else 0
        discipline_score += 8 if duplicate_skips else 0
        discipline_score += 8 if paper_trades == 0 and total_signals > 20 else 0
        discipline_score = max(0, min(100, discipline_score))

        if profit_pct >= daily_profit_stop_pct and not rotation_enabled:
            state = "TARGET_ACHIEVED"
            permission = "BLOCK_NEW_TRADES"
        elif profit_pct >= daily_profit_stop_pct and rotation_enabled:
            state = "TARGET_ACHIEVED"
            permission = "A_PLUS_ONLY"
        elif risk_halt.get("blocked"):
            state = "HALT_AND_REVIEW"
            permission = "BLOCK_NEW_TRADES"
        elif discipline_score >= 80 and not emotional_risks:
            state = "CALM_AND_SELECTIVE"
            permission = "A_PLUS_ONLY"
        elif discipline_score >= 60:
            state = "CAUTIOUS"
            permission = "A_PLUS_ONLY"
        else:
            state = "DEFENSIVE"
            permission = "WAIT"

        if not coach_actions:
            coach_actions.append("Stay selective. Let the bot wait for chart-aligned option-tape confirmation.")
        adjusted_stop, adjusted_hold, exit_reason = self._psychology_exit_adjustments(float(self.settings.paper_stop_points), {
            "state": state,
            "tradePermission": permission,
        })

        return {
            "state": state,
            "disciplineScore": discipline_score,
            "tradePermission": permission,
            "emotionalRisks": sorted(set(emotional_risks)),
            "behavioralFindings": behavioral_findings[:8],
            "coachActions": coach_actions[:8],
            "metrics": {
                "winRate": win_rate,
                "profitFactor": profit_factor,
                "paperTrades": paper_trades,
                "openTrades": open_count,
                "consecutiveLosses": consecutive_losses,
                "lossPct": round(loss_pct, 2),
                "profitPct": round(profit_pct, 2),
                "dailyProfitStopPct": daily_profit_stop_pct,
                "sessionBucket": session_adj.get("sessionBucket"),
                "currentSkipped": len(skipped),
                "chartConflicts": chart_conflicts,
                "chopSkips": chop_skips,
                "duplicateSkips": duplicate_skips,
            },
            "exitAdjustments": {
                "baseStopPoints": self.settings.paper_stop_points,
                "adjustedStopPoints": adjusted_stop,
                "baseMaxHoldSeconds": self.settings.max_paper_trade_seconds,
                "adjustedMaxHoldSeconds": adjusted_hold,
                "reason": exit_reason,
            },
            "mantra": "Protect capital first. Trade only when chart, tape, and risk agree.",
        }

    def _all_snapshots_cached(self, snapshots: dict[str, Any]) -> bool:
        if not snapshots:
            return False
        statuses = [(snapshot.get("cacheStatus") or {}).get("source") for snapshot in snapshots.values() if isinstance(snapshot, dict)]
        return bool(statuses) and all(status == "engine_snapshot_cache" for status in statuses)

    def _recent_signal_active(self, signal_id: str, cooldown_seconds: int | None = None) -> bool:
        if not signal_id:
            return False
        last_seen = AutoTraderEngine._shared_recent_signal_times.get(signal_id)
        if last_seen is None:
            return False
        age = monotonic() - last_seen
        cooldown = max(0, int(cooldown_seconds if cooldown_seconds is not None else self.settings.paper_duplicate_signal_cooldown_seconds))
        if age > cooldown:
            AutoTraderEngine._shared_recent_signal_times.pop(signal_id, None)
            return False
        return True

    def _age_seconds(self, iso_timestamp: str) -> float:
        try:
            return (datetime.now(timezone.utc) - datetime.fromisoformat(iso_timestamp)).total_seconds()
        except ValueError:
            return 0.0

    def _charges_estimate(self, entry_price: float, exit_price: float, quantity: int) -> float:
        quantity = max(0, int(quantity))
        if quantity <= 0:
            return 0.0
        buy_turnover = max(0.0, entry_price) * quantity
        sell_turnover = max(0.0, exit_price) * quantity
        total_turnover = buy_turnover + sell_turnover
        brokerage = min(float(self.settings.option_brokerage_per_order), buy_turnover) + min(float(self.settings.option_brokerage_per_order), sell_turnover)
        stt = sell_turnover * float(self.settings.option_stt_sell_pct) / 100
        exchange_txn = total_turnover * float(self.settings.option_exchange_txn_pct) / 100
        sebi = total_turnover * float(self.settings.option_sebi_pct) / 100
        stamp = buy_turnover * float(self.settings.option_stamp_buy_pct) / 100
        gst = (brokerage + exchange_txn + sebi) * float(self.settings.option_gst_pct) / 100
        return round(brokerage + stt + exchange_txn + sebi + stamp + gst, 2)

    def _paper_risk_plan(self, candidate: dict[str, Any], quality: dict[str, Any], premium: float, session_adj: dict[str, Any] | None = None) -> dict[str, float]:
        session_adj = session_adj or {}
        profile = candidate.get("optimizedProfile") or {}
        runner = candidate.get("runnerSignal") or {}
        metrics = runner.get("metrics") or {}
        target_multiplier = float(session_adj.get("targetPointsMultiplier") or 1.0)
        stop_multiplier = float(session_adj.get("stopPointsMultiplier") or 1.0)
        target = float(profile.get("targetPoints") or self.settings.paper_target_points) * target_multiplier
        base_stop = float(profile.get("stopPoints") or self.settings.paper_stop_points) * stop_multiplier
        breakeven = float(self.settings.paper_breakeven_shift_points)
        runner_score = float(runner.get("score") or 0)
        breakout = float(metrics.get("breakoutVelocity") or 0)
        delta_velocity = abs(float(metrics.get("deltaVelocity") or 0))
        costs_per_unit = float(quality.get("spreadCost") or 0) + float(quality.get("slippageEstimate") or 0) + float(quality.get("chargesPerUnit") or 0)
        # Low-premium options can lose too much if we use a fixed 7/10 point stop.
        premium_stop_cap_pct = 0.10 if runner_score >= 85 and breakout >= 65 and delta_velocity >= 45 else 0.08
        premium_capped_stop = max(costs_per_unit + 1.0, premium * premium_stop_cap_pct) if premium > 0 else base_stop
        stop = min(base_stop, premium_capped_stop) if premium > 0 else base_stop
        stop = max(costs_per_unit + 0.75, stop)
        trail = max(1.5, target * (0.45 if candidate.get("strategyType") == "EXPLOSIVE_RUNNER" else 0.35))
        return {
            "targetPoints": round(target, 2),
            "stopPoints": round(stop, 2),
            "breakevenShiftPoints": round(min(breakeven, max(1.5, target * 0.6)), 2),
            "trailPoints": round(trail, 2),
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

    def _loss_reasons(self, losses: list[PaperTrade]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for trade in losses:
            reason = trade.exit_reason or "unknown"
            counts[reason] = counts.get(reason, 0) + 1
        return counts
