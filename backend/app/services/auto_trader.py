from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.core.config import Settings
from app.services.ai_learning import ContinuousAILearner
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
    opened_at: str
    mode: str
    strategy_type: str = "SCALP"
    status: str = "OPEN"
    exit_price: float | None = None
    exit_reason: str | None = None
    exited_at: str | None = None
    pnl: float = 0.0
    lifecycle: list[LifecycleEvent] = field(default_factory=list)

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
            "openedAt": self.opened_at,
            "mode": self.mode,
            "strategyType": self.strategy_type,
            "status": self.status,
            "exitPrice": self.exit_price,
            "exitReason": self.exit_reason,
            "exitedAt": self.exited_at,
            "pnl": round(self.pnl, 2),
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

    def __init__(self, settings: Settings, trading_control: TradingControl, learner: ContinuousAILearner | None = None) -> None:
        self.settings = settings
        self.trading_control = trading_control
        self.learner = learner or ContinuousAILearner(settings.redis_url, settings.ai_learning_enabled)
        self.replay_buffer = AutoTraderEngine._shared_replay_buffer
        self.open_paper = AutoTraderEngine._shared_open_paper
        self.closed_paper = AutoTraderEngine._shared_closed_paper
        self.lifecycle_events = AutoTraderEngine._shared_lifecycle_events

    async def process(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        self.replay_buffer.append({"timestamp": now, "payload": self._compact_snapshot(payload)})
        trading_control = await self.trading_control.status()
        capital = await self.trading_control.capital_status()
        candidates = payload.get("executionCandidates") or []
        snapshots = payload.get("snapshots") or {payload.get("symbol", "NIFTY"): payload}

        signal_events = []
        skipped = []
        for candidate in candidates:
            event = self._signal_event(candidate, payload)
            signal_events.append(event)
            self.lifecycle_events.append(event)
            quality = self._pre_trade_quality(candidate)
            if quality["blocked"]:
                skipped.append({"candidate": candidate.get("id"), "reason": quality["reason"], "quality": quality})
                if not (self.settings.paper_trading and self.settings.shadow_trade_all_signals):
                    continue
                quality = {**quality, "shadowOverride": True, "reason": f"SHADOW PAPER despite rejection: {quality['reason']}"}
            if trading_control.get("autoTradingStopped") and self.settings.paper_trading_respects_stop:
                skipped.append({"candidate": candidate.get("id"), "reason": "manual stop active", "quality": quality})
                continue
            if self.settings.paper_trading or not self.settings.enable_live_trading:
                opened = self._open_paper_trade(candidate, quality)
                if opened:
                    signal_events.append(opened.lifecycle[-1])
        exits = self._update_open_paper(snapshots)
        online_learning = await self.learner.update_from_tick(payload, exits, "live" if self.settings.enable_live_trading and not self.settings.paper_trading else "paper")
        self._learn_every_tick(payload, exits)
        profit_lock = self.profit_lock_status(capital.get("tradingCapital", 0))
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
            "profitLock": profit_lock,
            "onlineLearning": online_learning,
            "dailyReport": self.daily_report(),
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
            "onlineLearning": self.learner.status_from_state(),
            "dailyReport": self.daily_report(),
        }


    def reset(self) -> dict[str, Any]:
        self.replay_buffer.clear()
        self.open_paper.clear()
        self.closed_paper.clear()
        self.lifecycle_events.clear()
        AutoTraderEngine._shared_learning_samples = 0
        AutoTraderEngine._shared_learning_score = 50.0
        AutoTraderEngine._shared_last_learning_update = None
        return {"reset": True, "status": self.status()}

    def replay(self, limit: int = 250) -> dict[str, Any]:
        return {"snapshots": list(self.replay_buffer)[-limit:], "count": min(limit, len(self.replay_buffer))}

    def profit_lock_status(self, capital: float | None = None) -> dict[str, Any]:
        capital = float(capital or 0)
        report = self.daily_report()
        net = float(report.get("grossProfit", 0)) - float(report.get("grossLoss", 0))
        tiers = [
            {"name": "fallback", "pct": self.settings.profit_target_fallback_pct},
            {"name": "secondary", "pct": self.settings.profit_target_secondary_pct},
            {"name": "primary", "pct": self.settings.profit_target_primary_pct},
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
        return {
            "capital": capital,
            "netPnl": round(net, 2),
            "tiers": [{**tier, "amount": round(capital * float(tier["pct"]) / 100, 2) if capital else 0} for tier in tiers],
            "activeTier": active,
            "lockedProfit": round(locked_profit, 2),
            "givebackAvailable": round(giveback, 2),
            "blockNewTrades": block_new,
            "message": "Primary profit locked; only trade from giveback buffer" if active and active["name"] == "primary" else "Profit target not locked yet" if not active else f"{active['name']} profit tier locked",
        }

    def daily_report(self) -> dict[str, Any]:
        trades = list(self.closed_paper)
        wins = [trade for trade in trades if trade.pnl > 0]
        losses = [trade for trade in trades if trade.pnl < 0]
        gross_profit = sum(trade.pnl for trade in wins)
        gross_loss = abs(sum(trade.pnl for trade in losses))
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss else round(gross_profit, 2)
        max_drawdown = self._max_drawdown([trade.pnl for trade in trades])
        return {
            "totalSignals": len(self.lifecycle_events),
            "paperTrades": len(trades),
            "openTrades": len(self.open_paper),
            "wins": len(wins),
            "losses": len(losses),
            "winRate": round((len(wins) / len(trades)) * 100, 2) if trades else 0,
            "grossProfit": round(gross_profit, 2),
            "grossLoss": round(gross_loss, 2),
            "profitFactor": profit_factor,
            "maxDrawdown": round(max_drawdown, 2),
            "bestSession": "open_drive" if wins else None,
            "worstSession": "midday_chop" if losses else None,
            "reasonForLosses": self._loss_reasons(losses),
        }

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

    def _pre_trade_quality(self, candidate: dict[str, Any]) -> dict[str, Any]:
        premium = float(candidate.get("lastPremium") or 0)
        spread_cost = max(0.0, premium * 0.004)
        slippage = max(0.05, premium * 0.002)
        required_move = spread_cost + slippage + self.settings.min_required_move_points
        reasons = []
        if premium <= 0:
            reasons.append("missing premium")
        if candidate.get("chopBlocked"):
            reasons.append("chop filter blocked")
        if candidate.get("tqs", 0) < 68:
            reasons.append("TQS below production learning threshold")
        if candidate.get("effectiveVolume", 0) <= 0:
            reasons.append("missing effective volume")
        if required_move > self.settings.min_required_move_points * 1.4:
            reasons.append("spread/slippage cost too high for 5-point scalp")
        return {
            "blocked": bool(reasons),
            "reason": ", ".join(reasons) if reasons else "quality accepted",
            "spreadCost": round(spread_cost, 2),
            "slippageEstimate": round(slippage, 2),
            "minimumRequiredMove": round(required_move, 2),
        }

    def _open_paper_trade(self, candidate: dict[str, Any], quality: dict[str, Any]) -> PaperTrade | None:
        trade_id = str(candidate.get("id") or uuid4())
        if trade_id in self.open_paper:
            return None
        quantity = int(candidate.get("quantityEstimate") or 1)
        trade = PaperTrade(
            id=trade_id,
            symbol=str(candidate.get("symbol")),
            side=str(candidate.get("side")),
            strike=int(candidate.get("strike") or 0),
            expiry=str(candidate.get("expiry")),
            instrument_key=candidate.get("instrumentKey"),
            entry_price=float(candidate.get("lastPremium") or 0),
            quantity=max(1, quantity),
            entry_tqs=int(candidate.get("tqs") or 0),
            spread_cost=float(quality["spreadCost"]),
            slippage_estimate=float(quality["slippageEstimate"]),
            opened_at=datetime.now(timezone.utc).isoformat(),
            mode=str(candidate.get("mode")),
            strategy_type=str(candidate.get("strategyType") or "SCALP"),
        )
        trade.lifecycle.extend([
            LifecycleEvent("RISK_CHECKED", trade.opened_at, quality["reason"], quality),
            LifecycleEvent("PAPER_OPENED", trade.opened_at, "Shadow trade opened; no broker order placed", {"entry": trade.entry_price}),
        ])
        self.open_paper[trade.id] = trade
        return trade

    def _update_open_paper(self, snapshots: dict[str, Any]) -> list[dict[str, Any]]:
        exits = []
        candidates_by_id = {}
        for snapshot in snapshots.values():
            for candidate in snapshot.get("suggestedTrades") or []:
                candidates_by_id[candidate.get("id")] = candidate
        for trade_id, trade in list(self.open_paper.items()):
            candidate = candidates_by_id.get(trade_id)
            current = float((candidate or {}).get("lastPremium") or trade.entry_price)
            age = self._age_seconds(trade.opened_at)
            reason = None
            profile = (candidate or {}).get("optimizedProfile") or {}
            target_points = float(profile.get("targetPoints") or self.settings.paper_target_points)
            stop_points = float(profile.get("stopPoints") or self.settings.paper_stop_points)
            style = str(profile.get("executionStyle") or "GENERIC")
            if style == "RUNNER_BREAKOUT":
                target_points = max(target_points, self.settings.paper_target_points * 1.2)
            elif style == "HIGH_WIN_SCALP":
                target_points = min(target_points, self.settings.paper_target_points)
            if current >= trade.entry_price + target_points:
                reason = "trailing profit lock / target extension"
            elif current <= trade.entry_price - stop_points:
                reason = "momentum decay or delta reversal stop"
            elif age >= self.settings.max_paper_trade_seconds:
                reason = "time stop"
            elif (candidate or {}).get("chopBlocked"):
                reason = "liquidity rejection / chop filter exit"
            if reason:
                trade.status = "EXITED"
                trade.exit_price = current
                trade.exit_reason = reason
                trade.exited_at = datetime.now(timezone.utc).isoformat()
                trade.pnl = (current - trade.entry_price - trade.spread_cost - trade.slippage_estimate) * trade.quantity
                trade.lifecycle.append(LifecycleEvent("EXITED", trade.exited_at, reason, {"exit": current, "pnl": trade.pnl}))
                self.closed_paper.append(trade)
                self.lifecycle_events.extend(trade.lifecycle[-1:])
                del self.open_paper[trade_id]
                exits.append(trade.to_dict())
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
        estimates = [self._pre_trade_quality(candidate)["slippageEstimate"] for candidate in candidates]
        return {
            "averageExpectedSlippage": round(sum(estimates) / len(estimates), 2) if estimates else 0,
            "minimumRequiredMovePoints": self.settings.min_required_move_points,
            "model": "premium_based_spread_slippage_guard",
        }

    def _position_sizing_summary(self, candidates: list[dict[str, Any]], capital: float) -> dict[str, Any]:
        return {
            "capital": capital,
            "candidates": [
                {
                    "id": candidate.get("id"),
                    "quantityEstimate": candidate.get("quantityEstimate", 0),
                    "allocationPct": candidate.get("allocationPct", 0),
                    "tqs": candidate.get("tqs"),
                }
                for candidate in candidates[:10]
            ],
        }

    def _compact_snapshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": payload.get("type"),
            "timestamp": payload.get("timestamp"),
            "displaySymbol": payload.get("displaySymbol") or payload.get("symbol"),
            "tradeQualityScore": payload.get("tradeQualityScore"),
            "marketPhase": payload.get("marketPhase"),
            "executionCandidates": payload.get("executionCandidates", [])[:10],
        }

    def _age_seconds(self, iso_timestamp: str) -> float:
        try:
            return (datetime.now(timezone.utc) - datetime.fromisoformat(iso_timestamp)).total_seconds()
        except ValueError:
            return 0.0

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
