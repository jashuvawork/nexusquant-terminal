"""Scalp workhorse + Explosive sniper — dual lane spec from product summary.

Scalp: +6pt captures, 40–180s, ATM/near-ATM, TQS + VAH/VAL + breadth, ACS exits.
Explosive: ultra-elite only (92+ / 3% vel / aligned), 90–900s, chain scan, 1 at a time.
"""
from __future__ import annotations

from typing import Any

SCALP_TARGET_POINTS = 6.0
SCALP_MIN_HOLD_SECONDS = 40.0
SCALP_MAX_HOLD_SECONDS = 180.0
SCALP_STOP_POINTS = 3.0
SCALP_TIME_LOCK_SECONDS = 75.0
SCALP_TIME_LOCK_MIN_GAIN = 2.5

EXPLOSIVE_MIN_HOLD_SECONDS = 90.0
EXPLOSIVE_MAX_HOLD_SECONDS = 900.0
EXPLOSIVE_MAX_OPEN_TRADES = 1

ULTRA_ELITE_MIN_SCORE = 92.0
ULTRA_ELITE_MIN_VELOCITY_PCT = 3.0
ULTRA_ELITE_MIN_VOLUME_ACCEL = 30.0

# PF-first scalp sizing — small lots keep losses from dominating gross wins.
DUAL_SCALP_MIN_LOTS = 2
DUAL_SCALP_TARGET_LOTS = 4
DUAL_SCALP_MAX_LOTS = 6
DUAL_SCALP_MAX_LOSS_INR = 2500.0
DUAL_SCALP_MIN_VELOCITY_PCT = 1.5
DUAL_SCALP_PF_STRICT_TQS = 58


def dual_scalp_lot_bounds(settings: Any, *, rolling_pf: float = 0.0) -> tuple[int, int, int]:
    """Scale scalp lots down when rolling PF is weak — protects profit factor."""
    min_lots = int(getattr(settings, "paper_dual_scalp_min_lots", DUAL_SCALP_MIN_LOTS))
    target_lots = int(getattr(settings, "paper_dual_scalp_target_lots", DUAL_SCALP_TARGET_LOTS))
    max_lots = int(getattr(settings, "paper_dual_scalp_max_lots", DUAL_SCALP_MAX_LOTS))
    target_pf = float(getattr(settings, "paper_target_profit_factor", 2.5))
    if rolling_pf < 1.0:
        return min_lots, min_lots, min(min_lots + 1, max_lots)
    if rolling_pf < target_pf:
        return min_lots, min(target_lots, min_lots + 2), min(target_lots, max_lots)
    return min_lots, target_lots, max_lots


def dual_explosive_lot_bounds(settings: Any, *, rolling_pf: float = 0.0) -> tuple[int, int, int]:
    """Explosive sniper stays small until edge is proven."""
    min_lots = int(getattr(settings, "paper_dual_explosive_min_lots", 2))
    target_lots = int(getattr(settings, "paper_dual_explosive_target_lots", 3))
    max_lots = int(getattr(settings, "paper_dual_explosive_max_lots", 4))
    if rolling_pf < 1.0:
        return min_lots, min_lots, min_lots + 1
    if rolling_pf < float(getattr(settings, "paper_target_profit_factor", 2.5)):
        return min_lots, min(target_lots, 3), min(max_lots, 3)
    return min_lots, target_lots, max_lots


def scalp_profile(session_bucket: str) -> dict[str, float]:
    bucket = str(session_bucket or "NORMAL").upper()
    target = SCALP_TARGET_POINTS
    if bucket == "MIDDAY_CHOP":
        target = 5.0
    elif bucket == "OPEN_DRIVE":
        target = 6.0
    elif bucket == "CLOSING_MOMENTUM":
        target = 6.5
    return {
        "targetPoints": target,
        "stopPoints": SCALP_STOP_POINTS,
        "minHoldSeconds": SCALP_MIN_HOLD_SECONDS,
        "maxHoldSeconds": SCALP_MAX_HOLD_SECONDS,
        "timeLockSeconds": SCALP_TIME_LOCK_SECONDS,
        "timeLockMinGain": SCALP_TIME_LOCK_MIN_GAIN,
        "quickProfitPoints": target,
    }


def explosive_profile() -> dict[str, float]:
    return {
        "targetPoints": 12.0,
        "stopPoints": 6.0,
        "minHoldSeconds": EXPLOSIVE_MIN_HOLD_SECONDS,
        "maxHoldSeconds": EXPLOSIVE_MAX_HOLD_SECONDS,
        "trailRetainPct": 45.0,
    }


def is_ultra_elite_explosive(candidate: dict[str, Any], settings: Any) -> bool:
    """Only ultra-elite bar keeps EXPLOSIVE_RUNNER — sniper lane."""
    if str(candidate.get("strategyType") or "").upper() != "EXPLOSIVE_RUNNER":
        return False
    runner = candidate.get("runnerSignal") or {}
    if str(runner.get("confidence") or "").upper() != "HIGH":
        return False
    if not runner.get("eliteRunner"):
        return False
    if not runner.get("momentumAligned"):
        return False
    score = float(runner.get("score") or candidate.get("tqs") or 0)
    if score < float(getattr(settings, "paper_ultra_elite_min_runner_score", ULTRA_ELITE_MIN_SCORE)):
        return False
    metrics = runner.get("metrics") or {}
    vel = float(runner.get("premiumVelocityPct") or metrics.get("premiumVelocity") or 0)
    vol = float(runner.get("volumeAcceleration") or metrics.get("volumeAcceleration") or 0)
    if vel < float(getattr(settings, "paper_ultra_elite_min_velocity_pct", ULTRA_ELITE_MIN_VELOCITY_PCT)):
        return False
    if vol < float(getattr(settings, "paper_ultra_elite_min_volume_accel", ULTRA_ELITE_MIN_VOLUME_ACCEL)):
        return False
    premium = float(candidate.get("lastPremium") or runner.get("premium") or 0)
    if premium <= 0:
        return False
    return True


def downgrade_runner_to_scalp(candidate: dict[str, Any], session_bucket: str) -> dict[str, Any]:
    """Runner watchlist hit → scalp workhorse unless ultra-elite."""
    scalp = dict(candidate)
    scalp["strategyType"] = "SCALP"
    profile = scalp_profile(session_bucket)
    opt = dict(scalp.get("optimizedProfile") or {})
    opt["executionStyle"] = "HIGH_WIN_SCALP"
    opt["targetPoints"] = profile["targetPoints"]
    opt["stopPoints"] = profile["stopPoints"]
    scalp["optimizedProfile"] = opt
    return scalp


def is_atm_or_near_atm(candidate: dict[str, Any], *, max_strikes_from_atm: int = 2) -> bool:
    """Scalp lane prefers planned ATM / near-ATM strikes."""
    strike = int(candidate.get("strike") or 0)
    if strike <= 0:
        return True
    mp = candidate.get("marketProfile") or {}
    spot = float(mp.get("spot") or candidate.get("spot") or 0)
    if spot <= 0:
        atm = candidate.get("atmStrike")
        if atm:
            spot = float(atm)
    if spot <= 0:
        return True
    step = 50 if str(candidate.get("symbol") or "").upper() in {"NIFTY", "BANKNIFTY"} else 100
    distance = abs(strike - spot)
    return distance <= step * max_strikes_from_atm


def passes_scalp_entry_gate(
    candidate: dict[str, Any],
    settings: Any,
    *,
    breadth: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """TQS + near-ATM + VAH/VAL + breadth for scalp workhorse entries."""
    if not is_atm_or_near_atm(candidate):
        return False, "scalp gate: strike too far from ATM — workhorse uses planned ATM/near-ATM"
    tqs = int(candidate.get("tqs") or 0)
    min_tqs = int(getattr(settings, "paper_scalping_min_entry_tqs", 52))
    if tqs < min_tqs:
        return False, f"scalp gate: TQS {tqs} < {min_tqs}"
    runner = candidate.get("runnerSignal") or {}
    vel = float(runner.get("premiumVelocityPct") or (runner.get("metrics") or {}).get("premiumVelocity") or 0)
    min_vel = float(getattr(settings, "paper_dual_scalp_min_velocity_pct", DUAL_SCALP_MIN_VELOCITY_PCT))
    if vel < min_vel and not runner.get("momentumSurge"):
        return False, f"scalp gate: velocity {vel:.1f}% < {min_vel}% for +6pt capture"
    if not (runner.get("momentumSurge") or runner.get("momentumAligned")):
        return False, "scalp gate: need momentum surge or aligned tape"
    side = str(candidate.get("side") or "").upper()
    mp = candidate.get("marketProfile") or {}
    spot = float(mp.get("spot") or candidate.get("spot") or 0)
    vah = float(mp.get("vah") or 0)
    val = float(mp.get("val") or 0)
    if spot > 0 and vah > 0 and val > 0 and vah > val:
        if side == "CALL" and spot < val:
            return False, "scalp gate: CALL below VAL — wait for value-area reclaim"
        if side == "PUT" and spot > vah:
            return False, "scalp gate: PUT above VAH — wait for value-area rejection"
    if breadth and breadth.get("available") and not breadth.get("aligned"):
        if bool(getattr(settings, "paper_breadth_filter_enabled", True)):
            return False, str(breadth.get("reason") or "scalp gate: breadth does not confirm trade side")
    return True, ""
