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
from app.services.daily_profit_strategy import build_daily_improvement_plan
from app.services.paper_session_manager import PaperSessionManager
from app.services.risk_profiles import paper_session_adjustments
from app.services.session import IST, MarketPhase
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
    capital_pool: str = ""
    exit_mode: str = "AUTO"
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
            capital_pool=str(payload.get("capitalPool") or ""),
            exit_mode=str(payload.get("exitMode") or "AUTO"),
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
            "capitalPool": self.capital_pool,
            "exitMode": self.exit_mode,
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
    _shared_missed_runners: deque[dict[str, Any]] = deque(maxlen=500)  # near-miss runner log

    def __init__(self, settings: Settings, trading_control: TradingControl, learner: ContinuousAILearner | None = None) -> None:
        self.settings = settings
        self.trading_control = trading_control
        self.learner = learner or ContinuousAILearner(settings.redis_url, settings.ai_learning_enabled)
        self.paper_sessions = PaperSessionManager(settings)
        self.replay_buffer = AutoTraderEngine._shared_replay_buffer
        self.open_paper = AutoTraderEngine._shared_open_paper
        self.closed_paper = AutoTraderEngine._shared_closed_paper
        self.lifecycle_events = AutoTraderEngine._shared_lifecycle_events
        self._latest_market_snapshot: dict[str, Any] = {}
        self._latest_news_state: dict[str, Any] = {}
        self._daily_plan_cache: dict[str, Any] | None = None
        self._daily_plan_day: str | None = None
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
        self._latest_market_snapshot = payload.get("marketSnapshot") or {}
        for _snap in snapshots.values():
            _ns = _snap.get("newsState")
            if isinstance(_ns, dict) and _ns.get("available"):
                self._latest_news_state = _ns
                break
        cached_snapshot = self._all_snapshots_cached(snapshots)
        signal_cooldown_seconds = int(session_adj.get("duplicateCooldownSeconds") or self.settings.paper_duplicate_signal_cooldown_seconds)
        market_phase = str(payload.get("marketPhase") or MarketPhase.LIVE_MARKET.value)

        pre_trade_psychology = self._psychology_report([], [], self._paper_risk_halt(trading_capital), session_adj)
        exits = self._update_open_paper(snapshots, pre_trade_psychology, session_adj)
        target_lock = self._maybe_lock_daily_profit_target(snapshots)
        if target_lock.get("lockedTrades"):
            exits.extend(target_lock["lockedTrades"])
        rotation_event = self._maybe_rotate_paper_session(snapshots, session_adj, trading_capital)
        risk_halt = self._paper_risk_halt(trading_capital, session_adj)
        pre_trade_psychology = self._psychology_report([], [], risk_halt, session_adj)

        signal_events = []
        skipped = []
        for candidate in candidates:
            prepared = self._prepare_execution_candidate(candidate)
            if prepared is None:
                skipped.append({"candidate": candidate.get("id"), "reason": "runner below ultra-elite; scalping gates not met"})
                continue
            candidate = prepared
            event = self._signal_event(candidate, payload)
            signal_events.append(event)
            self.lifecycle_events.append(event)
            signal_id = str(candidate.get("id") or "")
            runner_sig = (candidate.get("runnerSignal") or {})
            is_runner_burst = bool(
                runner_sig.get("momentumOverride")
                or self._is_momentum_explosion(candidate, runner_sig)
                or self._is_catchable_runner(candidate, runner_sig)
            )
            if cached_snapshot and not is_runner_burst:
                skipped.append({"candidate": signal_id, "reason": "cached snapshot; paper open skipped to avoid duplicate training sample"})
                continue
            # Momentum override uses shorter cooldown (20s) to catch continuation moves
            effective_cooldown = 20 if runner_sig.get("momentumOverride") else signal_cooldown_seconds
            if self._recent_signal_active(signal_id, effective_cooldown):
                skipped.append({"candidate": signal_id, "reason": "duplicate signal cooldown active"})
                continue
            if session_adj.get("blockNewPaperTrades") and not self._session_entry_allowed(candidate, session_adj, market_phase):
                skip_reason = session_adj.get("blockReason") or "session gate blocked"
                skipped.append({"candidate": candidate.get("id"), "reason": skip_reason})
                runner_s = (candidate.get("runnerSignal") or {})
                if runner_s.get("score", 0) >= 65 or runner_s.get("momentumOverride"):
                    AutoTraderEngine._shared_missed_runners.append({
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "symbol": candidate.get("symbol"), "side": candidate.get("side"),
                        "strike": candidate.get("strike"), "premium": candidate.get("lastPremium"),
                        "runnerScore": runner_s.get("score"), "momentumOverride": runner_s.get("momentumOverride"),
                        "premiumVelocity": runner_s.get("premiumVelocityPct"), "reason": skip_reason,
                        "gate": "session",
                    })
                continue
            quality = self._pre_trade_quality(candidate, session_adj, market_phase)
            if quality["blocked"]:
                reason_text = str(quality.get("reason") or "")
                is_position_limit = any(s in reason_text.lower() for s in ["max open", "max capacity", "already has an open", "hard cap"])
                is_chase = "chase blocked" in reason_text.lower()
                is_hard_block = self._is_hard_quality_block(reason_text)
                explosion_entry = self._runner_entry_bypass(candidate)
                can_bypass = (
                    not is_hard_block
                    and (
                        (explosion_entry and not is_position_limit and not is_chase)
                        or (self._runner_may_bypass_quality(candidate, reason_text, market_phase) and not is_position_limit)
                    )
                )
                if can_bypass:
                    quality = {**quality, "blocked": False, "paperEligible": True, "reason": f"runner quality bypass ({reason_text})"}
                elif self.settings.paper_trading and self.settings.shadow_trade_all_signals:
                    skipped.append({"candidate": candidate.get("id"), "reason": quality["reason"], "quality": quality})
                    quality = {**quality, "shadowOverride": True, "reason": f"SHADOW despite: {quality['reason']}"}
                else:
                    skipped.append({"candidate": candidate.get("id"), "reason": quality["reason"], "quality": quality})
                    # Log near-miss runners for post-session analysis
                    runner_s = (candidate.get("runnerSignal") or {})
                    if runner_s.get("score", 0) >= 70 or runner_s.get("momentumOverride"):
                        AutoTraderEngine._shared_missed_runners.append({
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "symbol": candidate.get("symbol"), "side": candidate.get("side"),
                            "strike": candidate.get("strike"), "premium": candidate.get("lastPremium"),
                            "runnerScore": runner_s.get("score"), "momentumOverride": runner_s.get("momentumOverride"),
                            "premiumVelocity": runner_s.get("premiumVelocityPct"), "reason": quality["reason"],
                            "nearExpiry": candidate.get("nearExpiry"), "daysToExpiry": candidate.get("daysToExpiry"),
                        })
                    continue
            if trading_control.get("autoTradingStopped") and self.settings.paper_trading_respects_stop:
                skipped.append({"candidate": candidate.get("id"), "reason": "manual stop active", "quality": quality})
                continue
            if risk_halt["blocked"]:
                skipped.append({"candidate": candidate.get("id"), "reason": risk_halt["reason"], "quality": quality})
                continue
            runner_sig = (candidate.get("runnerSignal") or {})
            entry_bypass = self._runner_entry_bypass(candidate, runner_sig)
            loss_guard = self._intraday_loss_guard(risk_halt)
            if loss_guard.get("eliteOnly"):
                momentum_override = bool(
                    runner_sig.get("momentumOverride")
                    and float(runner_sig.get("score") or 0) >= 85
                    and self._is_profit_tier_entry(candidate, runner_sig)
                )
            elif loss_guard.get("active"):
                momentum_override = bool(entry_bypass and self._is_profit_tier_entry(candidate, runner_sig))
            else:
                momentum_override = bool(runner_sig.get("momentumOverride") and str(runner_sig.get("confidence") or "").upper() == "HIGH") or entry_bypass
            if pre_trade_psychology.get("tradePermission") == "BLOCK_NEW_TRADES" and not momentum_override:
                skipped.append({"candidate": candidate.get("id"), "reason": "psychology gate: BLOCK_NEW_TRADES", "quality": quality})
                continue
            if pre_trade_psychology.get("tradePermission") == "WAIT" and not momentum_override:
                skipped.append({"candidate": candidate.get("id"), "reason": f"psychology gate: {pre_trade_psychology.get('tradePermission')}", "quality": quality})
                continue
            if self.settings.paper_trading or not self.settings.enable_live_trading:
                strategy_type = str(candidate.get("strategyType") or "SCALP")
                pool = self._capital_pool_for(strategy_type)
                pool_open = self._open_trades_in_pool(pool)
                pool_max = self._max_open_for_pool(pool)
                if pool_open >= pool_max:
                    skipped.append({"candidate": candidate.get("id"), "reason": f"{pool} pool full: {pool_open}/{pool_max} trades open"})
                    continue
                if len(self.open_paper) >= int(self.settings.paper_max_open_trades):
                    skipped.append({"candidate": candidate.get("id"), "reason": f"HARD CAP: {len(self.open_paper)}/{self.settings.paper_max_open_trades} trades already open"})
                    continue
                pool_capital = self._pool_capital(pool, trading_capital)
                opened = self._open_paper_trade(
                    candidate,
                    quality,
                    self._available_capital_for_strategy(strategy_type, trading_capital),
                    pool_capital,
                    session_adj,
                    market_phase,
                    capital_pool=pool,
                )
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
            "positionSizing": {
                **self._position_sizing_summary(candidates, capital.get("tradingCapital", 0)),
                "capitalPools": self._capital_pools_summary(capital.get("tradingCapital", 0)),
            },
            "sessionAdjustments": session_adj,
            "profitLock": profit_lock,
            "paperRiskHalt": risk_halt,
            "psychology": psychology,
            "onlineLearning": online_learning,
            "dailyReport": self.daily_report(),
            "paperSessions": self._paper_sessions_status(),
            "sessionRotation": rotation_event,
            "targetLock": target_lock,
            "performanceAnalysis": self.performance_analysis(),
        }

    def status(self) -> dict[str, Any]:
        capital = float(self.settings.trading_capital_default or 0)
        return {
            "paperTrading": self.settings.paper_trading,
            "shadowTradeAllSignals": self.settings.shadow_trade_all_signals,
            "paperTradingRespectsStop": self.settings.paper_trading_respects_stop,
            "liveTradingEnabled": self.settings.enable_live_trading,
            "capitalPools": self._capital_pools_summary(capital),
            "dualCapitalEnabled": self.settings.paper_dual_capital_enabled,
            "adaptiveExitEnabled": self.settings.paper_ai_adaptive_exit_enabled,
            "openPaperTrades": [trade.to_dict() for trade in self.open_paper.values()],
            "closedPaperTrades": [trade.to_dict() for trade in list(self.closed_paper)[-25:]],
            "orderLifecycle": [event.__dict__ for event in list(self.lifecycle_events)[-50:]],
            "replay": {"storedSnapshots": len(self.replay_buffer)},
            "profitLock": self.profit_lock_status(),
            "paperRiskHalt": self._paper_risk_halt(),
            "psychology": self._psychology_report([], [], self._paper_risk_halt()),
            "onlineLearning": self.learner.status_from_state(),
            "dailyReport": self.daily_report(),
            "paperSessions": self._paper_sessions_status(),
            "targetLock": self._target_lock_status(),
            "performanceAnalysis": self.performance_analysis(),
        }

    def paper_sessions_history(self, limit: int = 50) -> dict[str, Any]:
        return self.paper_sessions.list_sessions(limit)

    def performance_analysis(self) -> dict[str, Any]:
        trades = self._today_closed_trades()
        by_bucket = self._group_trade_summary(trades, lambda trade: self._trade_bucket(trade))
        by_symbol = self._group_trade_summary(trades, lambda trade: trade.symbol or "UNKNOWN")
        by_side = self._group_trade_summary(trades, lambda trade: trade.side or "UNKNOWN")
        by_session = self._group_trade_summary(trades, lambda trade: trade.paper_session_id or "UNKNOWN")
        best_bucket = self._best_summary_key(by_bucket)
        best_symbol = self._best_summary_key(by_symbol)
        best_side = self._best_summary_key(by_side)
        day_summary = self._summarize_trades(trades)
        target_info = self._daily_profit_target()
        target_amount = float(target_info.get("targetAmount") or self.settings.paper_daily_profit_target_amount or 50000.0)
        rolling_proof = self._rolling_proof()
        recent_postmortems = [self._trade_postmortem(trade) for trade in trades[-10:]]
        return {
            "tradingDay": datetime.now(IST).date().isoformat(),
            "target": {
                "capital": float(self.settings.trading_capital_default or 0),
                "dailyProfitAmount": target_amount,
                "dailyProfitPct": target_info.get("targetPct") or round(target_amount / float(self.settings.trading_capital_default or 1) * 100, 2),
                "dayQuality": target_info.get("quality"),
                "qualityReason": target_info.get("reason"),
                "tiers": target_info.get("tiers"),
                "currentNetPnl": day_summary["netPnl"],
                "remainingToTarget": round(target_amount - float(day_summary["netPnl"] or 0), 2),
            },
            "summary": day_summary,
            "byBucket": by_bucket,
            "bySymbol": by_symbol,
            "bySide": by_side,
            "bySession": by_session,
            "bestObserved": {
                "bucket": best_bucket,
                "symbol": best_symbol,
                "side": best_side,
            },
            "rollingProof": rolling_proof,
            "liveReadiness": self._live_readiness(rolling_proof),
            "recentPostmortems": recent_postmortems,
            "breadthReadiness": self._breadth_readiness(),
            "dailyImprovementPlan": self._daily_improvement_plan(),
            "institutionalAggressionProfiles": self._institutional_profile_recommendations(by_bucket, by_symbol, by_side),
            "rulesApplied": [
                "Use day-level paper PnL for profit factor; current-session report is separate.",
                "Do not open duplicate paper trades on the same instrument key during cooldown.",
                "Stop new paper entries after the configured daily profit target or daily loss guard is hit.",
            ],
        }

    def reset(self, preserve_history: bool = True) -> dict[str, Any]:
        """Reset active trading state. By default preserves closed trade history for analysis."""
        self.replay_buffer.clear()
        self.open_paper.clear()
        if not preserve_history:
            self.closed_paper.clear()
        self.lifecycle_events.clear()
        AutoTraderEngine._shared_recent_signal_times.clear()
        self._persist_paper_trades_file()
        AutoTraderEngine._shared_learning_samples = 0
        AutoTraderEngine._shared_learning_score = 50.0
        AutoTraderEngine._shared_last_learning_update = None
        self.paper_sessions.start_session("daily_reset")
        return {"reset": True, "preservedHistory": preserve_history,
                "allTimeTrades": len(self.closed_paper), "status": self.status()}

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
            restored_session_id = str(payload.get("currentSessionId") or "")
            restored_started_at: str | None = None
            for item in payload.get("open") or []:
                if not isinstance(item, dict):
                    continue
                trade = PaperTrade.from_dict(item)
                if trade.status == "OPEN":
                    self.open_paper[trade.id] = trade
                    if trade.paper_session_id == restored_session_id:
                        restored_started_at = restored_started_at or trade.opened_at
            limit = max(100, int(self.settings.paper_trades_persist_limit))
            for item in (payload.get("closed") or [])[-limit:]:
                if not isinstance(item, dict):
                    continue
                trade = PaperTrade.from_dict(item)
                if trade.status == "EXITED":
                    self.closed_paper.append(trade)
                    if trade.paper_session_id == restored_session_id:
                        restored_started_at = restored_started_at or trade.opened_at
            if restored_session_id:
                self.paper_sessions.restore_current_session(restored_session_id, started_at=restored_started_at)
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
        daily_target = self._daily_profit_target()
        daily_target_amount = float(daily_target.get("targetAmount") or self.settings.paper_daily_profit_target_amount or 0)
        daily_target_locked = bool(self.settings.paper_daily_target_lock_enabled and daily_target_amount > 0 and net >= daily_target_amount)
        if daily_target_locked:
            active = {"name": "daily_target", "pct": round(daily_target_amount / capital * 100, 2) if capital else 0, "amount": round(daily_target_amount, 2)}
            locked_profit = max(locked_profit, daily_target_amount)
            giveback = max(0, net - locked_profit)
            block_new = True
        if rotation_enabled and self.settings.paper_trading and not daily_target_locked:
            block_new = False
        if daily_target_locked:
            message = f"Daily INR {daily_target_amount:,.0f} paper target locked; no more paper entries today"
        elif rotation_enabled and active:
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
            "dailyTargetAmount": daily_target_amount,
            "dailyTargetPct": daily_target.get("targetPct"),
            "dayQuality": daily_target.get("quality"),
            "dailyTargetLocked": daily_target_locked,
        }

    def _session_closed_trades(self) -> list[PaperTrade]:
        session_id = self.paper_sessions.current_id()
        return [trade for trade in self.closed_paper if (trade.paper_session_id or session_id) == session_id]

    def _session_report(self) -> dict[str, Any]:
        return self.paper_sessions.build_report(self._session_closed_trades())

    def _trade_timestamp(self, trade: PaperTrade) -> datetime | None:
        raw = trade.opened_at or trade.exited_at
        if not raw:
            return None
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return None

    def _today_closed_trades(self) -> list[PaperTrade]:
        today = datetime.now(IST).date()
        trades: list[PaperTrade] = []
        for trade in self.closed_paper:
            timestamp = self._trade_timestamp(trade)
            if timestamp and timestamp.astimezone(IST).date() == today:
                trades.append(trade)
        return trades

    def _trade_bucket(self, trade: PaperTrade) -> str:
        timestamp = self._trade_timestamp(trade)
        if not timestamp:
            return "UNKNOWN"
        local_time = timestamp.astimezone(IST).time()
        if local_time.hour == 9 and local_time.minute >= 15 or local_time.hour == 10 and local_time.minute <= 30:
            return "OPEN_DRIVE"
        if (local_time.hour == 11 and local_time.minute >= 30) or local_time.hour == 12 or (local_time.hour == 13 and local_time.minute <= 30):
            return "MIDDAY_CHOP"
        if (local_time.hour == 14 and local_time.minute >= 30) or (local_time.hour == 15 and local_time.minute <= 15):
            return "CLOSING_MOMENTUM"
        if (local_time.hour > 9 or (local_time.hour == 9 and local_time.minute >= 15)) and (local_time.hour < 15 or (local_time.hour == 15 and local_time.minute <= 30)):
            return "NORMAL"
        return "OUTSIDE_LIVE"

    def _summarize_trades(self, trades: list[PaperTrade]) -> dict[str, Any]:
        wins = [trade for trade in trades if trade.pnl > 0]
        losses = [trade for trade in trades if trade.pnl < 0]
        gross_profit = sum(float(trade.pnl) for trade in wins)
        gross_loss = abs(sum(float(trade.pnl) for trade in losses))
        net = gross_profit - gross_loss
        return {
            "paperTrades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "winRate": round((len(wins) / len(trades)) * 100, 2) if trades else 0.0,
            "grossProfit": round(gross_profit, 2),
            "grossLoss": round(gross_loss, 2),
            "netPnl": round(net, 2),
            "profitFactor": round(gross_profit / gross_loss, 3) if gross_loss else round(gross_profit, 3),
            "avgPnl": round(net / len(trades), 2) if trades else 0.0,
            "maxDrawdown": round(self._max_drawdown([trade.pnl for trade in trades]), 2),
        }

    def _group_trade_summary(self, trades: list[PaperTrade], key_fn) -> dict[str, dict[str, Any]]:
        groups: dict[str, list[PaperTrade]] = {}
        for trade in trades:
            groups.setdefault(str(key_fn(trade)), []).append(trade)
        return {key: self._summarize_trades(items) for key, items in sorted(groups.items())}

    def _best_summary_key(self, summaries: dict[str, dict[str, Any]]) -> str | None:
        eligible = [
            (key, value)
            for key, value in summaries.items()
            if int(value.get("paperTrades") or 0) >= 3
        ]
        if not eligible:
            eligible = list(summaries.items())
        if not eligible:
            return None
        return max(eligible, key=lambda item: (float(item[1].get("profitFactor") or 0), float(item[1].get("netPnl") or 0)))[0]

    def _rolling_proof(self, limit: int | None = None) -> dict[str, Any]:
        limit = int(limit or self.settings.paper_live_readiness_min_trades)
        # Use all in-memory closed trades (up to deque maxlen=2000), not just today
        all_closed = list(self.closed_paper)
        trades = all_closed[-limit:]
        summary = self._summarize_trades(trades)
        # Total all-time count (for 100-trade gate progress across resets)
        all_time_total = len(all_closed)
        capital = float(self.settings.trading_capital_default or 0)
        max_drawdown_pct = (float(summary.get("maxDrawdown") or 0) / capital * 100) if capital > 0 else 0.0
        avg_win = (float(summary.get("grossProfit") or 0) / int(summary.get("wins") or 1)) if int(summary.get("wins") or 0) else 0.0
        avg_loss = (float(summary.get("grossLoss") or 0) / int(summary.get("losses") or 1)) if int(summary.get("losses") or 0) else 0.0
        return {
            **summary,
            "windowTrades": limit,
            "allTimeTrades": all_time_total,
            "sampleComplete": len(trades) >= limit,
            "maxDrawdownPct": round(max_drawdown_pct, 2),
            "avgWin": round(avg_win, 2),
            "avgLoss": round(avg_loss, 2),
            "expectancy": round((float(summary.get("netPnl") or 0) / len(trades)), 2) if trades else 0.0,
        }

    def _live_readiness(self, rolling: dict[str, Any]) -> dict[str, Any]:
        checks = [
            {
                "name": "Sample size",
                "passed": bool(rolling.get("sampleComplete")),
                "value": rolling.get("paperTrades"),
                "required": self.settings.paper_live_readiness_min_trades,
            },
            {
                "name": "Profit factor",
                "passed": float(rolling.get("profitFactor") or 0) >= float(self.settings.paper_live_readiness_min_profit_factor),
                "value": rolling.get("profitFactor"),
                "required": self.settings.paper_live_readiness_min_profit_factor,
            },
            {
                "name": "Win rate",
                "passed": float(rolling.get("winRate") or 0) >= float(self.settings.paper_live_readiness_min_win_rate_pct),
                "value": rolling.get("winRate"),
                "required": self.settings.paper_live_readiness_min_win_rate_pct,
            },
            {
                "name": "Max drawdown",
                "passed": float(rolling.get("maxDrawdownPct") or 100) <= float(self.settings.paper_live_readiness_max_drawdown_pct),
                "value": rolling.get("maxDrawdownPct"),
                "required": f"<= {self.settings.paper_live_readiness_max_drawdown_pct}",
            },
            {
                "name": "Average win/loss",
                "passed": float(rolling.get("avgWin") or 0) > float(rolling.get("avgLoss") or 0),
                "value": {"avgWin": rolling.get("avgWin"), "avgLoss": rolling.get("avgLoss")},
                "required": "avgWin > avgLoss",
            },
        ]
        passed = all(bool(check["passed"]) for check in checks)
        return {
            "ready": passed,
            "mode": "PAPER_ONLY" if not passed else "SMALL_SIZE_REVIEW_REQUIRED",
            "checks": checks,
            "message": "Not live ready. Keep live trading disabled until all rolling proof gates pass." if not passed else "Paper proof passed; only then consider tiny live pilot with manual approval.",
        }

    def _trade_postmortem(self, trade: PaperTrade) -> dict[str, Any]:
        bucket = self._trade_bucket(trade)
        pnl = float(trade.pnl or 0)
        reason = trade.exit_reason or "unknown"
        quality = "GOOD_WIN" if pnl > 0 else "CONTROLLED_LOSS" if abs(pnl) <= float(self.settings.paper_max_trade_loss_amount or 5000) else "OVERSIZED_LOSS"
        findings: list[str] = []
        actions: list[str] = []
        if pnl < 0 and "momentum decay" in reason:
            findings.append("Momentum failed after entry.")
            actions.append("Require stronger breadth and premium velocity before next similar entry.")
        if pnl < 0 and abs(pnl) > float(self.settings.paper_max_trade_loss_amount or 5000):
            findings.append("Loss exceeded intended per-trade cap.")
            actions.append("Reduce lots using stop-risk sizing before the next paper session.")
        if bucket == "MIDDAY_CHOP":
            findings.append("Trade occurred in chop-prone time window.")
            actions.append("Preserve midday scalp discipline in all sessions.")
        if trade.side == "CALL":
            findings.append("CALL side has recently underperformed in paper data.")
            actions.append("Require bullish breadth and chart alignment for any CALL.")
        if pnl > 0:
            findings.append("Trade contributed positively to paper proof.")
            actions.append("Preserve the same entry discipline; do not increase size after one win.")
        return {
            "id": trade.id,
            "symbol": trade.symbol,
            "side": trade.side,
            "bucket": bucket,
            "pnl": round(pnl, 2),
            "exitReason": reason,
            "quality": quality,
            "findings": findings or ["No special issue detected."],
            "nextActions": actions or ["Continue monitoring under current rules."],
        }

    def _breadth_readiness(self) -> dict[str, Any]:
        snapshot = self._latest_market_snapshot or {}
        count = int(snapshot.get("count") or 0)
        required = int(self.settings.market_breadth_recommended_count)
        return {
            "available": bool(snapshot.get("available")),
            "count": count,
            "recommendedCount": required,
            "sufficient": count >= required,
            "breadth": snapshot.get("breadth") or {},
            "message": "Breadth coverage is institutional-grade." if count >= required else "Add more NIFTY/BankNifty/sector instruments; current breadth coverage is too small for full confidence.",
        }

    def _institutional_profile_recommendations(
        self,
        by_bucket: dict[str, dict[str, Any]],
        by_symbol: dict[str, dict[str, Any]],
        by_side: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        best_bucket = self._best_summary_key(by_bucket)
        best_symbol = self._best_summary_key(by_symbol)
        best_side = self._best_summary_key(by_side)
        return {
            "recommendedBaseProfile": "realistic_aggressive",
            "why": [
                "The target is 10% on 5L capital, so the system needs aggressive paper allocation only during proven windows.",
                "Today only the strongest directional runner window produced target-sized profits; broad CALL scalping was negative.",
                "Use safe gates in weak windows and size only when session/bias evidence agrees.",
            ],
            "bestObservedBucket": best_bucket,
            "bestObservedSymbol": best_symbol,
            "bestObservedSide": best_side,
            "timeWindowSettings": {
                "OPEN_DRIVE": {
                    "windowIst": "09:15-10:30",
                    "profile": "safe_beginner",
                    "permission": "A_PLUS_ONLY",
                    "allocationPctMultiplier": 0.45,
                    "minEntryTqs": 88,
                    "minRunnerScore": 92,
                    "maxHoldSeconds": 90,
                    "note": "Today open-drive overtraded and lost heavily; trade only A+ direction-aligned runners.",
                },
                "MIDDAY_CHOP": {
                    "windowIst": "11:30-13:30",
                    "profile": "safe_beginner",
                    "permission": "NO_NORMAL_TRADES",
                    "allocationPctMultiplier": 0.0,
                    "minEntryTqs": 90,
                    "minRunnerScore": 94,
                    "maxHoldSeconds": 60,
                    "note": "Pause normal entries; allow only exceptional runners if future code enables bypass.",
                },
                "NORMAL": {
                    "windowIst": "10:30-11:30 and 13:30-14:30",
                    "profile": "balanced_pro",
                    "permission": "SELECTIVE",
                    "allocationPctMultiplier": 0.6,
                    "minEntryTqs": 84,
                    "minRunnerScore": 90,
                    "maxHoldSeconds": 120,
                    "note": "Normal window was below breakeven today; reduce frequency and wait for side confirmation.",
                },
                "CLOSING_MOMENTUM": {
                    "windowIst": "14:30-15:15",
                    "profile": "realistic_aggressive",
                    "permission": "AGGRESSIVE_IF_ELITE_RUNNER",
                    "allocationPctMultiplier": 1.0,
                    "minEntryTqs": 82,
                    "minRunnerScore": 86,
                    "maxHoldSeconds": 240,
                    "note": "Best observed window today; allow aggressive paper runners only while daily risk/target guard is clear.",
                },
            },
            "bestTiming": {
                "primaryWindowIst": "14:30-15:15",
                "primaryBucket": "CLOSING_MOMENTUM",
                "primaryProfile": "realistic_aggressive",
                "primarySetup": "Elite SENSEX PUT explosive runner",
                "avoidWindowsIst": ["11:30-13:30 unless elite runner", "opening drive until paper data improves"],
                "rule": "Be aggressive only in the best observed window and only when runner is elite; otherwise protect capital.",
            },
        }

    def _day_aggregate_from_trades(self) -> dict[str, Any]:
        trades = self._today_closed_trades()
        summary = self._summarize_trades(trades)
        session_ids = {trade.paper_session_id for trade in trades if trade.paper_session_id}
        return {
            "tradingDay": datetime.now(IST).date().isoformat(),
            "sessionsCompleted": len(self.paper_sessions.completed_today()),
            "sessionsIncludingCurrent": max(1, len(session_ids) + (0 if self.paper_sessions.current_id() in session_ids else 1)),
            "paperTrades": summary["paperTrades"],
            "wins": summary["wins"],
            "losses": summary["losses"],
            "grossProfit": summary["grossProfit"],
            "grossLoss": summary["grossLoss"],
            "netPnl": summary["netPnl"],
            "profitFactor": summary["profitFactor"],
        }

    def _paper_sessions_status(self) -> dict[str, Any]:
        payload = self.paper_sessions.status_payload(self._session_report())
        payload["dayAggregate"] = self._day_aggregate_from_trades()
        payload["singleDailySession"] = self.settings.paper_single_daily_session
        payload["targetLockEnabled"] = self.settings.paper_daily_target_lock_enabled
        return payload

    def daily_report(self) -> dict[str, Any]:
        session_report = self._session_report()
        day_aggregate = self._day_aggregate_from_trades()
        trades = self._today_closed_trades()
        day_report = self._summarize_trades(trades)
        losses = [trade for trade in trades if trade.pnl < 0]
        return {
            "totalSignals": len(self.lifecycle_events),
            "paperTrades": day_report["paperTrades"],
            "openTrades": len(self.open_paper),
            "wins": day_report["wins"],
            "losses": day_report["losses"],
            "winRate": day_report["winRate"],
            "grossProfit": day_report["grossProfit"],
            "grossLoss": day_report["grossLoss"],
            "profitFactor": day_report["profitFactor"],
            "netPnl": day_report["netPnl"],
            "maxDrawdown": day_report["maxDrawdown"],
            "sessionId": session_report.get("sessionId"),
            "sessionNumber": session_report.get("sessionNumber"),
            "currentSession": session_report,
            "dayAggregate": day_aggregate,
            "completedSessionsToday": len(self.paper_sessions.completed_today()),
            "reasonForLosses": self._loss_reasons(losses),
        }

    def _price_maps_from_snapshots(self, snapshots: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        price_by_id: dict[str, dict[str, Any]] = {}
        price_by_instrument: dict[str, dict[str, Any]] = {}
        for snapshot in snapshots.values():
            for candidate in snapshot.get("suggestedTrades") or []:
                self._index_price_payload(candidate, price_by_id, price_by_instrument)
            for candidate in snapshot.get("explosiveRunnerWatchlist") or []:
                self._index_price_payload(candidate, price_by_id, price_by_instrument)
            for candidate in snapshot.get("paperPriceWatch") or []:
                self._index_price_payload(candidate, price_by_id, price_by_instrument)
        return price_by_id, price_by_instrument

    def _open_marked_pnl(self, snapshots: dict[str, Any]) -> tuple[float, dict[str, float]]:
        price_by_id, price_by_instrument = self._price_maps_from_snapshots(snapshots)
        marks: dict[str, float] = {}
        total = 0.0
        for trade_id, trade in self.open_paper.items():
            candidate = price_by_id.get(trade_id) or price_by_instrument.get(str(trade.instrument_key or ""))
            current = float((candidate or {}).get("lastPremium") or (candidate or {}).get("premium") or trade.entry_price or 0)
            if current <= 0:
                current = trade.entry_price
            charges = self._charges_estimate(trade.entry_price, current, trade.quantity)
            pnl = ((current - trade.entry_price - trade.spread_cost - trade.slippage_estimate) * trade.quantity) - charges
            marks[trade_id] = round(pnl, 2)
            total += pnl
        return round(total, 2), marks

    def _paper_day_quality(self, snapshots: dict[str, Any] | None = None) -> dict[str, Any]:
        day_summary = self._summarize_trades(self._today_closed_trades())
        trades = int(day_summary.get("paperTrades") or 0)
        net = float(day_summary.get("netPnl") or 0)
        profit_factor = float(day_summary.get("profitFactor") or 0)
        win_rate = float(day_summary.get("winRate") or 0)
        scores: list[float] = []
        runner_scores: list[float] = []
        for snapshot in (snapshots or {}).values():
            for candidate in (snapshot.get("suggestedTrades") or []) + (snapshot.get("explosiveRunnerWatchlist") or []) + (snapshot.get("paperPriceWatch") or []):
                score = float(candidate.get("tqs") or candidate.get("score") or 0)
                if score > 0:
                    scores.append(score)
                runner = candidate.get("runnerSignal") or candidate
                runner_score = float(runner.get("score") or 0)
                if runner_score > 0:
                    runner_scores.append(runner_score)
        avg_score = sum(scores) / len(scores) if scores else 0.0
        best_score = max(scores or [0.0])
        best_runner_score = max(runner_scores or [0.0])

        quality = "WORST"
        reason = "No proven edge yet; use 5% lock and only perfect setups."
        if trades >= 5:
            if net > 0 and profit_factor >= 1.5 and win_rate >= 40:
                quality = "GOOD"
                reason = "Positive paper day with profit factor above 1.5; try 10% target."
            elif (net >= 0 and profit_factor >= 1.0) or win_rate >= 45:
                quality = "MEDIUM"
                reason = "Mixed but workable paper day; use 8% target."
            else:
                quality = "WORST"
                reason = "Weak paper day or drawdown active; lock quickly at 5% if recovered."
        elif best_runner_score >= 92 and avg_score >= 82:
            quality = "GOOD"
            reason = "Live signal quality is strong: runner score >= 92 and average score >= 82."
        elif best_runner_score >= 88 or best_score >= 86:
            quality = "MEDIUM"
            reason = "Live signal quality is moderate; use 8% target."

        return {
            "quality": quality,
            "reason": reason,
            "metrics": {
                "paperTrades": trades,
                "netPnl": round(net, 2),
                "profitFactor": profit_factor,
                "winRate": win_rate,
                "avgSignalScore": round(avg_score, 2),
                "bestSignalScore": round(best_score, 2),
                "bestRunnerScore": round(best_runner_score, 2),
            },
        }

    def _daily_profit_target(self, snapshots: dict[str, Any] | None = None) -> dict[str, Any]:
        quality = self._paper_day_quality(snapshots)
        capital = float(self.settings.trading_capital_default or 0)
        quality_key = str(quality.get("quality") or "WORST")
        pct_by_quality = {
            "GOOD": float(self.settings.paper_daily_profit_target_good_pct),
            "MEDIUM": float(self.settings.paper_daily_profit_target_medium_pct),
            "WORST": float(self.settings.paper_daily_profit_target_worst_pct),
        }
        pct = pct_by_quality.get(quality_key, float(self.settings.paper_daily_profit_target_worst_pct))
        amount = capital * pct / 100 if capital > 0 else float(self.settings.paper_daily_profit_target_amount or 0)
        return {
            "quality": quality_key,
            "reason": quality.get("reason"),
            "targetPct": round(pct, 2),
            "targetAmount": round(amount, 2),
            "capital": capital,
            "metrics": quality.get("metrics") or {},
            "tiers": {
                "worstPct": float(self.settings.paper_daily_profit_target_worst_pct),
                "mediumPct": float(self.settings.paper_daily_profit_target_medium_pct),
                "goodPct": float(self.settings.paper_daily_profit_target_good_pct),
            },
        }

    def _target_lock_status(self, snapshots: dict[str, Any] | None = None) -> dict[str, Any]:
        target_info = self._daily_profit_target(snapshots)
        target = float(target_info.get("targetAmount") or 0)
        day_net = float(self._day_aggregate_from_trades().get("netPnl") or 0)
        open_marked_pnl, marks = self._open_marked_pnl(snapshots or {}) if snapshots else (0.0, {})
        projected = day_net + open_marked_pnl
        enabled = bool(self.settings.paper_daily_target_lock_enabled and target > 0)
        return {
            "enabled": enabled,
            "targetAmount": target,
            "targetPct": target_info.get("targetPct"),
            "dayQuality": target_info.get("quality"),
            "qualityReason": target_info.get("reason"),
            "qualityMetrics": target_info.get("metrics"),
            "targetTiers": target_info.get("tiers"),
            "closedNetPnl": round(day_net, 2),
            "openMarkedPnl": round(open_marked_pnl, 2),
            "projectedNetPnl": round(projected, 2),
            "remainingToTarget": round(target - projected, 2),
            "locked": bool(enabled and day_net >= target),
            "projectedLocked": bool(enabled and projected >= target),
            "openMarks": marks,
            "mode": "single_daily_session_target_lock" if self.settings.paper_single_daily_session else "session_rotation",
        }

    def _maybe_lock_daily_profit_target(self, snapshots: dict[str, Any]) -> dict[str, Any]:
        status = self._target_lock_status(snapshots)
        if not status.get("enabled") or not status.get("projectedLocked") or not self.open_paper:
            return {**status, "lockedTrades": []}
        price_by_id, price_by_instrument = self._price_maps_from_snapshots(snapshots)
        locked: list[dict[str, Any]] = []
        reason = f"daily paper target lock INR {float(status.get('targetAmount') or 0):,.0f}"
        for trade_id, trade in list(self.open_paper.items()):
            candidate = price_by_id.get(trade_id) or price_by_instrument.get(str(trade.instrument_key or ""))
            current = float((candidate or {}).get("lastPremium") or (candidate or {}).get("premium") or trade.entry_price or 0)
            if current <= 0:
                current = trade.entry_price
            trade.status = "EXITED"
            trade.exit_price = current
            trade.exit_reason = reason
            trade.exited_at = datetime.now(timezone.utc).isoformat()
            charges = self._charges_estimate(trade.entry_price, current, trade.quantity)
            trade.charges_estimate = charges
            trade.pnl = ((current - trade.entry_price - trade.spread_cost - trade.slippage_estimate) * trade.quantity) - charges
            trade.lifecycle.append(LifecycleEvent("EXITED", trade.exited_at, reason, {"exit": current, "pnl": trade.pnl, "charges": charges, "targetLock": True}))
            self.closed_paper.append(trade)
            AutoTraderEngine._shared_recent_signal_times[trade_id] = monotonic()
            if trade.instrument_key:
                AutoTraderEngine._shared_recent_signal_times[f"instrument:{trade.instrument_key}"] = monotonic()
            self.lifecycle_events.extend(trade.lifecycle[-1:])
            del self.open_paper[trade_id]
            locked.append(trade.to_dict())
        if locked:
            self._persist_paper_trades_file()
        refreshed = self._target_lock_status(snapshots)
        return {**refreshed, "lockedTrades": locked, "reason": reason}

    def _maybe_rotate_paper_session(self, snapshots: dict[str, Any], session_adj: dict[str, Any], trading_capital: float) -> dict[str, Any] | None:
        if self.settings.paper_single_daily_session or not self.settings.paper_session_rotation_enabled:
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
        result = paper_session_adjustments(
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
            max_catch_mode=bool(self.settings.paper_max_catch_mode),
            unified_scalp_profile=bool(self.settings.paper_unified_scalp_session_profile),
        )
        news = self._latest_news_state or {}
        impact = news.get("impact") or {}
        if impact.get("avoidFreshTrades") and not result.get("blockNewPaperTrades"):
            # Block regular trades but NOT momentum override — velocity overrides news hesitation
            result["blockNewPaperTrades"] = True
            result["blockReason"] = f"News: avoid regular entries — {news.get('eventRisk')} risk, {news.get('sentiment')} sentiment (momentum override still active)"
            result.setdefault("adjustments", []).append("News avoidFreshTrades: scalps paused, momentum override runners still allowed")
        elif impact.get("raiseTqs"):
            result["minEntryTqs"] = int(result.get("minEntryTqs", 74)) + 4
            result.setdefault("adjustments", []).append(f"News event risk ({news.get('eventRisk')}): TQS floor +4")
        if impact.get("allowRunnerBias"):
            result["minRunnerScore"] = max(68.0, float(result.get("minRunnerScore", 78.0)) - 5.0)
            result.setdefault("adjustments", []).append("News confirms direction: runner score threshold -5")
        result["newsImpact"] = impact
        result["newsSentiment"] = news.get("sentiment", "neutral")
        result["newsEventRisk"] = news.get("eventRisk", "LOW")
        result["newsTradingImplication"] = news.get("tradingImplication")
        return result

    def _elite_runner_only_mode(self) -> bool:
        return bool(self.settings.paper_elite_runner_only or self.settings.paper_high_confidence_only)

    def _is_ultra_elite_runner_entry(self, candidate: dict[str, Any], runner: dict[str, Any] | None = None) -> bool:
        """Ultra-high-profile runner: elite + aligned tape + score≥92 + strong velocity/volume."""
        runner = runner or candidate.get("runnerSignal") or {}
        if candidate.get("strategyType") != "EXPLOSIVE_RUNNER":
            return False
        if self._is_explosion_chase(candidate, runner):
            return False
        if str(runner.get("confidence") or "").upper() != "HIGH":
            return False
        if not runner.get("eliteRunner"):
            return False
        if not runner.get("momentumAligned"):
            return False
        score = float(runner.get("score") or candidate.get("tqs") or 0)
        min_score = float(self.settings.paper_ultra_elite_min_runner_score)
        if score < min_score:
            return False
        metrics = self._runner_metrics(runner)
        premium_velocity = float(runner.get("premiumVelocityPct") or metrics.get("premiumVelocity") or 0)
        volume_accel = float(runner.get("volumeAcceleration") or metrics.get("volumeAcceleration") or 0)
        if premium_velocity < float(self.settings.paper_ultra_elite_min_velocity_pct):
            return False
        if volume_accel < float(self.settings.paper_ultra_elite_min_volume_accel):
            return False
        premium = float(candidate.get("lastPremium") or runner.get("premium") or 0)
        if premium <= 0:
            return False
        premium_min = float(self.settings.paper_min_premium_ltp or self.settings.explosive_runner_premium_min)
        premium_max = float(self.settings.explosive_runner_premium_max)
        return premium_min <= premium <= premium_max

    def _is_elite_runner_entry(self, candidate: dict[str, Any], runner: dict[str, Any] | None = None) -> bool:
        """High-profile runner gate — ultra-elite when elite-only mode is active."""
        if self._elite_runner_only_mode():
            return self._is_ultra_elite_runner_entry(candidate, runner)
        runner = runner or candidate.get("runnerSignal") or {}
        if candidate.get("strategyType") != "EXPLOSIVE_RUNNER":
            return False
        if self._is_explosion_chase(candidate, runner):
            return False
        if str(runner.get("confidence") or "").upper() != "HIGH":
            return False
        if not runner.get("eliteRunner"):
            return False
        score = float(runner.get("score") or candidate.get("tqs") or 0)
        min_score = float(self.settings.paper_high_confidence_min_runner_score)
        if score < min_score:
            return False
        metrics = self._runner_metrics(runner)
        premium_velocity = float(runner.get("premiumVelocityPct") or metrics.get("premiumVelocity") or 0)
        if premium_velocity < float(self.settings.paper_profit_tier_a_min_velocity_pct):
            return False
        premium = float(candidate.get("lastPremium") or runner.get("premium") or 0)
        if premium <= 0:
            return False
        premium_min = float(self.settings.paper_min_premium_ltp or self.settings.explosive_runner_premium_min)
        premium_max = float(self.settings.explosive_runner_premium_max)
        return premium_min <= premium <= premium_max

    def _is_scalp_candidate(self, candidate: dict[str, Any]) -> bool:
        if candidate.get("chopBlocked"):
            return False
        premium = float(candidate.get("lastPremium") or 0)
        if premium <= 0:
            return False
        tqs = int(candidate.get("tqs") or 0)
        return tqs >= int(self.settings.paper_scalping_min_entry_tqs)

    def _prepare_execution_candidate(self, candidate: dict[str, Any]) -> dict[str, Any] | None:
        """Route weak runners to scalping; only ultra-elite stays explosive."""
        strategy = str(candidate.get("strategyType") or "SCALP").upper()
        runner = candidate.get("runnerSignal") or {}
        if strategy != "EXPLOSIVE_RUNNER":
            return candidate
        if self._is_ultra_elite_runner_entry(candidate, runner):
            return candidate
        if self.settings.paper_prefer_scalping and self._is_scalp_candidate(candidate):
            scalp = dict(candidate)
            scalp["strategyType"] = "SCALP"
            profile = dict(scalp.get("optimizedProfile") or {})
            profile["executionStyle"] = "HIGH_WIN_SCALP"
            profile["targetPoints"] = min(float(profile.get("targetPoints") or self.settings.paper_target_points), float(self.settings.paper_quick_profit_points) + 4.0)
            profile["stopPoints"] = min(float(profile.get("stopPoints") or self.settings.paper_stop_points), float(self.settings.paper_stop_points))
            scalp["optimizedProfile"] = profile
            return scalp
        return None

    def _is_catchable_runner(self, candidate: dict[str, Any], runner: dict[str, Any] | None = None) -> bool:
        """Max-catch mode: enter on in-range runners with tape activity."""
        if not self.settings.paper_max_catch_mode:
            return False
        if self._elite_runner_only_mode():
            return self._is_elite_runner_entry(candidate, runner)
        runner = runner or candidate.get("runnerSignal") or {}
        if candidate.get("strategyType") != "EXPLOSIVE_RUNNER":
            return False
        score = float(runner.get("score") or candidate.get("tqs") or 0)
        if score < float(self.settings.paper_max_catch_min_runner_score):
            return False
        premium = float(candidate.get("lastPremium") or runner.get("premium") or 0)
        if premium < float(self.settings.explosive_runner_premium_min):
            return False
        if premium > float(self.settings.paper_momentum_max_entry_premium):
            return False
        metrics = self._runner_metrics(runner)
        premium_velocity = float(runner.get("premiumVelocityPct") or metrics.get("premiumVelocity") or 0)
        volume_accel = float(runner.get("volumeAcceleration") or metrics.get("volumeAcceleration") or 0)
        effective_volume = float(candidate.get("effectiveVolume") or 0)
        if runner.get("momentumOverride") or runner.get("momentumSurge"):
            return True
        if premium_velocity >= float(self.settings.paper_momentum_explosion_velocity_pct):
            return True
        if volume_accel >= float(self.settings.paper_momentum_explosion_volume_accel):
            return True
        if effective_volume > 0 and score >= 58:
            return True
        return score >= 65

    def _intraday_loss_guard(self, risk_halt: dict[str, Any] | None = None) -> dict[str, Any]:
        risk_halt = risk_halt or self._paper_risk_halt()
        day_net = float(risk_halt.get("dayNetPnl") or 0)
        day_summary = self._summarize_trades(self._today_closed_trades())
        trades = int(day_summary.get("paperTrades") or day_summary.get("trades") or 0)
        win_rate = float(day_summary.get("winRate") or 0)
        strict = day_net < -8000 or (trades >= 3 and win_rate < 35 and day_net < 0)
        elite_only = day_net < -15000 or (trades >= 4 and win_rate < 30 and day_net < 0)
        return {
            "active": strict,
            "eliteOnly": elite_only,
            "dayNetPnl": round(day_net, 2),
            "winRatePct": round(win_rate, 2),
            "trades": trades,
        }

    def _daily_improvement_plan(self) -> dict[str, Any]:
        trading_day = datetime.now(IST).date().isoformat()
        if self._daily_plan_cache and self._daily_plan_day == trading_day:
            return self._daily_plan_cache
        today_trades = self._today_closed_trades()
        today_summary = self._summarize_trades(today_trades)
        rolling = self._rolling_proof(limit=self.settings.paper_rolling_calibration_trades)
        by_side = self._group_trade_summary(list(self.closed_paper)[-self.settings.paper_rolling_calibration_trades :], lambda t: t.side or "UNKNOWN")
        by_bucket = self._group_trade_summary(today_trades, lambda t: self._trade_bucket(t))
        by_symbol = self._group_trade_summary(today_trades, lambda t: t.symbol or "UNKNOWN")
        missed_count = len(AutoTraderEngine._shared_missed_runners)
        plan = build_daily_improvement_plan(
            today_summary=today_summary,
            rolling_summary=rolling,
            by_side=by_side,
            by_bucket=by_bucket,
            by_symbol=by_symbol,
            missed_count=missed_count,
            target_profit_factor=float(self.settings.paper_target_profit_factor),
            target_win_rate_pct=float(self.settings.paper_target_win_rate_pct),
            min_trades_for_calibration=int(self.settings.paper_rolling_calibration_trades),
            unified_scalp_session_profile=bool(self.settings.paper_unified_scalp_session_profile),
        )
        self._daily_plan_cache = plan
        self._daily_plan_day = trading_day
        return plan

    def _profit_tier_label(self, candidate: dict[str, Any], runner: dict[str, Any] | None = None) -> str | None:
        runner = runner or candidate.get("runnerSignal") or {}
        if not self._is_profit_tier_entry(candidate, runner):
            return None
        score = float(runner.get("score") or 0)
        metrics = self._runner_metrics(runner)
        pv = float(runner.get("premiumVelocityPct") or metrics.get("premiumVelocity") or 0)
        if runner.get("momentumOverride") and score >= 85 and pv >= 2.5:
            return "A+"
        if runner.get("eliteRunner") and score >= 88:
            return "A+"
        if score >= float(self.settings.paper_profit_tier_a_min_runner_score) and pv >= float(self.settings.paper_profit_tier_a_min_velocity_pct):
            return "A"
        return "B"

    def _is_profit_tier_entry(self, candidate: dict[str, Any], runner: dict[str, Any] | None = None) -> bool:
        """Profit-first entry: high win-rate setups only; tiers tighten automatically when PF drops."""
        runner = runner or candidate.get("runnerSignal") or {}
        if candidate.get("strategyType") != "EXPLOSIVE_RUNNER":
            return False
        if self._elite_runner_only_mode():
            return self._is_elite_runner_entry(candidate, runner)
        premium = float(candidate.get("lastPremium") or runner.get("premium") or 0)
        if premium <= 0 or self._is_explosion_chase(candidate, runner):
            return False
        plan = self._daily_improvement_plan()
        gates = plan.get("gates") or {}
        side = str(candidate.get("side") or "")
        if side in (gates.get("blockedSides") or []):
            return False
        bucket = str((self._paper_session_settings({})).get("sessionBucket") or "")
        if bucket in (gates.get("blockedBuckets") or []):
            if not (runner.get("momentumOverride") and float(runner.get("score") or 0) >= 85):
                return False
        score = float(runner.get("score") or 0)
        metrics = self._runner_metrics(runner)
        premium_velocity = float(runner.get("premiumVelocityPct") or metrics.get("premiumVelocity") or 0)
        volume_accel = float(runner.get("volumeAcceleration") or metrics.get("volumeAcceleration") or 0)
        min_score = float(gates.get("minRunnerScore") or self.settings.paper_profit_tier_b_min_runner_score)
        min_vel = float(gates.get("minVelocityPct") or self.settings.paper_profit_tier_b_min_velocity_pct)
        min_vol = float(gates.get("minVolumeAccel") or 25.0)
        confidence = str(runner.get("confidence") or "").upper()
        if not self.settings.paper_unified_scalp_session_profile:
            if bucket == "OPEN_DRIVE":
                min_vel = min(min_vel, 1.5)
                min_vol = min(min_vol, 20.0)
                min_score = min(min_score, 68.0)
            elif bucket == "CLOSING_MOMENTUM":
                min_vel = min(min_vel, 1.5)
                min_vol = min(min_vol, 20.0)
                min_score = min(min_score, 70.0)

        # A+ — momentum burst + alignment or elite tape
        if runner.get("momentumOverride") and score >= 85 and premium_velocity >= 2.5:
            if runner.get("momentumAligned") or volume_accel >= 30:
                return True
        if runner.get("eliteRunner") and confidence == "HIGH" and score >= 88 and premium_velocity >= 2.0:
            return True

        # A — surge with direction
        if score >= min_score and premium_velocity >= min_vel and volume_accel >= min_vol:
            if runner.get("momentumOverride") or (runner.get("momentumSurge") and runner.get("momentumAligned")):
                return True
            if confidence == "HIGH" and score >= float(self.settings.paper_profit_tier_a_min_runner_score):
                return True

        # B — wider capture when rolling PF supports it
        if gates.get("allowTierB") and score >= float(self.settings.paper_profit_tier_b_min_runner_score):
            if premium_velocity >= 1.5 and (runner.get("momentumSurge") or volume_accel >= 20):
                return True

        # C — max-catch fallback only when proven profitable
        if gates.get("allowTierC") and self._is_catchable_runner(candidate, runner):
            return True
        return False

    def _is_verified_explosion_entry(self, candidate: dict[str, Any], runner: dict[str, Any] | None = None) -> bool:
        """Tape-confirmed explosion — velocity + volume on a live runner (e.g. ₹45 PE spike)."""
        runner = runner or candidate.get("runnerSignal") or {}
        if candidate.get("strategyType") != "EXPLOSIVE_RUNNER":
            return False
        if self._is_explosion_chase(candidate, runner):
            return False
        score = float(runner.get("score") or 0)
        if score < float(self.settings.paper_max_catch_min_runner_score):
            return False
        metrics = self._runner_metrics(runner)
        premium_velocity = float(runner.get("premiumVelocityPct") or metrics.get("premiumVelocity") or 0)
        volume_accel = float(runner.get("volumeAcceleration") or metrics.get("volumeAcceleration") or 0)
        min_vel = float(self.settings.paper_momentum_explosion_velocity_pct)
        min_vol = float(self.settings.paper_momentum_explosion_volume_accel)
        has_tape = premium_velocity >= min_vel and volume_accel >= min_vol
        if has_tape:
            return True
        if runner.get("momentumOverride") and premium_velocity >= min_vel:
            return True
        return False

    def _runner_entry_bypass(self, candidate: dict[str, Any], runner: dict[str, Any] | None = None) -> bool:
        runner = runner or candidate.get("runnerSignal") or {}
        if self._elite_runner_only_mode():
            return self._is_elite_runner_entry(candidate, runner)
        loss_guard = self._intraday_loss_guard()
        if loss_guard.get("eliteOnly"):
            if not self._is_profit_tier_entry(candidate, runner):
                return False
            score = float(runner.get("score") or 0)
            return bool(
                (runner.get("momentumOverride") and score >= 85)
                or (runner.get("eliteRunner") and score >= 88)
            )
        if self.settings.paper_profit_first_mode:
            if self._is_profit_tier_entry(candidate, runner):
                return True
            if self.settings.paper_profit_explosion_bypass and self.settings.paper_max_catch_mode:
                if self._is_verified_explosion_entry(candidate, runner):
                    return True
                if runner.get("momentumOverride") and float(runner.get("score") or 0) >= 78:
                    metrics = self._runner_metrics(runner)
                    pv = float(runner.get("premiumVelocityPct") or metrics.get("premiumVelocity") or 0)
                    if pv >= float(self.settings.paper_momentum_explosion_velocity_pct):
                        return True
            return False
        if self._is_catchable_runner(candidate, runner):
            return True
        if self._is_momentum_explosion(candidate, runner):
            return True
        return self._is_strong_runner_entry(candidate, runner)

    def _is_strong_runner_entry(self, candidate: dict[str, Any], runner: dict[str, Any] | None = None) -> bool:
        """HIGH-confidence elite — bypass chop/breadth/AI gates."""
        return self._is_elite_runner_entry(candidate, runner)

    def _runner_metrics(self, runner: dict[str, Any]) -> dict[str, Any]:
        return runner.get("metrics") or {}

    def _is_explosion_chase(self, candidate: dict[str, Any], runner: dict[str, Any] | None = None) -> bool:
        """Block late chase entries — not valid premiums like ₹110 CE before a breakout."""
        runner = runner or candidate.get("runnerSignal") or {}
        premium = float(candidate.get("lastPremium") or runner.get("premium") or 0)
        max_entry = float(self.settings.paper_momentum_max_entry_premium)
        if premium > max_entry:
            return True
        metrics = self._runner_metrics(runner)
        premium_velocity = float(runner.get("premiumVelocityPct") or metrics.get("premiumVelocity") or 0)
        chase_floor = float(self.settings.paper_momentum_chase_premium_floor)
        chase_velocity = float(self.settings.paper_momentum_chase_max_velocity_pct)
        # High premium + momentum fading = chasing the tail of the move
        if premium >= chase_floor and premium_velocity < chase_velocity:
            return True
        return False

    def _is_momentum_explosion(self, candidate: dict[str, Any], runner: dict[str, Any] | None = None) -> bool:
        """Premium velocity + volume burst — e.g. ₹45→₹100 with volume spike on 1m chart."""
        runner = runner or candidate.get("runnerSignal") or {}
        metrics = self._runner_metrics(runner)
        premium_velocity = float(runner.get("premiumVelocityPct") or metrics.get("premiumVelocity") or 0)
        volume_accel = float(runner.get("volumeAcceleration") or metrics.get("volumeAcceleration") or 0)
        premium = float(candidate.get("lastPremium") or runner.get("premium") or 0)
        min_vel = float(self.settings.paper_momentum_explosion_velocity_pct)
        min_vol = float(self.settings.paper_momentum_explosion_volume_accel)
        min_premium = float(self.settings.paper_momentum_min_premium_ltp)
        max_premium = float(self.settings.paper_momentum_max_entry_premium)
        if not (min_premium <= premium <= max_premium):
            return False
        if self.settings.paper_max_catch_mode:
            return premium_velocity >= min_vel or volume_accel >= min_vol
        if not runner.get("momentumOverride"):
            return False
        if str(runner.get("confidence") or "").upper() != "HIGH":
            return False
        return premium_velocity >= min_vel and volume_accel >= min_vol

    def _passes_high_confidence_gate(self, candidate: dict[str, Any], runner: dict[str, Any] | None = None) -> tuple[bool, str]:
        runner = runner or candidate.get("runnerSignal") or {}
        if self.settings.paper_max_catch_mode and self._runner_entry_bypass(candidate, runner):
            premium = float(candidate.get("lastPremium") or runner.get("premium") or 0)
            if self._is_explosion_chase(candidate, runner):
                return False, f"chase blocked: premium ₹{premium:.0f}"
            return True, ""
        if not self.settings.paper_high_confidence_only:
            return True, ""
        if self._elite_runner_only_mode() and candidate.get("strategyType") == "EXPLOSIVE_RUNNER":
            if self._is_elite_runner_entry(candidate, runner):
                return True, ""
            score = float(runner.get("score") or candidate.get("tqs") or 0)
            return False, (
                f"elite runner required: confidence={runner.get('confidence')} "
                f"elite={bool(runner.get('eliteRunner'))} score={score:.0f}"
            )
        premium = float(candidate.get("lastPremium") or runner.get("premium") or runner.get("lastPremium") or 0)
        max_entry = float(self.settings.paper_momentum_max_entry_premium)
        min_momentum_ltp = float(self.settings.paper_momentum_min_premium_ltp)
        if self._is_momentum_explosion(candidate, runner):
            if self._is_explosion_chase(candidate, runner):
                return False, f"explosion chase blocked: premium ₹{premium:.0f} above max entry ₹{max_entry:.0f}"
            if premium < min_momentum_ltp:
                return False, f"explosion premium ₹{premium:.0f} below minimum ₹{min_momentum_ltp:.0f}"
            return True, ""
        runner_score = float(runner.get("score") or candidate.get("tqs") or 0)
        if (
            candidate.get("strategyType") == "EXPLOSIVE_RUNNER"
            and str(runner.get("confidence") or "").upper() == "HIGH"
            and runner.get("eliteRunner")
            and runner_score >= 80
        ):
            if self._is_explosion_chase(candidate, runner):
                return False, f"explosion chase blocked: premium ₹{premium:.0f} above max entry ₹{max_entry:.0f}"
            return True, ""
        min_ltp = float(self.settings.paper_min_premium_ltp or 0)
        if min_ltp > 0 and premium < min_ltp:
            # Momentum override and near-expiry options bypass the LTP floor
            # ₹48 near-expiry PE can still be a 100%+ runner — don't miss for LTP
            if runner.get("momentumOverride") or candidate.get("nearExpiry"):
                pass  # bypass — velocity/near-expiry is the signal
            else:
                return False, f"LTP ₹{premium:.0f} below high-confidence minimum ₹{min_ltp:.0f}"
        if candidate.get("strategyType") == "EXPLOSIVE_RUNNER":
            confidence = str(runner.get("confidence") or "").upper()
            score = float(runner.get("score") or candidate.get("tqs") or 0)
            min_score = float(self.settings.paper_high_confidence_min_runner_score)
            momentum_override = bool(runner.get("momentumOverride") and confidence == "HIGH")
            # Elite HIGH confidence (preferred path)
            if confidence == "HIGH" and runner.get("eliteRunner"):
                if score < min_score and not momentum_override:
                    return False, f"runner score {score:.0f} below high-confidence minimum {min_score:.0f}"
            # Allow MEDIUM confidence with strong score (≥75) for more trade opportunities
            elif confidence == "HIGH" and (score >= 75 or momentum_override):
                pass  # HIGH confidence non-elite allowed at score ≥75
            elif confidence == "MEDIUM" and score >= 80 and runner.get("momentumAligned"):
                pass  # MEDIUM confidence with strong tape (score ≥80, momentum) — secondary path
            else:
                return False, f"runner: confidence={confidence} score={score:.0f}; need HIGH+elite or HIGH≥75 or MEDIUM≥80+momentum"
            if not runner.get("momentumAligned") and confidence != "HIGH":
                return False, "non-HIGH-confidence runners require momentum alignment"
            chart_bias = str(candidate.get("chartBias") or "")
            side = str(candidate.get("side") or "")
            if momentum_override:
                if self._is_explosion_chase(candidate, runner):
                    return False, f"explosion chase blocked: premium ₹{premium:.0f} above max entry ₹{max_entry:.0f}"
                return True, ""
            if chart_bias in {"CALL", "PUT"} and side in {"CALL", "PUT"} and side != chart_bias:
                return False, f"runner chart trend conflict: {chart_bias} bias vs {side} trade"
            if chart_bias == "WAIT":
                return False, "runner chart analysis says wait"
            return True, ""
        min_tqs = int(self.settings.paper_high_confidence_min_tqs)
        tqs = int(candidate.get("tqs") or 0)
        if tqs < min_tqs:
            return False, f"TQS {tqs} below high-confidence minimum {min_tqs}"
        chart_bias = str(candidate.get("chartBias") or "")
        side = str(candidate.get("side") or "")
        if chart_bias in {"CALL", "PUT"} and side in {"CALL", "PUT"} and side != chart_bias:
            return False, f"chart trend conflict: {chart_bias} bias vs {side} trade"
        if chart_bias == "WAIT":
            return False, "chart analysis says wait"
        return True, ""

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

    def _is_hard_quality_block(self, reason_text: str) -> bool:
        """Blocks that must never be bypassed — even for explosive runners."""
        lowered = reason_text.lower()
        markers = (
            "cooldown",
            "chart analysis says wait",
            "chart trend conflict",
            "ai quality predictor rejected",
            "below high-confidence minimum",
            "chop filter",
            "breadth does not confirm",
            "max open",
            "max capacity",
            "tqs ",
            "tqs below",
            "runner score below",
            "side underperforming",
            "missing premium",
            "missing effective volume",
            "spread/slippage cost too high",
        )
        return any(marker in lowered for marker in markers)

    def _runner_may_bypass_quality(self, candidate: dict[str, Any], reason_text: str, market_phase: str | None = None) -> bool:
        if self._is_catchable_runner(candidate):
            lowered = reason_text.lower()
            hard_only = ("missing premium", "max open", "hard cap", "max capacity", "already has an open")
            if any(marker in lowered for marker in hard_only):
                return False
            return True
        if not self.settings.paper_runner_bypass_quality_gates:
            return False
        if self._is_hard_quality_block(reason_text):
            return False
        runner = candidate.get("runnerSignal") or {}
        if runner.get("momentumOverride") and str(runner.get("confidence") or "").upper() == "HIGH":
            return True
        return self._is_tradeable_explosive_runner(candidate, market_phase)

    def _is_tradeable_explosive_runner(self, candidate: dict[str, Any], market_phase: str | None = None) -> bool:
        if not self.settings.explosive_runner_enabled:
            return False
        phase = str(market_phase or MarketPhase.LIVE_MARKET.value)
        if phase != MarketPhase.LIVE_MARKET.value:
            return False
        if candidate.get("strategyType") != "EXPLOSIVE_RUNNER":
            return False
        runner = candidate.get("runnerSignal") or {}
        if self._elite_runner_only_mode():
            return self._is_elite_runner_entry(candidate, runner)
        if self.settings.paper_max_catch_mode and self._is_catchable_runner(candidate, runner):
            return True
        # MOMENTUM OVERRIDE: premium velocity burst bypasses all score/elite gates
        confidence = str(runner.get("confidence") or "").upper()
        score = float(runner.get("score") or candidate.get("tqs") or 0)
        if runner.get("momentumOverride") and confidence == "HIGH":
            premium = float(candidate.get("lastPremium") or runner.get("premium") or 0)
            if self._is_explosion_chase(candidate, runner):
                return False
            if premium < float(self.settings.paper_momentum_min_premium_ltp):
                return False
            if self._is_momentum_explosion(candidate, runner):
                return True
            metrics = self._runner_metrics(runner)
            premium_velocity = float(runner.get("premiumVelocityPct") or metrics.get("premiumVelocity") or 0)
            if premium_velocity >= float(self.settings.paper_momentum_override_min_velocity_pct):
                return premium <= float(self.settings.paper_momentum_max_entry_premium)
            return False
        # HIGH confidence + strong score + momentum surge = tradeable without elite flag
        # Catches score=85, surge=True runners that just miss elite threshold (88)
        if confidence == "HIGH" and score >= 80 and runner.get("momentumSurge"):
            return True
        momentum_runner = self._is_momentum_aligned_runner(candidate, runner)
        if not momentum_runner and not self.settings.paper_always_trade_explosive_runners:
            return False
        if runner.get("candidate") is False and not momentum_runner:
            return False
        if not runner.get("eliteRunner"):
            return False
        if confidence != "HIGH":
            return False
        if score < max(self._runner_min_score(runner), float(self.settings.explosive_runner_elite_min_score), float(self.settings.paper_high_confidence_min_runner_score)):
            return False
        premium = float(candidate.get("lastPremium") or runner.get("premium") or runner.get("lastPremium") or 0)
        if premium <= 0:
            return False
        premium_min = float(self.settings.paper_min_premium_ltp if self.settings.paper_high_confidence_only else self.settings.explosive_runner_premium_min)
        premium_max = float(self.settings.explosive_runner_premium_max)
        if not (premium_min <= premium <= premium_max):
            return False
        ok, _ = self._passes_high_confidence_gate(candidate, runner)
        return ok

    def _is_active_scalp_entry(self, candidate: dict[str, Any]) -> bool:
        """Unified scalp mode: SCALP / HIGH_WIN_SCALP entries allowed in every live session."""
        if not self.settings.paper_unified_scalp_session_profile:
            return False
        strategy = str(candidate.get("strategyType") or "").upper()
        style = str((candidate.get("optimizedProfile") or {}).get("executionStyle") or "")
        if strategy != "SCALP" and style != "HIGH_WIN_SCALP":
            return False
        return self._is_scalp_candidate(candidate)

    def _session_entry_allowed(self, candidate: dict[str, Any], session_adj: dict[str, Any], market_phase: str | None = None) -> bool:
        if self._is_active_scalp_entry(candidate):
            return True
        if self._is_tradeable_explosive_runner(candidate, market_phase):
            return True
        if self._elite_runner_only_mode():
            return False
        if self._runner_entry_bypass(candidate):
            return True
        runner = candidate.get("runnerSignal") or {}
        if runner.get("momentumOverride") and str(runner.get("confidence") or "").upper() == "HIGH":
            return True
        if self._is_momentum_explosion(candidate, runner):
            return True
        bypass_score = float(session_adj.get("middayRunnerBypassScore") or 90)
        bypass_all_sessions = bool(session_adj.get("unifiedScalpProfile", self.settings.paper_unified_scalp_session_profile))
        if (
            (bypass_all_sessions or session_adj.get("sessionBucket") == "MIDDAY_CHOP")
            and float(runner.get("score") or 0) >= bypass_score
            and runner.get("momentumSurge")
            and runner.get("momentumAligned")
        ):
            return True
        if not session_adj.get("blockNewPaperTrades"):
            return True
        return False

    def _side_performance_gate(self, candidate: dict[str, Any]) -> str | None:
        trades = self._today_closed_trades()
        if len(trades) < 10:
            return None
        by_side = self._group_trade_summary(trades, lambda trade: trade.side or "UNKNOWN")
        side = str(candidate.get("side") or "")
        side_summary = by_side.get(side)
        best_side = self._best_summary_key(by_side)
        best_summary = by_side.get(best_side or "")
        if not side_summary or not best_summary or side == best_side:
            return None
        side_pf = float(side_summary.get("profitFactor") or 0)
        side_net = float(side_summary.get("netPnl") or 0)
        best_pf = float(best_summary.get("profitFactor") or 0)
        if side_net < 0 and side_pf < 1 and best_pf >= 1.5:
            return f"{side} side underperforming today; best observed side is {best_side} with PF {best_pf:.2f}"
        return None

    def _entry_correlation_gate(self, candidate: dict[str, Any], session_adj: dict[str, Any]) -> str | None:
        side = str(candidate.get("side") or "").upper()
        bucket = str(session_adj.get("sessionBucket") or "UNKNOWN")
        runner_sig = (candidate.get("runnerSignal") or {})
        is_momentum_override = bool(runner_sig.get("momentumOverride"))
        # Momentum override: allow stacking across different symbols (NIFTY+SENSEX+BANKNIFTY all moving)
        # Hard cap at 5 total to prevent runaway stacking
        hard_cap = 5 if is_momentum_override else int(self.settings.paper_max_open_trades)
        if len(self.open_paper) >= hard_cap:
            return f"max open trades reached ({hard_cap})"
        # For momentum override: allow up to 3 same-side (all 3 symbols crashing/surging simultaneously)
        same_side_cap = 3 if is_momentum_override else int(self.settings.paper_max_open_same_side_trades)
        same_side_open = [trade for trade in self.open_paper.values() if str(trade.side or "").upper() == side]
        if same_side_cap > 0 and len(same_side_open) >= same_side_cap:
            return f"{side} side at max capacity ({same_side_cap})"
        now = datetime.now(timezone.utc)
        # Momentum override: 30s entry cooldown (vs 300s) — catch continuation of explosive move
        entry_cooldown = 30 if is_momentum_override else max(0, int(self.settings.paper_same_side_entry_cooldown_seconds))
        loss_cooldown = 60 if is_momentum_override else max(0, int(self.settings.paper_same_side_loss_cooldown_seconds))
        for trade in reversed(list(self.closed_paper)[-50:]):
            if str(trade.side or "").upper() != side:
                continue
            if self._trade_bucket(trade) != bucket:
                continue
            timestamp = None
            for raw in [trade.exited_at, trade.opened_at]:
                if raw:
                    try:
                        timestamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                        break
                    except ValueError:
                        continue
            if not timestamp:
                continue
            age_seconds = (now - timestamp.astimezone(timezone.utc)).total_seconds()
            if trade.pnl < 0 and age_seconds <= loss_cooldown:
                return f"{side} side loss cooldown active in {bucket} after {trade.symbol} loss ({int((loss_cooldown - age_seconds) // 60)}m left)"
            if age_seconds <= entry_cooldown:
                return f"{side} side same-window entry cooldown active after {trade.symbol} trade ({int((entry_cooldown - age_seconds) // 60)}m left)"
        return None

    def _breadth_confirmation(self, side: str, symbol: str = "") -> dict[str, Any]:
        snapshot = self._latest_market_snapshot or {}
        breadth = snapshot.get("breadth") or {}
        count = int(snapshot.get("count") or 0)
        sector_breadth = snapshot.get("sectorBreadth") or {}
        stock_score = breadth.get("stockScore")
        # For BANKNIFTY: use banking-sector breadth score instead of overall Nifty 50 breadth
        sym_upper = symbol.upper()
        if sym_upper == "BANKNIFTY" and sector_breadth.get("banking", {}).get("count", 0) >= 4:
            banking = sector_breadth["banking"]
            raw_score = float(banking.get("score") or 50.0)
            bias = str(banking.get("bias") or "NEUTRAL")
        else:
            raw_score = float(stock_score if stock_score is not None else breadth.get("score") or 50.0)
            bias = str(breadth.get("bias") or "NEUTRAL")
        score = raw_score
        enabled = bool(self.settings.paper_breadth_filter_enabled)
        enough_data = count >= int(self.settings.paper_breadth_min_count)
        side = str(side or "").upper()
        aligned = True
        reason = "market breadth neutral or unavailable"
        sector_breadth = snapshot.get("sectorBreadth") or {}
        if enabled and enough_data:
            bullish = score >= float(self.settings.paper_breadth_bullish_threshold)
            bearish = score <= float(self.settings.paper_breadth_bearish_threshold)
            if side == "CALL":
                aligned = bullish
                reason = "CALL aligned with bullish breadth" if aligned else f"CALL rejected: breadth not bullish ({score:.1f})"
            elif side == "PUT":
                aligned = bearish
                reason = "PUT aligned with bearish breadth" if aligned else f"PUT rejected: breadth not bearish ({score:.1f})"
        stock_count = int(snapshot.get("stockCount") or 0)
        return {
            "available": bool(snapshot.get("available")) and enough_data,
            "enabled": enabled,
            "aligned": aligned,
            "score": round(score, 2),
            "stockScore": round(raw_score, 2) if stock_score is not None else None,
            "bias": bias,
            "count": count,
            "stockCount": stock_count,
            "reason": reason,
            "source": snapshot.get("source") or "market_snapshot_cache",
            "sectorBreadth": sector_breadth,
        }

    def _ai_trade_quality_prediction(
        self,
        candidate: dict[str, Any],
        *,
        premium: float,
        required_move: float,
        session_adj: dict[str, Any],
        market_phase: str | None,
    ) -> dict[str, Any]:
        runner = candidate.get("runnerSignal") or {}
        metrics = runner.get("metrics") or {}
        side = str(candidate.get("side") or "")
        chart_bias = str(candidate.get("chartBias") or "")
        tqs = float(candidate.get("tqs") or runner.get("score") or 0)
        runner_score = float(runner.get("score") or 0)
        spread_quality = float(metrics.get("spreadQuality") or 0)
        breakout = float(metrics.get("breakoutVelocity") or 0)
        delta_velocity = abs(float(metrics.get("deltaVelocity") or 0))
        premium_velocity = float(metrics.get("premiumVelocity") or 0)
        volume_state = runner.get("volumeState") or {}
        effective_volume = float(candidate.get("effectiveVolume") or volume_state.get("effectiveVolume") or metrics.get("volume") or 0)
        session_bucket = str(session_adj.get("sessionBucket") or "UNKNOWN")
        is_runner = candidate.get("strategyType") == "EXPLOSIVE_RUNNER"
        breadth = self._breadth_confirmation(side)

        score = 45.0
        score += max(0.0, min(18.0, (tqs - 70) * 0.6))
        score += max(0.0, min(20.0, (runner_score - 80) * 0.8))
        score += 14.0 if runner.get("eliteRunner") else 0.0
        score += 8.0 if runner.get("momentumAligned") else -8.0 if is_runner else 0.0
        score += 8.0 if spread_quality >= 80 else -10.0 if spread_quality and spread_quality < 70 else 0.0
        score += 7.0 if breakout >= 70 else 0.0
        score += 7.0 if delta_velocity >= 55 else 0.0
        score += 5.0 if premium_velocity >= float(self.settings.explosive_runner_momentum_premium_velocity_pct) else 0.0
        score += 5.0 if effective_volume > 0 else -8.0
        if not self.settings.paper_unified_scalp_session_profile:
            score += 6.0 if session_bucket == "CLOSING_MOMENTUM" else -7.0 if session_bucket == "MIDDAY_CHOP" else 0.0
        if chart_bias in {"CALL", "PUT"} and side in {"CALL", "PUT"}:
            score += 8.0 if chart_bias == side else -25.0
        elif chart_bias == "WAIT":
            score -= 15.0
        if candidate.get("chopBlocked") and not is_runner:
            score -= 18.0
        if breadth.get("available"):
            score += 10.0 if breadth.get("aligned") else -18.0

        news = self._latest_news_state or {}
        news_impact = news.get("impact") or {}
        if news_impact.get("avoidFreshTrades") and not runner.get("momentumOverride"):
            score -= 40.0  # penalise regular trades; momentum override bypasses
        elif news_impact.get("raiseTqs") and not runner.get("momentumOverride"):
            score -= 7.0
        if news_impact.get("allowRunnerBias") and is_runner:
            bias_side = str(news_impact.get("biasSide") or "")
            if not bias_side or bias_side == side:
                score += 12.0
        if runner.get("momentumOverride"):
            score = max(score, 70.0)  # momentum override always passes AI predictor
        if self.settings.paper_max_catch_mode and self._is_catchable_runner(candidate, runner):
            score = max(score, 78.0)

        expected_move = max(required_move * 2.0, float((runner.get("maxPointsPlan") or {}).get("targetPremiumPct") or 0) * premium / 100)
        if is_runner:
            expected_move = max(expected_move, float(self.settings.paper_runner_target_premium_pct) * premium / 100)
        expected_drawdown = max(required_move, float(self.settings.paper_stop_points))
        risk_reward = expected_move / expected_drawdown if expected_drawdown > 0 else 0.0
        win_probability = max(5.0, min(90.0, score))
        confidence = max(5.0, min(95.0, (win_probability * 0.65) + min(100.0, risk_reward * 25) * 0.35))
        passed = (
            win_probability >= float(self.settings.paper_ai_min_win_probability_pct)
            and risk_reward >= float(self.settings.paper_ai_min_risk_reward)
            and confidence >= float(self.settings.paper_ai_min_confidence_pct)
        )
        return {
            "passed": passed,
            "winProbabilityPct": round(win_probability, 2),
            "expectedMovePoints": round(expected_move, 2),
            "expectedDrawdownPoints": round(expected_drawdown, 2),
            "riskReward": round(risk_reward, 2),
            "confidencePct": round(confidence, 2),
            "minimums": {
                "winProbabilityPct": self.settings.paper_ai_min_win_probability_pct,
                "riskReward": self.settings.paper_ai_min_risk_reward,
                "confidencePct": self.settings.paper_ai_min_confidence_pct,
            },
            "sessionBucket": session_bucket,
            "breadth": breadth,
            "model": "heuristic_trade_quality_predictor_v1",
        }

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
        strong_runner = self._runner_entry_bypass(candidate, runner)
        if premium <= 0:
            reasons.append("missing premium")
        if candidate.get("chopBlocked") and not tradeable_runner and not strong_runner:
            reasons.append("chop filter blocked")
        correlation_gate = self._entry_correlation_gate(candidate, session_adj)
        if correlation_gate:
            reasons.append(correlation_gate)
        side_gate = self._side_performance_gate(candidate)
        if side_gate:
            reasons.append(side_gate)
        plan = self._daily_improvement_plan()
        blocked_sides = plan.get("gates", {}).get("blockedSides") or []
        if str(candidate.get("side") or "") in blocked_sides:
            reasons.append(f"daily calibration blocked {candidate.get('side')} side (underperforming)")
        symbol = str(candidate.get("symbol") or "")
        breadth = self._breadth_confirmation(side, symbol=symbol)
        momentum_explosion = self._is_momentum_explosion(candidate, runner)
        runner_momentum_override = bool(runner.get("momentumOverride")) or momentum_explosion or strong_runner
        if self._is_explosion_chase(candidate, runner) and runner.get("momentumOverride"):
            reasons.append(
                f"explosion chase blocked: premium ₹{premium:.0f} above max entry ₹{self.settings.paper_momentum_max_entry_premium:.0f}"
            )
        if runner_momentum_override:
            pass  # momentum override: premium velocity is the signal, not breadth
        elif breadth.get("available") and not breadth.get("aligned") and not tradeable_runner:
            reasons.append(str(breadth.get("reason") or "market breadth does not confirm trade side"))
        elif breadth.get("available") and not breadth.get("aligned") and tradeable_runner:
            if self._intraday_loss_guard().get("active"):
                reasons.append(str(breadth.get("reason") or "market breadth does not confirm trade side"))
            # else: elite runners bypass stale breadth; runner tape score is more current
        high_conf_ok, high_conf_reason = self._passes_high_confidence_gate(candidate, runner)
        if not high_conf_ok and not momentum_explosion and not strong_runner:
            reasons.append(high_conf_reason)
        ai_prediction = self._ai_trade_quality_prediction(candidate, premium=premium, required_move=required_move, session_adj=session_adj, market_phase=market_phase)
        if not ai_prediction["passed"] and not momentum_explosion and not runner.get("momentumOverride") and not strong_runner:
            reasons.append(
                "AI quality predictor rejected: "
                f"win {ai_prediction['winProbabilityPct']}%, RR {ai_prediction['riskReward']}, confidence {ai_prediction['confidencePct']}%"
            )
        if tradeable_runner:
            volume_state = runner.get("volumeState") or {}
            if candidate.get("effectiveVolume", 0) <= 0 and not volume_state.get("volumeAvailable"):
                reasons.append("missing effective volume")
        else:
            min_entry_tqs = int(session_adj.get("minEntryTqs") or max(int(self.settings.nifty_opt_min_tqs), int(self.settings.sensex_opt_min_tqs)))
            if candidate.get("strategyType") != "EXPLOSIVE_RUNNER" and self.settings.paper_dual_capital_enabled:
                min_entry_tqs = min(min_entry_tqs, int(self.settings.paper_scalping_min_entry_tqs))
            if candidate.get("tqs", 0) < min_entry_tqs:
                reasons.append(f"TQS below session threshold ({min_entry_tqs})")
            if candidate.get("effectiveVolume", 0) <= 0:
                reasons.append("missing effective volume")
            if not momentum_runner and not momentum_explosion and not strong_runner:
                if chart_bias in {"CALL", "PUT"} and side in {"CALL", "PUT"} and side != chart_bias:
                    reasons.append(f"chart trend conflict: {chart_bias} bias vs {side} trade")
                if chart_bias == "WAIT":
                    reasons.append("chart analysis says wait")
            min_runner_score = float(session_adj.get("minRunnerScore") or self.settings.explosive_runner_min_score)
            if self._elite_runner_only_mode():
                min_runner_score = max(min_runner_score, float(self.settings.paper_high_confidence_min_runner_score))
            elif momentum_runner:
                min_runner_score = min(min_runner_score, float(self.settings.explosive_runner_momentum_min_score))
            if candidate.get("strategyType") == "EXPLOSIVE_RUNNER" and runner_score < min_runner_score:
                reasons.append(f"runner score below session threshold ({min_runner_score:g})")
            if required_move > self.settings.min_required_move_points * 1.4:
                reasons.append("spread/slippage cost too high for 5-point scalp")
        return {
            "blocked": bool(reasons),
            "paperEligible": not bool(reasons),
            "reason": ", ".join(reasons) if reasons else "quality accepted",
            "spreadCost": round(spread_cost, 2),
            "slippageEstimate": round(slippage, 2),
            "chargesEstimate": round(charges, 2),
            "chargesPerUnit": round(charges_per_unit, 4),
            "minimumRequiredMove": round(required_move, 2),
            "aiPrediction": ai_prediction,
        }

    def _quick_profit_sizing_active(self, candidate: dict[str, Any], pool: str, strategy_type: str) -> bool:
        if not self.settings.paper_quick_profit_enabled:
            return False
        if pool == "scalping" or strategy_type == "SCALP":
            return True
        return bool(self.settings.paper_quick_profit_size_runners and strategy_type == "EXPLOSIVE_RUNNER")

    def _sizing_per_unit_risk(self, risk_plan: dict[str, float], quality: dict[str, Any], *, quick_profit: bool) -> float:
        charges_per_unit = float(quality.get("chargesPerUnit") or 0)
        spread_slip = float(quality.get("spreadCost") or 0) + float(quality.get("slippageEstimate") or 0)
        if quick_profit:
            unit_risk = float(self.settings.paper_quick_profit_risk_unit_points)
        else:
            unit_risk = float(risk_plan["stopPoints"])
        return max(0.05, unit_risk + spread_slip + charges_per_unit)

    def _open_paper_trade(
        self,
        candidate: dict[str, Any],
        quality: dict[str, Any],
        available_capital: float | None = None,
        trading_capital: float | None = None,
        session_adj: dict[str, Any] | None = None,
        market_phase: str | None = None,
        capital_pool: str | None = None,
    ) -> PaperTrade | None:
        session_adj = session_adj or {}
        tradeable_runner = self._is_tradeable_explosive_runner(candidate, market_phase)
        runner_sig = candidate.get("runnerSignal") or {}
        momentum_override = bool(runner_sig.get("momentumOverride") and str(runner_sig.get("confidence") or "").upper() == "HIGH")
        entry_bypass = self._runner_entry_bypass(candidate, runner_sig)
        momentum_explosion = self._is_momentum_explosion(candidate, runner_sig)
        strategy_type = str(candidate.get("strategyType") or "SCALP")
        pool = capital_pool or self._capital_pool_for(strategy_type)
        entry_tqs = int(candidate.get("tqs") or 0)
        if entry_bypass or momentum_explosion:
            min_entry_tqs = int(self.settings.paper_momentum_min_entry_tqs)
        elif not self._is_explosive_strategy(strategy_type) and self.settings.paper_dual_capital_enabled:
            min_entry_tqs = int(self.settings.paper_scalping_min_entry_tqs)
        else:
            min_entry_tqs = int(self.settings.paper_min_entry_tqs or self.settings.paper_high_confidence_min_tqs)
        if entry_tqs < min_entry_tqs and not momentum_override and not entry_bypass:
            return None
        if self._is_explosion_chase(candidate, runner_sig) and not self.settings.paper_max_catch_mode:
            return None
        if quality.get("blocked") and not entry_bypass and not momentum_explosion and not momentum_override:
            return None
        trade_id = str(candidate.get("id") or uuid4())
        if trade_id in self.open_paper:
            return None
        if self._recent_signal_active(trade_id):
            return None
        instrument_key = str(candidate.get("instrumentKey") or "")
        instrument_signal_id = f"instrument:{instrument_key}" if instrument_key else ""
        if instrument_key and any(str(trade.instrument_key or "") == instrument_key for trade in self.open_paper.values()):
            return None
        if instrument_signal_id and self._recent_signal_active(instrument_signal_id):
            return None
        premium = float(candidate.get("lastPremium") or 0)
        lot_size = max(1, int(candidate.get("lotSize") or 1))
        desired_quantity = int(candidate.get("quantityEstimate") or lot_size)
        risk_plan = self._paper_risk_plan(candidate, quality, premium, session_adj)
        quick_sizing = self._quick_profit_sizing_active(candidate, pool, strategy_type)
        if available_capital is not None and premium > 0:
            capital = max(0.0, float(trading_capital or 0))
            runner_sig_inner = candidate.get("runnerSignal") or {}
            alloc_boost = 1.5 if runner_sig_inner.get("momentumOverride") else 1.0
            if quick_sizing:
                alloc_boost *= float(self.settings.paper_quick_profit_allocation_boost)
            allocation_pct = self._allocation_pct_for_pool(pool, session_adj) * alloc_boost
            if tradeable_runner and not quick_sizing:
                allocation_pct = min(allocation_pct, float(self.settings.paper_runner_max_allocation_pct))
            elif tradeable_runner and quick_sizing:
                allocation_pct = min(allocation_pct, float(self.settings.paper_runner_max_allocation_pct) * float(self.settings.paper_quick_profit_allocation_boost))
            target_allocation = capital * max(0.0, allocation_pct) / 100 if capital > 0 else max(0.0, available_capital)
            min_allocation_pct = float(self.settings.paper_min_trade_allocation_pct)
            if tradeable_runner:
                min_allocation_pct = min(min_allocation_pct, float(self.settings.paper_min_trade_allocation_pct))
            min_allocation = capital * max(0.0, min_allocation_pct) / 100 if capital > 0 else 0.0
            usable_capital = min(max(0.0, available_capital), target_allocation)
            affordable_lots = int(usable_capital // (premium * lot_size))
            max_trade_loss_amount = float(self.settings.paper_quick_profit_risk_budget_amount if quick_sizing else (self.settings.paper_max_trade_loss_amount or 0))
            max_trade_loss_pct = float(self.settings.paper_max_trade_loss_pct or 0)
            pct_risk_amount = capital * max_trade_loss_pct / 100 if capital > 0 and max_trade_loss_pct > 0 else 0
            risk_budget = min([value for value in [max_trade_loss_amount, pct_risk_amount] if value > 0], default=0)
            per_unit_risk = self._sizing_per_unit_risk(risk_plan, quality, quick_profit=quick_sizing)
            risk_lots = int(risk_budget // (per_unit_risk * lot_size)) if risk_budget > 0 else affordable_lots
            max_lots_cap = int(self.settings.paper_quick_profit_max_lots) if quick_sizing else affordable_lots
            affordable_lots = min(affordable_lots, max_lots_cap)
            risk_lots = min(risk_lots, max_lots_cap)
            if quick_sizing:
                min_lots = int(self.settings.paper_quick_profit_min_lots)
                target_lots = int(self.settings.paper_quick_profit_target_lots)
                allowed_lots = min(affordable_lots, risk_lots, max_lots_cap)
                final_lots = min(allowed_lots, max(min_lots, min(target_lots, allowed_lots)))
                quantity = final_lots * lot_size
            else:
                quantity = min(desired_quantity, affordable_lots * lot_size, risk_lots * lot_size)
            if risk_budget <= 0 and quantity * premium < min_allocation:
                return None
        else:
            quantity = desired_quantity
        if quantity < lot_size:
            return None
        min_viable_lots = 3
        if quick_sizing:
            min_viable_lots = int(self.settings.paper_quick_profit_min_lots)
        cheap_threshold = float(self.settings.paper_cheap_premium_lot_threshold)
        if premium <= cheap_threshold and (entry_bypass or momentum_explosion or momentum_override):
            min_viable_lots = max(1, int(self.settings.paper_runner_min_lots_cheap_premium))
        if quantity < lot_size * min_viable_lots:
            return None
        charges = self._charges_estimate(premium, premium, max(1, quantity))
        trade = PaperTrade(
            id=trade_id,
            symbol=str(candidate.get("symbol")),
            side=str(candidate.get("side")),
            strike=int(candidate.get("strike") or 0),
            expiry=str(candidate.get("expiry")),
            instrument_key=instrument_key or candidate.get("instrumentKey"),
            entry_price=premium,
            quantity=max(1, quantity),
            entry_tqs=int(candidate.get("tqs") or 0),
            spread_cost=float(quality["spreadCost"]),
            slippage_estimate=float(quality["slippageEstimate"]),
            charges_estimate=float(quality.get("chargesEstimate") or charges),
            opened_at=datetime.now(timezone.utc).isoformat(),
            mode=str(candidate.get("mode")),
            strategy_type=strategy_type,
            capital_pool=pool,
            exit_mode="SCALP_LOCK" if (
                strategy_type == "EXPLOSIVE_RUNNER" and self.settings.paper_runner_start_scalp_lock
            ) or (
                strategy_type == "SCALP" and self.settings.paper_unified_scalp_session_profile
            ) else "AUTO",
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
            style = str(profile.get("executionStyle") or "GENERIC")
            is_runner = trade.strategy_type == "EXPLOSIVE_RUNNER"
            exit_mode = trade.exit_mode or "AUTO"
            if is_runner:
                if exit_mode == "AUTO":
                    computed = self._adaptive_exit_mode(trade, candidate, age)
                    if computed == "SCALP_LOCK":
                        trade.exit_mode = "SCALP_LOCK"
                        exit_mode = "SCALP_LOCK"
                        trade.lifecycle.append(
                            LifecycleEvent(
                                "MODIFIED",
                                datetime.now(timezone.utc).isoformat(),
                                "AI exit mode -> SCALP_LOCK (momentum fade; lock profit via scalp trail)",
                                {"exitMode": "SCALP_LOCK", "bestPrice": trade.best_price},
                            )
                        )
                    else:
                        exit_mode = "RUNNER"
                else:
                    exit_mode = trade.exit_mode
            scalp_lock = exit_mode == "SCALP_LOCK" or (not is_runner and style == "HIGH_WIN_SCALP") or (not is_runner and trade.strategy_type == "SCALP")
            scalp_exits = self._trade_uses_scalp_exits(trade, exit_mode=exit_mode, style=style, is_runner=is_runner)
            stop_points, max_hold_seconds, psych_exit_reason = self._psychology_exit_adjustments(
                stop_points,
                psychology,
                session_max_hold,
                scalp_trade=scalp_exits,
            )
            if is_runner and exit_mode != "SCALP_LOCK":
                target_points = max(target_points, self.settings.paper_target_points * 1.25)
                max_hold_seconds = max(max_hold_seconds, int(self.settings.paper_runner_max_hold_seconds))
                breakeven_shift = max(breakeven_shift, min(target_points * 0.35, float(self.settings.paper_breakeven_shift_points) + 2.0))
            elif scalp_lock or style == "HIGH_WIN_SCALP":
                target_points = min(target_points, max(self.settings.paper_target_points, 8.0))
                max_hold_seconds = min(max_hold_seconds, int(self.settings.max_paper_trade_seconds))
                breakeven_shift = min(breakeven_shift, max(4.0, target_points * 0.5))
            if not trade.breakeven_armed and trade.best_price >= trade.entry_price + breakeven_shift:
                trade.breakeven_armed = True
                trade.lifecycle.append(LifecycleEvent("MODIFIED", datetime.now(timezone.utc).isoformat(), "breakeven stop armed", {"breakevenAt": trade.entry_price, "bestPrice": trade.best_price}))
            if not trade.partial_exit_taken and trade.best_price >= trade.entry_price + partial_exit_at:
                trade.partial_exit_taken = True
                trade.lifecycle.append(LifecycleEvent("PARTIAL_FILL", datetime.now(timezone.utc).isoformat(), "partial exit threshold reached in paper model", {"partialExitAt": round(partial_exit_at, 2), "bestPrice": trade.best_price}))
            runner_min_hold = int(self.settings.paper_runner_min_hold_seconds)
            target_price = trade.entry_price + target_points
            best_gain = max(0.0, (trade.best_price or trade.entry_price) - trade.entry_price)
            unrealized = current - trade.entry_price
            scalp_trail = max(1.0, min(3.5, best_gain * 0.35)) if scalp_lock else max(2.0, target_points * 0.35)
            trail_pts = float(trade.trail_points or (target_points * 0.18 if is_runner and not scalp_lock else scalp_trail))
            trail_arm_at = max(2.0, min(partial_exit_at, target_points * 0.2)) if scalp_lock else max(partial_exit_at, target_points * 0.25)
            in_profitable_trail = trade.best_price >= trade.entry_price + trail_arm_at
            reason = self._quick_profit_exit_reason(trade, current, age)
            if not reason and scalp_lock and in_profitable_trail and current <= trade.best_price - trail_pts:
                reason = "AI adaptive scalp profit lock"
            elif not reason and in_profitable_trail and current <= trade.best_price - trail_pts:
                reason = "elite runner trailing max-points lock" if is_runner else "trailing profit lock"
            elif not reason and is_runner and trade.best_price >= target_price and trade.best_price > target_price + trail_pts * 0.25:
                if current <= trade.best_price - trail_pts:
                    reason = "elite runner trailing max-points lock"
            elif not reason and scalp_lock and current >= trade.entry_price + max(3.0, target_points * 0.5):
                reason = "AI adaptive scalp target hit"
            elif not reason and current >= target_price:
                reason = "elite runner target profit hit" if is_runner and not scalp_lock else "target profit hit"
            elif not reason and is_runner and not scalp_lock and age >= max_hold_seconds:
                if current >= trade.entry_price + max(float(self.settings.paper_micro_scalp_min_gain), 2.0):
                    reason = "runner max hold profit lock"
                elif current >= trade.entry_price + target_points * 0.35:
                    reason = "elite runner max hold profit lock"
                else:
                    reason = "runner time stop"
            elif not reason and is_runner and not scalp_lock and age >= int(self.settings.paper_runner_min_hold_seconds) and current <= trade.entry_price - max(1.0, stop_points * 0.5):
                if unrealized < max(1.5, float(self.settings.paper_micro_scalp_min_gain) * 0.5) and best_gain < float(self.settings.paper_micro_scalp_min_gain):
                    reason = "runner early decay stop"
            elif not reason and scalp_lock and age >= min(max_hold_seconds, 120) and current > trade.entry_price + 1.0:
                reason = "AI adaptive scalp time lock"
            elif not reason and trade.breakeven_armed and current <= trade.entry_price + 1.5 and (not is_runner or age >= runner_min_hold):
                reason = "breakeven protection after profit move"
            elif not reason and current <= trade.entry_price - stop_points:
                reason = "momentum decay or delta reversal stop" if scalp_exits else (psych_exit_reason or "momentum decay or delta reversal stop")
            elif not reason and age >= max_hold_seconds:
                if unrealized >= max(2.0, float(self.settings.paper_micro_scalp_min_gain)):
                    reason = "time stop profit lock"
                elif scalp_exits:
                    reason = "scalp time stop"
                else:
                    reason = "psychology shortened time stop" if max_hold_seconds < self.settings.max_paper_trade_seconds else "time stop"
            elif not reason and self._should_chop_exit(trade, candidate, age):
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
                if trade.instrument_key:
                    AutoTraderEngine._shared_recent_signal_times[f"instrument:{trade.instrument_key}"] = monotonic()
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

    def _is_explosive_strategy(self, strategy_type: str | None) -> bool:
        return str(strategy_type or "").upper() == "EXPLOSIVE_RUNNER"

    def _capital_pool_for(self, strategy_type: str | None) -> str:
        return "explosive" if self._is_explosive_strategy(strategy_type) else "scalping"

    def _pool_capital(self, pool: str, trading_capital: float | None = None) -> float:
        if not self.settings.paper_dual_capital_enabled:
            return float(trading_capital or self.settings.trading_capital_default or 0)
        if pool == "explosive":
            return float(self.settings.paper_explosive_capital or 0)
        return float(self.settings.paper_scalping_capital or 0)

    def _capital_pools_summary(self, trading_capital: float | None = None) -> dict[str, Any]:
        total = float(trading_capital or self.settings.trading_capital_default or 0)
        if not self.settings.paper_dual_capital_enabled:
            return {
                "enabled": False,
                "totalCapital": total,
                "scalping": {"capital": total, "used": self._used_capital_in_pool("scalping"), "openTrades": self._open_trades_in_pool("scalping")},
                "explosive": {"capital": total, "used": self._used_capital_in_pool("explosive"), "openTrades": self._open_trades_in_pool("explosive")},
            }
        scalp_cap = float(self.settings.paper_scalping_capital or 0)
        explosive_cap = float(self.settings.paper_explosive_capital or 0)
        return {
            "enabled": True,
            "totalCapital": round(scalp_cap + explosive_cap, 2),
            "scalping": {
                "capital": scalp_cap,
                "used": round(self._used_capital_in_pool("scalping"), 2),
                "available": round(max(0.0, scalp_cap - self._used_capital_in_pool("scalping")), 2),
                "openTrades": self._open_trades_in_pool("scalping"),
                "maxOpenTrades": int(self.settings.paper_scalping_max_open_trades),
                "allocationPct": float(self.settings.paper_scalping_allocation_pct),
            },
            "explosive": {
                "capital": explosive_cap,
                "used": round(self._used_capital_in_pool("explosive"), 2),
                "available": round(max(0.0, explosive_cap - self._used_capital_in_pool("explosive")), 2),
                "openTrades": self._open_trades_in_pool("explosive"),
                "maxOpenTrades": int(self.settings.paper_explosive_max_open_trades),
                "allocationPct": float(self.settings.paper_explosive_allocation_pct),
            },
        }

    def _used_capital_in_pool(self, pool: str) -> float:
        return sum(
            max(0.0, trade.entry_price) * max(0, trade.quantity)
            for trade in self.open_paper.values()
            if self._capital_pool_for(trade.strategy_type) == pool
        )

    def _open_trades_in_pool(self, pool: str) -> int:
        return sum(1 for trade in self.open_paper.values() if self._capital_pool_for(trade.strategy_type) == pool)

    def _max_open_for_pool(self, pool: str) -> int:
        if not self.settings.paper_dual_capital_enabled:
            return int(self.settings.paper_max_open_trades)
        if pool == "explosive":
            return int(self.settings.paper_explosive_max_open_trades)
        return int(self.settings.paper_scalping_max_open_trades)

    def _allocation_pct_for_pool(self, pool: str, session_adj: dict[str, Any] | None = None) -> float:
        session_adj = session_adj or {}
        multiplier = float(session_adj.get("allocationPctMultiplier") or 1.0)
        if not self.settings.paper_dual_capital_enabled:
            return float(self.settings.paper_trade_allocation_pct) * multiplier
        base = float(self.settings.paper_explosive_allocation_pct if pool == "explosive" else self.settings.paper_scalping_allocation_pct)
        return base * multiplier

    def _available_capital(self, trading_capital: float) -> float:
        if self.settings.paper_dual_capital_enabled:
            scalp = max(0.0, self._pool_capital("scalping", trading_capital) - self._used_capital_in_pool("scalping"))
            explosive = max(0.0, self._pool_capital("explosive", trading_capital) - self._used_capital_in_pool("explosive"))
            return scalp + explosive
        used = sum(max(0.0, trade.entry_price) * max(0, trade.quantity) for trade in self.open_paper.values())
        return max(0.0, float(trading_capital or 0) - used)

    def _available_capital_for_strategy(self, strategy_type: str, trading_capital: float) -> float:
        pool = self._capital_pool_for(strategy_type)
        pool_cap = self._pool_capital(pool, trading_capital)
        return max(0.0, pool_cap - self._used_capital_in_pool(pool))

    def _adaptive_exit_mode(self, trade: PaperTrade, candidate: dict[str, Any], age: float) -> str:
        if not self.settings.paper_ai_adaptive_exit_enabled or not self._is_explosive_strategy(trade.strategy_type):
            return "RUNNER" if self._is_explosive_strategy(trade.strategy_type) else "SCALP"
        runner = (candidate or {}).get("runnerSignal") or {}
        metrics = runner.get("metrics") or {}
        premium_velocity = float(runner.get("premiumVelocityPct") or metrics.get("premiumVelocity") or 0)
        runner_score = float(runner.get("score") or 0)
        best_gain = max(0.0, (trade.best_price or trade.entry_price) - trade.entry_price)
        min_gain = float(self.settings.paper_adaptive_scalp_lock_min_gain_points)
        fade_vel = float(self.settings.paper_adaptive_momentum_fade_velocity_pct)
        had_profit = best_gain >= max(min_gain, trade.target_points * 0.12)
        momentum_fading = premium_velocity < fade_vel
        score_decay = runner_score < 55 and had_profit
        current = float(candidate.get("lastPremium") or candidate.get("premium") or 0)
        unrealized = current - trade.entry_price
        giveback = had_profit and best_gain > 0 and unrealized < best_gain * (1.0 - float(self.settings.paper_micro_scalp_giveback_pct))
        quick_age = age >= float(self.settings.paper_runner_quick_lock_seconds)
        if had_profit and (momentum_fading or score_decay or giveback or quick_age):
            return "SCALP_LOCK"
        return "RUNNER"

    def _quick_profit_exit_reason(self, trade: PaperTrade, current: float, age: float) -> str | None:
        """Book small profits quickly instead of waiting for wide runner targets."""
        if not self.settings.paper_quick_profit_enabled:
            return None
        entry = float(trade.entry_price)
        best = float(trade.best_price or entry)
        best_gain = max(0.0, best - entry)
        unrealized = current - entry
        min_move = float(self.settings.min_required_move_points) + 0.5
        micro_min = max(float(self.settings.paper_micro_scalp_min_gain), min_move)
        micro_trail = max(1.0, float(self.settings.paper_micro_scalp_trail_points))
        quick_target = max(micro_min, float(self.settings.paper_quick_profit_points))
        giveback_floor = 1.0 - float(self.settings.paper_micro_scalp_giveback_pct)
        if current >= entry + quick_target:
            return "quick profit target hit"
        if best_gain >= micro_min:
            if current <= best - micro_trail:
                return "micro scalp profit lock"
            if unrealized >= min_move and unrealized < best_gain * giveback_floor:
                return "micro scalp giveback lock"
        if age >= float(self.settings.paper_runner_quick_lock_seconds) and unrealized >= min_move:
            if current <= best - micro_trail or unrealized < best_gain * giveback_floor:
                return "time-based quick profit lock"
        return None

    def _paper_risk_halt(self, trading_capital: float | None = None, session_adj: dict[str, Any] | None = None) -> dict[str, Any]:
        session_adj = session_adj or {}
        capital = float(trading_capital or self.settings.trading_capital_default or 0)
        session_report = self._session_report()
        day_aggregate = self._day_aggregate_from_trades()
        session_net = float(session_report.get("netPnl") or 0)
        day_net = float(day_aggregate.get("netPnl") or 0)
        consecutive_losses = int(session_report.get("consecutiveLosses") or 0)
        day_loss_pct = abs(day_net) / capital * 100 if capital > 0 and day_net < 0 else 0.0
        session_loss_pct = abs(session_net) / capital * 100 if capital > 0 and session_net < 0 else 0.0
        day_loss_amount = abs(day_net) if day_net < 0 else 0.0
        session_loss_amount = abs(session_net) if session_net < 0 else 0.0
        reasons = []
        max_loss_amount = float(self.settings.paper_max_daily_loss_amount or 0)
        max_loss_pct = float(self.settings.paper_max_daily_loss_pct)
        rotation_enabled = bool(self.settings.paper_session_rotation_enabled)
        active_loss_amount = session_loss_amount if rotation_enabled else day_loss_amount
        active_loss_pct = session_loss_pct if rotation_enabled else day_loss_pct
        if max_loss_amount > 0 and day_loss_amount >= max_loss_amount:
            reasons.append(f"paper daily loss ₹{day_loss_amount:,.0f} >= ₹{max_loss_amount:,.0f}")
        elif day_loss_pct >= max_loss_pct:
            reasons.append(f"paper daily loss {day_loss_pct:.2f}% >= {max_loss_pct:.2f}%")
        if rotation_enabled:
            if max_loss_amount > 0 and session_loss_amount >= max_loss_amount:
                reasons.append(f"paper session loss ₹{session_loss_amount:,.0f} >= ₹{max_loss_amount:,.0f}")
            elif session_loss_pct >= max_loss_pct:
                reasons.append(f"paper session loss {session_loss_pct:.2f}% >= {max_loss_pct:.2f}%")
        if not rotation_enabled and consecutive_losses >= int(self.settings.paper_max_consecutive_losses):
            reasons.append(f"{consecutive_losses} consecutive paper losses")
        daily_target = self._daily_profit_target()
        daily_profit_target_amount = float(daily_target.get("targetAmount") or self.settings.paper_daily_profit_target_amount or 0)
        if daily_profit_target_amount > 0 and day_net >= daily_profit_target_amount:
            reasons.append(f"paper daily profit target INR {daily_profit_target_amount:,.0f} reached")
        profit_target_pct = float(session_adj.get("sessionProfitStopPct") or self.settings.paper_daily_profit_stop_pct)
        session_profit_pct = float(session_report.get("profitPct") or 0)
        if not rotation_enabled and session_profit_pct >= profit_target_pct:
            reasons.append(f"session profit {session_profit_pct:.2f}% >= {profit_target_pct:.2f}%")
        return {
            "blocked": bool(reasons),
            "reason": "; ".join(reasons) if reasons else None,
            "netPnl": round(session_net, 2),
            "dayNetPnl": round(day_net, 2),
            "lossPct": round(active_loss_pct, 2),
            "dayLossPct": round(day_loss_pct, 2),
            "sessionLossPct": round(session_loss_pct, 2),
            "lossAmount": round(active_loss_amount, 2),
            "dayLossAmount": round(day_loss_amount, 2),
            "sessionLossAmount": round(session_loss_amount, 2),
            "consecutiveLosses": consecutive_losses,
            "sessionProfitPct": round(session_profit_pct, 2),
            "profitTargetPct": profit_target_pct,
            "dailyProfitTargetAmount": daily_profit_target_amount,
            "dailyProfitTargetPct": daily_target.get("targetPct"),
            "dayQuality": daily_target.get("quality"),
            "maxDailyLossPct": max_loss_pct,
            "maxDailyLossAmount": max_loss_amount,
            "maxConsecutiveLosses": self.settings.paper_max_consecutive_losses,
            "sessionRotationEnabled": rotation_enabled,
            "riskScope": "session" if rotation_enabled else "day",
        }

    def _trade_uses_scalp_exits(
        self,
        trade: PaperTrade,
        *,
        exit_mode: str | None = None,
        style: str = "",
        is_runner: bool | None = None,
    ) -> bool:
        """Scalp/quick-profit exit path — psychology must not tighten stops or shorten time."""
        if self.settings.paper_unified_scalp_session_profile:
            return True
        is_runner_flag = trade.strategy_type == "EXPLOSIVE_RUNNER" if is_runner is None else is_runner
        mode = str(exit_mode or trade.exit_mode or "AUTO")
        if mode == "SCALP_LOCK" or trade.strategy_type == "SCALP" or style == "HIGH_WIN_SCALP":
            return not self.settings.paper_psychology_affects_scalp_exits
        if is_runner_flag and self.settings.paper_runner_start_scalp_lock:
            return not self.settings.paper_psychology_affects_scalp_exits
        return False

    def _psychology_exit_adjustments(
        self,
        stop_points: float,
        psychology: dict[str, Any] | None = None,
        session_max_hold: int | None = None,
        *,
        scalp_trade: bool = False,
    ) -> tuple[float, int, str | None]:
        if scalp_trade:
            return round(float(stop_points), 2), int(session_max_hold or self.settings.max_paper_trade_seconds), None
        psychology = psychology or {}
        state = str(psychology.get("state") or "CALM_AND_SELECTIVE")
        permission = str(psychology.get("tradePermission") or "A_PLUS_ONLY")
        max_hold = int(session_max_hold or self.settings.max_paper_trade_seconds)
        adjusted_stop = float(stop_points)
        reason = None
        if permission == "BLOCK_NEW_TRADES" or state == "HALT_AND_REVIEW":
            adjusted_stop = min(adjusted_stop, max(1.5, stop_points * 0.60))
            max_hold = min(max_hold, 90)
            reason = "psychology halt defensive stop"
        elif permission == "WAIT" or state == "DEFENSIVE":
            adjusted_stop = min(adjusted_stop, max(2.0, stop_points * 0.75))
            max_hold = min(max_hold, 150)
            reason = "psychology defensive stop"
        elif state == "CAUTIOUS":
            adjusted_stop = min(adjusted_stop, max(2.5, stop_points * 0.85))
            max_hold = min(max_hold, int(self.settings.paper_runner_max_hold_seconds))
            reason = "psychology cautious stop"
        return round(adjusted_stop, 2), max_hold, reason

    def _ai_psychological_coach(
        self,
        *,
        state: str,
        permission: str,
        risk_halt: dict[str, Any],
        session_adj: dict[str, Any],
        emotional_risks: list[str],
        behavioral_findings: list[str],
        day_summary: dict[str, Any],
        recent_losses: list[PaperTrade],
        recent_wins: list[PaperTrade],
    ) -> dict[str, Any]:
        performance = self.performance_analysis()
        best = performance.get("bestObserved") or {}
        target = performance.get("target") or {}
        session_bucket = str(session_adj.get("sessionBucket") or "UNKNOWN")
        net_pnl = float(day_summary.get("netPnl") or 0)
        profit_factor = float(day_summary.get("profitFactor") or 0)
        win_rate = float(day_summary.get("winRate") or 0)
        risks = sorted(set(emotional_risks))

        if risk_halt.get("blocked"):
            mode = "HALT_COACH"
            urgency = "HIGH"
            next_action = "No new paper entries. Review losses and wait for the next trading day or manual reset."
            cooldown_minutes = 60
        elif permission == "WAIT":
            mode = "RESET_FOCUS"
            urgency = "MEDIUM"
            next_action = "Wait for a clean A+ setup; do not increase size to recover."
            cooldown_minutes = 20
        elif profit_factor < 1 and int(day_summary.get("paperTrades") or 0) >= 10:
            mode = "SELECTIVITY_COACH"
            urgency = "MEDIUM"
            next_action = "Trade only the best observed bucket/side until profit factor recovers above 1."
            cooldown_minutes = 15
        else:
            mode = "EXECUTION_COACH"
            urgency = "LOW"
            next_action = "Stay process-focused and take only checklist-passing setups."
            cooldown_minutes = 5

        best_bucket = best.get("bucket") or "not proven yet"
        best_symbol = best.get("symbol") or "not proven yet"
        best_side = best.get("side") or "not proven yet"
        diagnosis = [
            f"Current psychology state is {state}; trade permission is {permission}.",
            f"Today's paper PF is {profit_factor:.2f}, win rate is {win_rate:.2f}%, net PnL is INR {net_pnl:,.0f}.",
            f"Best observed paper edge today: {best_bucket} / {best_symbol} / {best_side}.",
        ]
        if risks:
            diagnosis.append(f"Detected behavioral risks: {', '.join(risks)}.")
        if target:
            diagnosis.append(
                f"Daily target is INR {float(target.get('dailyProfitAmount') or 0):,.0f}; remaining is INR {float(target.get('remainingToTarget') or 0):,.0f}."
            )

        intervention = [
            "Pause for 90 seconds before every new paper entry.",
            "Say the trade thesis out loud: direction, trigger, invalidation, and exit.",
            "If the setup is not in the best observed bucket/side, reduce aggression or skip.",
            "After any loss, wait for a fresh candle/snapshot confirmation before the next entry.",
        ]
        if recent_losses and not recent_wins:
            intervention.insert(0, "Loss streak detected: do not take the next signal unless it passes every checklist item.")
        if "overtrading" in risks:
            intervention.append("Cap the next session to one open instrument at a time.")

        return {
            "mode": mode,
            "urgency": urgency,
            "sessionBucket": session_bucket,
            "nextAction": next_action,
            "cooldownMinutes": cooldown_minutes,
            "diagnosis": diagnosis[:6],
            "interventionScript": intervention[:8],
            "preTradeChecklist": [
                "Is this in the best observed window/side, or is there a clear reason to override?",
                "Is chart bias aligned with option direction?",
                "Is runner score/TQS above the current time-window threshold?",
                "Is there no active daily loss/profit halt?",
                "Is this not a duplicate instrument already traded in cooldown?",
                "Can I accept the stop without revenge trading?",
            ],
            "breathingProtocol": "4-2-6 breathing for three cycles: inhale 4, hold 2, exhale 6 before pressing the trade.",
            "journalPrompt": "Why this trade now? What condition proves me wrong? Did I follow the system or chase emotion?",
            "antiRevengeRules": [
                "No size increase after a loss.",
                "No CALL trades when today's CALL bucket remains negative unless chart and tape both flip.",
                "No trade outside the recommended time-window profile after risk halt.",
            ],
            "positiveReinforcement": "Your job is not to trade more; it is to protect 5L capital until the highest-quality paper edge appears.",
            "profileGuidance": {
                "baseProfile": (performance.get("institutionalAggressionProfiles") or {}).get("recommendedBaseProfile"),
                "bestBucket": best_bucket,
                "bestSymbol": best_symbol,
                "bestSide": best_side,
            },
            "confidenceScore": max(0, min(100, int(100 - len(risks) * 15 - max(0, 1 - profit_factor) * 25))),
            "source": "paper_performance_psychology_rules_v1",
            "findingsUsed": behavioral_findings[:5],
        }

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
        loss_pct = float(risk_halt.get("sessionLossPct") if rotation_enabled else risk_halt.get("lossPct") or 0)
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
        if risk_halt.get("blocked"):
            discipline_score = min(discipline_score, 45)
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
        day_summary = self._summarize_trades(self._today_closed_trades())
        ai_coach = self._ai_psychological_coach(
            state=state,
            permission=permission,
            risk_halt=risk_halt,
            session_adj=session_adj,
            emotional_risks=emotional_risks,
            behavioral_findings=behavioral_findings,
            day_summary=day_summary,
            recent_losses=recent_losses,
            recent_wins=recent_wins,
        )

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
            "aiCoach": ai_coach,
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
        is_runner = candidate.get("strategyType") == "EXPLOSIVE_RUNNER"
        if is_runner:
            plan = runner.get("maxPointsPlan") or {}
            target_pct = float(plan.get("targetPremiumPct") or profile.get("targetPremiumPct") or self.settings.paper_runner_target_premium_pct)
            max_target_pct = float(self.settings.paper_runner_max_target_premium_pct)
            target = max(target, premium * min(max_target_pct, max(target_pct, self.settings.paper_runner_target_premium_pct)) / 100, self.settings.paper_target_points * 1.5)
            base_stop = max(base_stop, premium * float(plan.get("hardStopPct") or 10.0) / 100)
        premium_stop_cap_pct = 0.10 if runner_score >= 85 and breakout >= 65 and delta_velocity >= 45 else 0.08
        premium_capped_stop = max(costs_per_unit + 1.0, premium * premium_stop_cap_pct) if premium > 0 else base_stop
        stop = min(base_stop, premium_capped_stop) if premium > 0 and not is_runner else base_stop
        stop = max(costs_per_unit + 0.75, stop)
        if is_runner:
            retain_pct = max(10.0, min(50.0, float(self.settings.paper_runner_trail_retain_pct)))
            trail = max(2.0, target * (1.0 - retain_pct / 100.0))
        else:
            trail = max(1.5, target * 0.35)
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
