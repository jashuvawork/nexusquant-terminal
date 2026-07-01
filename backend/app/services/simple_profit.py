"""Simple profit mode — reference ledger micro-scalp or legacy simple exits."""
from __future__ import annotations

from typing import Any

SESSION_TARGETS: dict[str, float] = {
    "OPEN_DRIVE": 12.0,
    "NORMAL": 10.0,
    "MIDDAY_CHOP": 8.0,
    "CLOSING_MOMENTUM": 10.0,
}


def reference_ledger_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "paper_reference_ledger_mode", False))


def session_target_points(session_bucket: str, settings: Any) -> float:
    bucket = str(session_bucket or "NORMAL").upper()
    if reference_ledger_enabled(settings):
        return float(getattr(settings, "paper_reference_target_points", 12.0))
    return float(SESSION_TARGETS.get(bucket, getattr(settings, "paper_simple_target_points", 4.0)))


def reference_entry_confidence(candidate: dict[str, Any], breadth: dict[str, Any] | None) -> float:
    """0–100 tape confidence for CE/PE scaling and side-switch decisions."""
    runner = candidate.get("runnerSignal") or {}
    metrics = runner.get("metrics") or {}
    score = float(runner.get("score") or candidate.get("tqs") or 0)
    vel = float(runner.get("premiumVelocityPct") or metrics.get("premiumVelocity") or 0)
    confidence = score * 0.55 + vel * 9.0
    if runner.get("momentumSurge"):
        confidence += 18.0
    if runner.get("momentumAligned"):
        confidence += 12.0
    if runner.get("momentumOverride"):
        confidence += 15.0
    if str(runner.get("confidence") or "").upper() == "HIGH":
        confidence += 10.0
    side = str(candidate.get("side") or "").upper()
    bscore = float((breadth or {}).get("score") or 50)
    if side == "CALL" and bscore >= 55:
        confidence += 8.0
    elif side == "PUT" and bscore <= 45:
        confidence += 8.0
    return max(0.0, min(100.0, confidence))


def reference_preferred_side(candidate: dict[str, Any], breadth: dict[str, Any] | None) -> str:
    runner = candidate.get("runnerSignal") or {}
    chart = str(candidate.get("chartBias") or "").upper()
    bias = str(runner.get("directionalBias") or candidate.get("directionalBias") or "").upper()
    bscore = float((breadth or {}).get("score") or 50)
    if bias == "BULLISH" or chart == "CALL" or bscore >= 58:
        return "CALL"
    if bias == "BEARISH" or chart == "PUT" or bscore <= 42:
        return "PUT"
    side = str(candidate.get("side") or runner.get("side") or "").upper()
    if runner.get("momentumAligned") and side in {"CALL", "PUT"}:
        return side
    return "WAIT"


def reference_side_switch_gate(
    candidate: dict[str, Any],
    breadth: dict[str, Any] | None,
    settings: Any,
) -> tuple[bool, str, float]:
    """Allow quick CE↔PE switch when tape confidence supports the candidate side."""
    if not getattr(settings, "paper_reference_side_switch_enabled", True):
        return True, "", reference_entry_confidence(candidate, breadth)
    runner = candidate.get("runnerSignal") or {}
    side = str(candidate.get("side") or "").upper()
    if side not in {"CALL", "PUT"}:
        return False, "reference gate: missing side", 0.0
    confidence = reference_entry_confidence(candidate, breadth)
    preferred = reference_preferred_side(candidate, breadth)
    min_vel = float(getattr(settings, "paper_reference_min_velocity_pct", 0.75))
    vel = float(runner.get("premiumVelocityPct") or (runner.get("metrics") or {}).get("premiumVelocity") or 0)
    switch_min = float(getattr(settings, "paper_reference_side_switch_min_confidence", 62.0))
    full_min = float(getattr(settings, "paper_reference_confidence_full_pct", 82.0))

    if runner.get("momentumOverride") or confidence >= full_min:
        return True, f"reference side OK — {side} confidence {confidence:.0f}%", confidence
    if preferred in {"CALL", "PUT"} and side == preferred:
        return True, f"reference aligned {side} confidence {confidence:.0f}%", confidence
    if confidence >= switch_min and vel >= min_vel * 1.1:
        return True, f"CE/PE switch — {side} tape confidence {confidence:.0f}% (preferred {preferred})", confidence
    if preferred in {"CALL", "PUT"} and side != preferred and confidence < switch_min:
        return False, f"CE/PE block: {side} vs tape {preferred} confidence {confidence:.0f}% < {switch_min:.0f}", confidence
    return True, "", confidence


def reference_confidence_lots(settings: Any, confidence: float) -> tuple[int, int, int]:
    """Quick scale lots by confidence — more trades at smaller size, full size on burst."""
    fixed = int(getattr(settings, "paper_reference_fixed_lots", 100))
    if not getattr(settings, "paper_reference_confidence_scale_enabled", True):
        return fixed, fixed, fixed
    min_lots = int(getattr(settings, "paper_reference_scale_min_lots", 50))
    mid_lots = int(getattr(settings, "paper_reference_scale_mid_lots", 75))
    full_pct = float(getattr(settings, "paper_reference_confidence_full_pct", 82.0))
    mid_pct = float(getattr(settings, "paper_reference_confidence_mid_pct", 65.0))
    if confidence >= full_pct:
        return fixed, fixed, fixed
    if confidence >= mid_pct:
        return min_lots, mid_lots, fixed
    return min_lots, min_lots, mid_lots


def passes_reference_entry(candidate: dict[str, Any], session_bucket: str, settings: Any) -> tuple[bool, str]:
    """Reference ledger — enter on live momentum; no ultra-elite / breadth wall."""
    runner = candidate.get("runnerSignal") or {}
    metrics = runner.get("metrics") or {}
    vel = float(runner.get("premiumVelocityPct") or metrics.get("premiumVelocity") or 0)
    score = float(runner.get("score") or candidate.get("tqs") or 0)
    min_vel = float(getattr(settings, "paper_reference_min_velocity_pct", 1.0))
    min_score = float(getattr(settings, "paper_reference_min_runner_score", 50.0))
    premium = float(candidate.get("lastPremium") or candidate.get("premium") or runner.get("premium") or 0)
    if premium <= 0:
        return False, "reference gate: missing premium"
    if vel < min_vel and not runner.get("momentumOverride"):
        aligned_grind = (
            runner.get("momentumAligned")
            and score >= min_score
            and vel >= max(0.5, min_vel * 0.5)
        )
        if not aligned_grind:
            return False, f"reference gate: velocity {vel:.1f}% < {min_vel}%"
    if score < min_score and not runner.get("momentumOverride"):
        return False, f"reference gate: score {score:.0f} < {min_score}"
    if vel < min_vel and not (
        runner.get("momentumSurge")
        or runner.get("momentumAligned")
        or runner.get("momentumOverride")
    ):
        return False, "reference gate: no momentum on tape"
    return True, ""


def passes_simple_entry(candidate: dict[str, Any], session_bucket: str, settings: Any) -> tuple[bool, str]:
    if reference_ledger_enabled(settings):
        return passes_reference_entry(candidate, session_bucket, settings)
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


def passes_simple_breadth(candidate: dict[str, Any], breadth: dict[str, Any] | None, settings: Any | None = None) -> tuple[bool, str]:
    if settings is not None and reference_ledger_enabled(settings):
        return True, ""
    if not breadth or not breadth.get("available"):
        return True, ""
    side = str(candidate.get("side") or "").upper()
    if side == "CALL" and not breadth.get("aligned") and str(breadth.get("bias") or "").upper() != "BULLISH":
        return False, f"simple gate: CALL needs bullish breadth (got {breadth.get('bias')})"
    if side == "PUT" and not breadth.get("aligned") and str(breadth.get("bias") or "").upper() != "BEARISH":
        return False, f"simple gate: PUT needs bearish breadth (got {breadth.get('bias')})"
    return True, ""


def simple_lot_bounds(settings: Any, confidence: float | None = None) -> tuple[int, int, int]:
    if reference_ledger_enabled(settings):
        if confidence is not None:
            return reference_confidence_lots(settings, confidence)
        lots = int(getattr(settings, "paper_reference_fixed_lots", 100))
        return lots, lots, lots
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
    """Reference ledger exits: micro lock, trail lock, time profit/stop, no progress, stop loss."""
    unrealized = current - entry
    best_gain = max(0.0, best - entry)
    qty = max(1, int(quantity))
    loss_inr = max(0.0, -unrealized * qty)
    ref = reference_ledger_enabled(settings)

    if ref:
        min_hold = float(getattr(settings, "paper_reference_min_hold_seconds", 15.0))
        max_hold = float(getattr(settings, "paper_reference_max_hold_seconds", 480.0))
        runner_max_hold = float(getattr(settings, "paper_reference_runner_max_hold_seconds", 10000.0))
        runner_target = float(getattr(settings, "paper_reference_runner_target_points", 27.0))
        stop = float(getattr(settings, "paper_reference_stop_points", 3.2))
        micro_target = float(getattr(settings, "paper_reference_micro_arm_points", 3.0))
        micro_trail = float(getattr(settings, "paper_reference_micro_trail_points", 1.5))
        trail_arm = float(getattr(settings, "paper_reference_trail_arm_points", 2.0))
        trail_retain = float(getattr(settings, "paper_reference_trail_retain_pct", 0.50))
        no_progress_age = float(getattr(settings, "paper_reference_no_progress_seconds", 75.0))
        target = float(getattr(settings, "paper_reference_target_points", 12.0))
        emergency_inr = float(getattr(settings, "paper_reference_emergency_loss_inr", 22000.0))
        runner_extend_gain = float(getattr(settings, "paper_reference_runner_extend_best_gain", 8.0))
    else:
        min_hold = float(getattr(settings, "paper_simple_min_hold_seconds", 30.0))
        max_hold = float(getattr(settings, "paper_simple_max_hold_seconds", 180.0))
        runner_max_hold = max_hold
        runner_target = session_target_points(session_bucket, settings)
        stop = float(getattr(settings, "paper_simple_stop_points", 3.0))
        micro_target = float(getattr(settings, "paper_simple_micro_target_points", 3.0))
        micro_trail = float(getattr(settings, "paper_simple_micro_trail_points", 1.25))
        trail_arm = float(getattr(settings, "paper_simple_trail_arm_points", 2.0))
        trail_retain = float(getattr(settings, "paper_simple_trail_retain_pct", 0.50))
        no_progress_age = 120.0
        target = session_target_points(session_bucket, settings)
        emergency_inr = float(getattr(settings, "paper_simple_emergency_loss_inr", 4000.0))
        runner_extend_gain = 999.0

    effective_max_hold = runner_max_hold if ref and best_gain >= runner_extend_gain else max_hold
    runner_mode = ref and best_gain >= runner_extend_gain

    if loss_inr >= emergency_inr:
        return "stop loss" if ref else "simple emergency INR stop"

    if age < min_hold:
        return None

    if ref and age >= no_progress_age and best_gain < 0.5 and unrealized <= 0:
        return "no progress"

    if runner_mode:
        # Explosive PE/CALL runner — wide trailing SL, no early +3pt micro scratch (23950 PE 45→108 style).
        if best_gain >= 40.0:
            dynamic_retain = 0.75
            dynamic_trail = 4.0
        elif best_gain >= 20.0:
            dynamic_retain = 0.65
            dynamic_trail = 3.0
        else:
            dynamic_retain = max(trail_retain, 0.55)
            dynamic_trail = max(micro_trail, 2.5)
        scaled_runner_target = max(runner_target, min(60.0, best_gain * 0.85))
        if unrealized <= -stop:
            return "stop loss"
        floor_price = entry + max(2.0, best_gain * dynamic_retain)
        if current <= floor_price or current <= best - dynamic_trail:
            return "trail lock"
        if unrealized >= scaled_runner_target and best_gain >= 50.0:
            return "profit target hit"
        if age >= effective_max_hold:
            return "time profit" if unrealized > 0 else "time stop"
        return None

    if ref and unrealized >= runner_target:
        return "profit target hit"

    if best_gain >= micro_target and unrealized >= micro_target * 0.35:
        if current <= best - micro_trail or (ref and unrealized <= best_gain * 0.55):
            return "micro profit lock" if ref else "simple micro profit lock"

    if unrealized >= target:
        return "profit target hit" if ref else "simple profit target hit"

    if unrealized <= -stop:
        return "stop loss" if ref else "simple stop loss"

    if best_gain >= trail_arm:
        floor_price = entry + max(1.0, best_gain * trail_retain)
        if current <= floor_price or current <= best - micro_trail:
            return "trail lock" if ref else "simple trail profit lock"

    if age >= effective_max_hold:
        if ref:
            return "time profit" if unrealized > 0 else "time stop"
        return "simple time profit lock" if unrealized > 0 else "simple time stop"

    if not ref and age >= 120 and best_gain < 1.0 and unrealized < -1.0:
        return "simple no-progress scratch"

    return None
