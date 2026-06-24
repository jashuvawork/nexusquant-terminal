"""Simple profit mode — one entry gate, capped size, three exit rules.

Replaces the stacked ACS / mastermind / partial / decay exit layers when enabled.
"""
from __future__ import annotations

from typing import Any

SESSION_TARGETS: dict[str, float] = {
    "OPEN_DRIVE": 5.0,
    "NORMAL": 4.0,
    "MIDDAY_CHOP": 3.0,
    "CLOSING_MOMENTUM": 4.5,
}


def session_target_points(session_bucket: str, settings: Any) -> float:
    bucket = str(session_bucket or "NORMAL").upper()
    return float(SESSION_TARGETS.get(bucket, getattr(settings, "paper_simple_target_points", 4.0)))


def passes_simple_entry(candidate: dict[str, Any], session_bucket: str, settings: Any) -> tuple[bool, str]:
    """Only enter when tape is actually moving — no dead-tape scalps."""
    runner = candidate.get("runnerSignal") or {}
    metrics = runner.get("metrics") or {}
    vel = float(runner.get("premiumVelocityPct") or metrics.get("premiumVelocity") or 0)
    score = float(runner.get("score") or candidate.get("tqs") or 0)
    min_vel = float(getattr(settings, "paper_simple_min_velocity_pct", 2.0))
    min_score = float(getattr(settings, "paper_simple_min_runner_score", 72.0))
    if vel < min_vel:
        return False, f"simple gate: velocity {vel:.1f}% < {min_vel}%"
    if score < min_score:
        return False, f"simple gate: runner score {score:.0f} < {min_score}"
    if not (runner.get("momentumSurge") or runner.get("momentumAligned") or runner.get("momentumOverride")):
        return False, "simple gate: no live momentum surge/alignment"
    if float(candidate.get("lastPremium") or candidate.get("premium") or 0) <= 0:
        return False, "simple gate: missing premium"
    return True, ""


def passes_simple_breadth(candidate: dict[str, Any], breadth: dict[str, Any] | None) -> tuple[bool, str]:
    """CALL only when breadth bullish; PUT only when bearish — matches today's only win."""
    if not breadth or not breadth.get("available"):
        return True, ""
    side = str(candidate.get("side") or "").upper()
    if side == "CALL" and not breadth.get("aligned") and str(breadth.get("bias") or "").upper() != "BULLISH":
        return False, f"simple gate: CALL needs bullish breadth (got {breadth.get('bias')})"
    if side == "PUT" and not breadth.get("aligned") and str(breadth.get("bias") or "").upper() != "BEARISH":
        return False, f"simple gate: PUT needs bearish breadth (got {breadth.get('bias')})"
    return True, ""


def simple_lot_bounds(settings: Any) -> tuple[int, int, int]:
    return (
        int(getattr(settings, "paper_simple_min_lots", 2)),
        int(getattr(settings, "paper_simple_target_lots", 4)),
        int(getattr(settings, "paper_simple_max_lots", 6)),
    )


def simple_profit_exit(
    *,
    entry: float,
    current: float,
    best: float,
    age: float,
    quantity: int,
    session_bucket: str,
    settings: Any,
) -> str | None:
    """Three rules: target hit, stop after min-hold, trail after arm. Plus INR emergency cap."""
    unrealized = current - entry
    best_gain = max(0.0, best - entry)
    qty = max(1, int(quantity))
    loss_inr = max(0.0, -unrealized * qty)
    min_hold = float(getattr(settings, "paper_simple_min_hold_seconds", 30.0))
    max_hold = float(getattr(settings, "paper_simple_max_hold_seconds", 180.0))
    target = session_target_points(session_bucket, settings)
    stop = float(getattr(settings, "paper_simple_stop_points", 3.0))
    trail_arm = float(getattr(settings, "paper_simple_trail_arm_points", 2.0))
    trail_retain = float(getattr(settings, "paper_simple_trail_retain_pct", 0.50))
    emergency_inr = float(getattr(settings, "paper_simple_emergency_loss_inr", 4000.0))

    if loss_inr >= emergency_inr:
        return "simple emergency INR stop"

    if age < min_hold:
        return None

    # No progress after 90s — scratch before full time stop bleeds charges
    if age >= 90 and best_gain < 0.5 and unrealized < 0:
        return "simple no-progress scratch"

    if unrealized >= target:
        return "simple profit target hit"
    if unrealized <= -stop:
        return "simple stop loss"
    if best_gain >= trail_arm and unrealized <= entry + best_gain * trail_retain:
        return "simple trail profit lock"
    if age >= max_hold:
        return "simple time profit lock" if unrealized > 0 else "simple time stop"
    return None
