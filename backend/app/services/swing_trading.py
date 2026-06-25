"""Swing lane — ride sustained index-option trends between scalp and explosive sniper.

Scalp books +5–6pt quickly; explosive needs 92+ ultra-elite. Swing catches aligned
trend continuation (e.g. midday 150→215 CE rallies) with wider trail and longer hold.
"""
from __future__ import annotations

from typing import Any

SWING_MIN_HOLD_SECONDS = 120.0
SWING_MAX_HOLD_SECONDS = 600.0
SWING_STOP_POINTS = 5.0
SWING_TRAIL_ARM_POINTS = 6.0
SWING_TRAIL_RETAIN_PCT = 0.65
SWING_PARTIAL_POINTS = 8.0
SWING_PARTIAL_PCT = 0.45

SESSION_SWING_TARGETS: dict[str, float] = {
    "OPEN_DRIVE": 16.0,
    "NORMAL": 14.0,
    "MIDDAY_CHOP": 12.0,
    "CLOSING_MOMENTUM": 15.0,
}


def swing_profile(session_bucket: str, settings: Any | None = None) -> dict[str, float]:
    bucket = str(session_bucket or "NORMAL").upper()
    target = float(SESSION_SWING_TARGETS.get(bucket, 14.0))
    if settings is not None:
        target = float(getattr(settings, "paper_swing_target_points", target) or target)
    return {
        "targetPoints": target,
        "stopPoints": float(getattr(settings, "paper_swing_stop_points", SWING_STOP_POINTS) if settings else SWING_STOP_POINTS),
        "minHoldSeconds": float(getattr(settings, "paper_swing_min_hold_seconds", SWING_MIN_HOLD_SECONDS) if settings else SWING_MIN_HOLD_SECONDS),
        "maxHoldSeconds": float(getattr(settings, "paper_swing_max_hold_seconds", SWING_MAX_HOLD_SECONDS) if settings else SWING_MAX_HOLD_SECONDS),
        "trailArmPoints": float(getattr(settings, "paper_swing_trail_arm_points", SWING_TRAIL_ARM_POINTS) if settings else SWING_TRAIL_ARM_POINTS),
        "trailRetainPct": float(getattr(settings, "paper_swing_trail_retain_pct", SWING_TRAIL_RETAIN_PCT) if settings else SWING_TRAIL_RETAIN_PCT),
        "partialPoints": float(getattr(settings, "paper_swing_partial_points", SWING_PARTIAL_POINTS) if settings else SWING_PARTIAL_POINTS),
        "partialPct": float(getattr(settings, "paper_swing_partial_pct", SWING_PARTIAL_PCT) if settings else SWING_PARTIAL_PCT),
    }


def passes_swing_entry(
    candidate: dict[str, Any],
    session_bucket: str,
    settings: Any,
    *,
    breadth: dict[str, Any] | None = None,
    regime: str = "NORMAL",
) -> tuple[bool, str]:
    """Sustained trend — not ultra burst (explosive) and not dead tape."""
    if not bool(getattr(settings, "paper_swing_trading_enabled", True)):
        return False, "swing gate: disabled"
    runner = candidate.get("runnerSignal") or {}
    metrics = runner.get("metrics") or {}
    vel = float(runner.get("premiumVelocityPct") or metrics.get("premiumVelocity") or 0)
    score = float(runner.get("score") or candidate.get("tqs") or 0)
    min_score = float(getattr(settings, "paper_swing_min_runner_score", 78.0))
    min_vel = float(getattr(settings, "paper_swing_min_velocity_pct", 1.2))
    max_vel = float(getattr(settings, "paper_swing_max_velocity_pct", 4.5))
    if score < min_score:
        return False, f"swing gate: runner score {score:.0f} < {min_score:.0f}"
    if vel < min_vel:
        return False, f"swing gate: velocity {vel:.1f}% < {min_vel}%"
    if vel > max_vel:
        return False, f"swing gate: velocity {vel:.1f}% too high for swing (use explosive)"
    if not (runner.get("momentumAligned") or runner.get("momentumSurge") or runner.get("momentumOverride")):
        return False, "swing gate: momentum not aligned"
    premium = float(candidate.get("lastPremium") or runner.get("premium") or 0)
    if premium <= 0:
        return False, "swing gate: missing premium"
    breadth = breadth or {}
    if breadth.get("enabled") and breadth.get("available"):
        if not breadth.get("aligned"):
            return False, f"swing gate: breadth not aligned ({breadth.get('bias')})"
    bucket = str(session_bucket or "NORMAL").upper()
    regime_u = str(regime or "NORMAL").upper()
    if bucket == "MIDDAY_CHOP" and regime_u not in {"TREND_EXPANSION", "NORMAL"}:
        if score < float(getattr(settings, "paper_swing_midday_min_score", 82.0)):
            return False, "swing gate: midday chop needs score ≥ 82 with trend"
    if regime_u == "REVERSAL_RISK" and not runner.get("momentumOverride"):
        return False, "swing gate: reversal risk — no swing without override"
    return True, ""


def swing_lot_bounds(settings: Any, *, rolling_pf: float = 0.0) -> tuple[int, int, int]:
    min_lots = int(getattr(settings, "paper_swing_min_lots", 4))
    target_lots = int(getattr(settings, "paper_swing_target_lots", 6))
    max_lots = int(getattr(settings, "paper_swing_max_lots", 8))
    if rolling_pf < 1.0:
        return min_lots, min_lots, min(min_lots + 1, max_lots)
    if rolling_pf < float(getattr(settings, "paper_target_profit_factor", 2.5)):
        return min_lots, min(target_lots, min_lots + 2), min(target_lots, max_lots)
    return min_lots, target_lots, max_lots


def swing_profit_exit(
    *,
    entry: float,
    current: float,
    best: float,
    age: float,
    session_bucket: str,
    settings: Any,
    partial_taken: bool = False,
) -> str | None:
    """Wider trail swing exits — ride trend, book on fade."""
    profile = swing_profile(session_bucket, settings)
    stop = profile["stopPoints"]
    target = profile["targetPoints"]
    min_hold = profile["minHoldSeconds"]
    max_hold = profile["maxHoldSeconds"]
    arm = profile["trailArmPoints"]
    retain = profile["trailRetainPct"]
    partial_pts = profile["partialPoints"]
    unrealized = current - entry
    best_gain = max(0.0, best - entry)

    if age < min_hold and unrealized > -stop:
        return None
    if current <= entry - stop:
        return "swing stop loss"
    if not partial_taken and current >= entry + partial_pts and age >= min_hold * 0.5:
        return None  # partial handled separately in auto_trader
    if current >= entry + target:
        return "swing target hit"
    if best_gain >= arm:
        floor_price = entry + max(2.0, best_gain * retain)
        if current <= floor_price:
            return "swing trail profit lock"
    if age >= max_hold:
        if unrealized >= 2.0:
            return "swing time profit lock"
        return "swing time stop"
    if age >= min_hold * 2 and best_gain >= 4.0 and unrealized < best_gain * 0.45:
        return "swing giveback exit"
    return None
