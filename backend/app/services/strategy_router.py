"""Market-aware strategy router — pick lane + exit engine per trade when all modes are on."""
from __future__ import annotations

from typing import Any

from app.services.dual_strategy import (
    downgrade_runner_to_scalp,
    explosive_profile,
    is_ultra_elite_explosive,
    passes_scalp_entry_gate,
    scalp_profile,
)
from app.services.simple_profit import passes_simple_breadth, passes_simple_entry

EXEC_SIMPLE = "SIMPLE"
EXEC_DUAL_SCALP = "DUAL_SCALP"
EXEC_DUAL_EXPLOSIVE = "DUAL_EXPLOSIVE"
EXEC_ACS = "ACS"
EXEC_MASTERMIND = "MASTERMIND"


def allocate_trade_strategy(
    candidate: dict[str, Any],
    settings: Any,
    *,
    session_bucket: str,
    breadth: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Choose strategy type + exit engine from live tape — one plan per trade."""
    runner = candidate.get("runnerSignal") or {}
    strategy = str(candidate.get("strategyType") or "SCALP").upper()
    bucket = str(session_bucket or "NORMAL").upper()
    vel = float(runner.get("premiumVelocityPct") or (runner.get("metrics") or {}).get("premiumVelocity") or 0)
    score = float(runner.get("score") or candidate.get("tqs") or 0)

    # Ultra-elite explosive sniper
    if strategy == "EXPLOSIVE_RUNNER" and is_ultra_elite_explosive(candidate, settings):
        plan = EXEC_DUAL_EXPLOSIVE
        if bool(getattr(settings, "paper_trade_mastermind_enabled", False)):
            plan = EXEC_MASTERMIND
        return _pack(candidate, "EXPLOSIVE_RUNNER", plan, bucket, "ultra-elite explosive — sniper lane")

    # Strong runner with mastermind governor
    if strategy == "EXPLOSIVE_RUNNER" and score >= 85 and vel >= 3.0 and runner.get("momentumAligned"):
        if bool(getattr(settings, "paper_trade_mastermind_enabled", False)):
            return _pack(candidate, "EXPLOSIVE_RUNNER", EXEC_MASTERMIND, bucket, "strong runner — mastermind lane")
        return _pack(candidate, "EXPLOSIVE_RUNNER", EXEC_ACS, bucket, "strong runner — ACS lane")

    # Dual scalp workhorse (ATM / TQS / VAH-VAL) — before simple so runners don't inherit 14-lot simple sizing
    scalp_ok, _ = passes_scalp_entry_gate(candidate, settings, breadth=breadth)
    if scalp_ok and bool(getattr(settings, "paper_dual_strategy_enabled", True)):
        downgraded = downgrade_runner_to_scalp(candidate, bucket) if strategy == "EXPLOSIVE_RUNNER" else dict(candidate)
        return _pack(downgraded, "SCALP", EXEC_DUAL_SCALP, bucket, "dual scalp — ATM workhorse +6pt")

    # Runner watchlist → downgrade to dual scalp if possible
    if strategy == "EXPLOSIVE_RUNNER" and bool(getattr(settings, "paper_dual_strategy_enabled", True)):
        downgraded = downgrade_runner_to_scalp(candidate, bucket)
        scalp_ok2, _ = passes_scalp_entry_gate(downgraded, settings, breadth=breadth)
        if scalp_ok2:
            return _pack(downgraded, "SCALP", EXEC_DUAL_SCALP, bucket, "runner downgraded — dual scalp workhorse")

    # Catch / burst runners (max-catch or momentum burst) — ACS pro quick book
    if strategy == "EXPLOSIVE_RUNNER":
        if bool(getattr(settings, "paper_max_catch_mode", False)) and score >= float(getattr(settings, "paper_max_catch_min_runner_score", 88)):
            return _pack(candidate, "EXPLOSIVE_RUNNER", EXEC_ACS, bucket, "max-catch runner — ACS pro quick book")
        if vel >= float(getattr(settings, "paper_momentum_burst_min_velocity_pct", 2.0)) and score >= float(
            getattr(settings, "paper_momentum_burst_min_runner_score", 65)
        ):
            return _pack(candidate, "EXPLOSIVE_RUNNER", EXEC_ACS, bucket, "momentum burst — ACS pro quick book")
        # Strong tape runners: ACS exits (not simple) — avoids yesterday's simple time-stop bleed on NIFTY
        if score >= 72 and vel >= float(getattr(settings, "paper_simple_min_velocity_pct", 2.0)):
            return _pack(candidate, "EXPLOSIVE_RUNNER", EXEC_ACS, bucket, "active runner — ACS pro quick book")

    # Simple profit — SCALP lane only (never raw EXPLOSIVE_RUNNER)
    if strategy == "SCALP" and bool(getattr(settings, "paper_simple_profit_mode", True)):
        simple_ok, _ = passes_simple_entry(candidate, bucket, settings)
        breadth_ok, _ = passes_simple_breadth(candidate, breadth)
        if simple_ok and breadth_ok:
            return _pack(candidate, "SCALP", EXEC_SIMPLE, bucket, "simple profit — momentum + breadth aligned")

    # Default ACS scalp / quick book
    if strategy == "SCALP" or bool(getattr(settings, "paper_acs_scalp_enabled", True)):
        body = downgrade_runner_to_scalp(candidate, bucket) if strategy == "EXPLOSIVE_RUNNER" else dict(candidate)
        lane = "SCALP" if strategy == "SCALP" else "SCALP"
        return _pack(body, lane, EXEC_ACS, bucket, "default ACS pro quick book lane")

    return None


def _pack(candidate: dict[str, Any], strategy_type: str, execution_plan: str, bucket: str, reason: str) -> dict[str, Any]:
    out = dict(candidate)
    out["strategyType"] = strategy_type
    out["executionPlan"] = execution_plan
    out["executionPlanReason"] = reason
    opt = dict(out.get("optimizedProfile") or {})
    if execution_plan == EXEC_SIMPLE:
        opt["executionStyle"] = "HIGH_WIN_SCALP"
    elif execution_plan == EXEC_DUAL_SCALP:
        sp = scalp_profile(bucket)
        opt["executionStyle"] = "HIGH_WIN_SCALP"
        opt["targetPoints"] = sp["targetPoints"]
        opt["stopPoints"] = sp["stopPoints"]
    elif execution_plan in {EXEC_DUAL_EXPLOSIVE, EXEC_MASTERMIND, EXEC_ACS}:
        if strategy_type == "EXPLOSIVE_RUNNER":
            ep = explosive_profile()
            opt["executionStyle"] = "RUNNER_BREAKOUT"
            opt["targetPoints"] = ep["targetPoints"]
            opt["stopPoints"] = ep["stopPoints"]
        else:
            sp = scalp_profile(bucket)
            opt["executionStyle"] = "HIGH_WIN_SCALP"
            opt["targetPoints"] = sp["targetPoints"]
            opt["stopPoints"] = sp["stopPoints"]
    out["optimizedProfile"] = opt
    return out
