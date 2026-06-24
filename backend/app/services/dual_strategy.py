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
    if vel < 1.0 and not runner.get("momentumSurge"):
        return False, "scalp gate: insufficient premium velocity for +6pt capture"
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
