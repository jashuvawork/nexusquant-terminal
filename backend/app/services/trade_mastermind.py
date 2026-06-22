"""Per-trade AI governor — dynamic SL/TP, session-aware Indian index option management.

Monitors each open paper trade every tick and adapts stops, targets, hold time,
and exit mode from live momentum, session bucket, and learner priors.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Indian F&O session behaviour (IST buckets)
SESSION_RUNNER_TARGETS: dict[str, float] = {
    "OPEN_DRIVE": 8.0,
    "NORMAL": 6.0,
    "MIDDAY_CHOP": 4.0,
    "CLOSING_MOMENTUM": 7.0,
}
SESSION_MIN_HOLD: dict[str, float] = {
    "OPEN_DRIVE": 60.0,
    "NORMAL": 45.0,
    "MIDDAY_CHOP": 35.0,
    "CLOSING_MOMENTUM": 50.0,
}
SESSION_MAX_HOLD: dict[str, float] = {
    "OPEN_DRIVE": 420.0,
    "NORMAL": 300.0,
    "MIDDAY_CHOP": 180.0,
    "CLOSING_MOMENTUM": 240.0,
}


@dataclass
class MastermindPlan:
    phase: str  # RUNNER | MICRO_BURST | GRIND | DEFEND | DEAD
    stop_points: float
    target_points: float
    quick_profit_points: float
    cap_points: float
    trail_retain: float
    min_hold_seconds: float
    max_hold_seconds: float
    allow_decay: bool
    allow_micro_partial: bool
    exit_reason: str | None
    note: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "stopPoints": round(self.stop_points, 2),
            "targetPoints": round(self.target_points, 2),
            "quickProfitPoints": round(self.quick_profit_points, 2),
            "capPoints": round(self.cap_points, 2),
            "trailRetain": round(self.trail_retain, 3),
            "minHoldSeconds": round(self.min_hold_seconds, 1),
            "maxHoldSeconds": round(self.max_hold_seconds, 1),
            "allowDecay": self.allow_decay,
            "allowMicroPartial": self.allow_micro_partial,
            "exitReason": self.exit_reason,
            "note": self.note,
        }


def _runner_metrics(candidate: dict[str, Any]) -> dict[str, Any]:
    runner = candidate.get("runnerSignal") or {}
    return runner.get("metrics") or {}


def _tape_signals(candidate: dict[str, Any]) -> dict[str, float]:
    runner = candidate.get("runnerSignal") or {}
    metrics = _runner_metrics(candidate)
    return {
        "premium_velocity": float(runner.get("premiumVelocityPct") or metrics.get("premiumVelocity") or 0),
        "volume_accel": float(runner.get("volumeAcceleration") or metrics.get("volumeAcceleration") or 0),
        "runner_score": float(runner.get("score") or candidate.get("tqs") or 0),
        "delta_velocity": abs(float(metrics.get("deltaVelocity") or 0)),
        "breakout": float(metrics.get("breakoutVelocity") or 0),
    }


def classify_entry_mode(candidate: dict[str, Any], session_bucket: str) -> str:
    """Entry sizing mode: RUNNER (aim 6-8pt) vs MICRO_BURST (2pt, more lots)."""
    tape = _tape_signals(candidate)
    vel = tape["premium_velocity"]
    score = tape["runner_score"]
    surge = bool((candidate.get("runnerSignal") or {}).get("momentumSurge"))
    bucket = str(session_bucket or "NORMAL").upper()
    if bucket == "OPEN_DRIVE" and vel >= 2.0 and score >= 70:
        return "RUNNER"
    if vel >= 2.5 and score >= 75 and surge:
        return "RUNNER"
    if vel >= 1.0 and score >= 65:
        return "GRIND"
    return "MICRO_BURST"


def entry_lot_multiplier(mode: str, *, win_boost: float = 1.0) -> float:
    if mode == "RUNNER":
        return 1.0 * win_boost
    if mode == "MICRO_BURST":
        return 1.45 * win_boost
    return 1.15 * win_boost


def evaluate_trade_mastermind(
    *,
    entry: float,
    current: float,
    best: float,
    age: float,
    session_bucket: str,
    candidate: dict[str, Any],
    acs: dict[str, float],
    learner_state: dict[str, Any] | None,
    regime: str,
    settings: Any,
) -> MastermindPlan:
    """Recompute dynamic SL/TP and exit intent for one open trade."""
    learner_state = learner_state or {}
    unrealized = current - entry
    best_gain = max(0.0, best - entry)
    tape = _tape_signals(candidate)
    vel = tape["premium_velocity"]
    score = tape["runner_score"]
    vol = tape["volume_accel"]
    bucket = str(session_bucket or "NORMAL").upper()
    settings_runner = float(getattr(settings, "paper_mastermind_runner_target_points", 6.0))
    runner_target = float(SESSION_RUNNER_TARGETS.get(bucket, settings_runner))
    if bucket == "NORMAL":
        runner_target = settings_runner
    min_hold = float(SESSION_MIN_HOLD.get(bucket, getattr(settings, "paper_mastermind_min_hold_seconds", 45.0)))
    max_hold = float(SESSION_MAX_HOLD.get(bucket, 300.0))
    base_stop = float(acs.get("stop") or settings.paper_scalp_controlled_stop_points)
    base_cap = float(acs.get("cap") or settings.paper_scalp_runner_cap_points)
    base_quick = float(acs.get("quickProfit") or settings.paper_acs_quick_profit_points)
    retain = float(acs.get("retain") or settings.paper_scalp_runner_retain_pct)
    micro_target = float(getattr(settings, "paper_mastermind_micro_burst_target", 2.0))
    max_stop = float(getattr(settings, "paper_mastermind_max_stop_points", 6.0))
    min_stop = float(getattr(settings, "paper_mastermind_min_stop_points", 2.0))

    prior = (learner_state.get("prior") or {}).get("regimePriors") or {}
    regime_prior = prior.get(regime) or prior.get("RANGE_ABSORPTION") or {}
    trail_bias = float(regime_prior.get("trailBias") or 1.0)

    momentum_index = vel * 0.35 + min(score, 100) * 0.025 + min(vol, 50) * 0.02 + tape["breakout"] * 0.01
    aligned = bool((candidate.get("runnerSignal") or {}).get("momentumAligned"))
    surge = bool((candidate.get("runnerSignal") or {}).get("momentumSurge"))

    # Phase classification
    if momentum_index >= 3.5 and vel >= 1.8 and (surge or aligned):
        phase = "RUNNER"
    elif momentum_index >= 1.8 and vel >= 1.0:
        phase = "GRIND"
    elif momentum_index < 0.8 or (age >= 25 and best_gain < 0.2):
        phase = "DEAD"
    elif vel < 0.6 and best_gain < 1.0:
        phase = "DEFEND"
    else:
        phase = "MICRO_BURST"

    # Dynamic target — RUNNER aims for session target (e.g. 6pt), not premature 2pt book
    if phase == "RUNNER":
        target = max(runner_target, base_cap * 0.85)
        quick = max(base_quick, target * 0.55)
        cap = max(target, base_cap, runner_target * 1.15)
        trail_retain = min(0.78, retain * trail_bias * 1.05)
        allow_decay = False
        allow_micro = False
    elif phase == "MICRO_BURST":
        target = micro_target
        quick = micro_target
        cap = max(micro_target * 1.5, base_quick)
        trail_retain = 0.55
        allow_decay = vel < 0.5
        allow_micro = True
    elif phase == "GRIND":
        target = max(base_quick, runner_target * 0.65)
        quick = float(acs.get("microPartial") or settings.paper_scalp_micro_partial_points)
        cap = max(target, base_cap * 0.7)
        trail_retain = retain
        allow_decay = best_gain < quick * 0.8
        allow_micro = True
    elif phase == "DEFEND":
        target = max(1.5, base_quick * 0.8)
        quick = target
        cap = base_cap * 0.6
        trail_retain = 0.6
        allow_decay = True
        allow_micro = True
    else:  # DEAD
        target = base_quick
        quick = base_quick
        cap = base_cap
        trail_retain = retain
        allow_decay = True
        allow_micro = False

    # Dynamic stop — wide when running toward target, tight only when tape dies
    if age < min_hold and unrealized > -max_stop * 0.5:
        stop = max_stop
    elif phase == "RUNNER" and unrealized >= 0:
        stop = max(min_stop, target * 0.7, max_stop * 0.85)
    elif phase == "RUNNER" and best_gain >= target * 0.5:
        stop = max(min_stop, best_gain * 0.45)
    elif vel >= 1.5 and unrealized >= 0:
        stop = max(base_stop, target * 0.55, 3.5)
    elif vel < 0.5 and unrealized < 0:
        stop = min_stop
    elif vel < 0.8 and best_gain >= 1.5 and unrealized < best_gain * 0.5:
        stop = max(min_stop, best_gain * 0.35)
    else:
        stop = max(min_stop, min(base_stop, max_stop))

    # Extend hold when momentum supports the runner target
    if phase == "RUNNER" and vel >= 1.5:
        max_hold = max(max_hold, min_hold + 180)
    if phase == "DEAD" and age >= 40:
        max_hold = min(max_hold, age + 15)

    exit_reason: str | None = None
    note = f"{phase} vel={vel:.1f}% score={score:.0f} target={target:.1f} stop={stop:.1f}"

    # Mastermind exit calls (before fixed ACS decay)
    if age >= min_hold:
        if phase == "RUNNER" and current >= entry + cap:
            exit_reason = "mastermind runner cap target"
        elif phase == "RUNNER" and vel < 0.8 and best_gain >= target * 0.7 and unrealized <= best_gain * 0.55:
            exit_reason = "mastermind runner momentum fade — profit locked"
        elif phase == "MICRO_BURST" and unrealized >= micro_target:
            exit_reason = "mastermind micro burst target hit"
        elif phase in {"DEFEND", "DEAD"} and best_gain >= 1.0 and (best_gain - unrealized) >= 0.6:
            exit_reason = "mastermind peak giveback exit"
        elif phase == "DEAD" and age >= max_hold * 0.9:
            exit_reason = "mastermind dead tape time exit"
    elif unrealized <= -stop:
        exit_reason = "mastermind grace stop — deep loss before min hold"

    return MastermindPlan(
        phase=phase,
        stop_points=round(stop, 2),
        target_points=round(target, 2),
        quick_profit_points=round(quick, 2),
        cap_points=round(cap, 2),
        trail_retain=round(trail_retain, 3),
        min_hold_seconds=min_hold,
        max_hold_seconds=max_hold,
        allow_decay=allow_decay,
        allow_micro_partial=allow_micro,
        exit_reason=exit_reason,
        note=note,
    )
