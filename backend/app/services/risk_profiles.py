from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time
from typing import Any

from app.services.session import IST, MarketPhase


@dataclass(frozen=True)
class RiskProfile:
    key: str
    label: str
    minimum_tqs: int
    safe_mode_tqs: int
    max_exposure_pct: int
    daily_drawdown_pct: float
    cooldown_seconds: int
    behavior: str
    account_size: str


PROFILES: dict[str, RiskProfile] = {
    "safe_beginner": RiskProfile("safe_beginner", "Safe Beginner", 86, 92, 18, 2.0, 60, "Highest-quality setups only; low overtrading.", "INR 5k-25k"),
    "balanced_pro": RiskProfile("balanced_pro", "Balanced Pro", 76, 86, 32, 3.0, 25, "Best default: consistent scalping with controlled risk.", "INR 25k-2L"),
    "aggressive_scalping": RiskProfile("aggressive_scalping", "Aggressive Scalping", 68, 82, 45, 4.5, 10, "Fast momentum scalps; higher trade frequency and risk.", "Experienced intraday"),
    "extreme_prop": RiskProfile("extreme_prop", "Extreme Prop-Desk", 62, 78, 65, 7.0, 3, "Hyper aggressive; prone to overtrading. Not recommended initially.", "Prop/advanced only"),
    "realistic_aggressive": RiskProfile("realistic_aggressive", "Realistic Aggressive", 70, 84, 38, 3.0, 12, "Recommended aggressive default for repeated 5-point captures.", "Most aggressive users"),
}

SESSION_NOTES = {
    "OPEN_DRIVE": "9:15-10:30 IST: strongest momentum; lower TQS and shorter cooldown if liquidity/spread are good.",
    "MIDDAY_CHOP": "11:30-13:30 IST: fake moves common; raise TQS and cooldown.",
    "CLOSING_MOMENTUM": "14:30-15:15 IST: trend continuation window; use moderate aggression.",
    "PREMARKET": "Pre-market: analysis only; build levels and bias, no F&O scalps.",
    "CLOSED": "Closed market: backtest and tomorrow plan only.",
    "NORMAL": "Normal session: use selected profile unless regime says otherwise.",
}


def profile_list() -> list[dict[str, Any]]:
    return [asdict(profile) for profile in PROFILES.values()]


def get_profile(key: str | None) -> RiskProfile:
    normalized = (key or "realistic_aggressive").strip().lower()
    return PROFILES.get(normalized, PROFILES["realistic_aggressive"])


def session_bucket(phase: MarketPhase, now: datetime | None = None) -> str:
    current = (now or datetime.now(IST)).astimezone(IST).time()
    if phase == MarketPhase.PRE_MARKET_ANALYSIS:
        return "PREMARKET"
    if phase != MarketPhase.LIVE_MARKET:
        return "CLOSED"
    if time(9, 15) <= current <= time(10, 30):
        return "OPEN_DRIVE"
    if time(11, 30) <= current <= time(13, 30):
        return "MIDDAY_CHOP"
    if time(14, 30) <= current <= time(15, 15):
        return "CLOSING_MOMENTUM"
    return "NORMAL"


def adaptive_settings(profile_key: str | None, phase: MarketPhase, regime: str, tqs: int) -> dict[str, Any]:
    base = get_profile(profile_key)
    minimum_tqs = base.minimum_tqs
    safe_mode_tqs = base.safe_mode_tqs
    max_exposure = base.max_exposure_pct
    daily_drawdown = base.daily_drawdown_pct
    cooldown = base.cooldown_seconds
    bucket = session_bucket(phase)
    adjustments: list[str] = []

    if bucket == "OPEN_DRIVE":
        minimum_tqs = max(58, minimum_tqs - 4)
        cooldown = max(5, min(cooldown, 10))
        adjustments.append("Open drive: lower TQS and shorter cooldown for momentum bursts.")
    elif bucket == "MIDDAY_CHOP":
        minimum_tqs = max(minimum_tqs, 82 if base.key in {"aggressive_scalping", "extreme_prop"} else 78)
        safe_mode_tqs = max(safe_mode_tqs, minimum_tqs + 6)
        cooldown = max(cooldown, 45)
        max_exposure = min(max_exposure, 25)
        adjustments.append("Midday chop: higher TQS, lower exposure, longer cooldown.")
    elif bucket == "CLOSING_MOMENTUM":
        minimum_tqs = max(70, min(minimum_tqs, 74))
        cooldown = max(10, min(cooldown, 15))
        adjustments.append("Closing momentum: moderate TQS and cooldown for continuation.")
    elif bucket in {"PREMARKET", "CLOSED"}:
        cooldown = max(cooldown, 60)
        max_exposure = 0
        adjustments.append("Analysis-only session: no live F&O execution.")

    if regime in {"REVERSAL_RISK", "CLOSED_MARKET_ANALYSIS"}:
        minimum_tqs = max(minimum_tqs, 84)
        safe_mode_tqs = max(safe_mode_tqs, 92)
        max_exposure = min(max_exposure, 15)
        cooldown = max(cooldown, 60)
        adjustments.append("Risk/chop regime: raise thresholds and reduce exposure.")
    elif regime == "TREND_EXPANSION" and tqs >= 85 and bucket in {"OPEN_DRIVE", "CLOSING_MOMENTUM", "NORMAL"}:
        max_exposure = min(70, max(max_exposure, base.max_exposure_pct + 5))
        cooldown = max(5, cooldown - 5)
        adjustments.append("Trend expansion with high TQS: allow dynamic exposure increase.")

    return {
        "profile": asdict(base),
        "sessionBucket": bucket,
        "sessionNote": SESSION_NOTES[bucket],
        "minimumTqs": int(minimum_tqs),
        "safeModeTqs": int(max(safe_mode_tqs, minimum_tqs + 4)),
        "maxExposurePct": int(max_exposure),
        "dailyDrawdownPct": float(daily_drawdown),
        "cooldownSeconds": int(cooldown),
        "dynamicExposurePct": int(max_exposure if tqs >= minimum_tqs else max(0, min(max_exposure, 15))),
        "adjustments": adjustments,
        "benchmarks": {
            "minimumTrades": 300,
            "professionalTrades": "500-1000",
            "targetWinRatePct": "58-68",
            "targetProfitFactor": "1.8-2.5",
            "maxDrawdownGoalPct": 8,
            "excellentDrawdownPct": 5,
            "targetTradesPerDay": "15-40",
            "maxConsecutiveLosses": 6,
        },
    }


def paper_session_adjustments(
    profile_key: str | None,
    phase: MarketPhase,
    regime: str,
    *,
    base_min_tqs: int,
    base_runner_score: float,
    base_allocation_pct: float,
    base_duplicate_cooldown: int,
    base_target_points: float,
    base_stop_points: float,
    base_max_hold_seconds: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Time-of-day paper trading gates aligned with Indian market session buckets."""
    bucket = session_bucket(phase, now)
    adjustments: list[str] = []
    min_entry_tqs = int(base_min_tqs)
    min_runner_score = float(base_runner_score)
    allocation_multiplier = 1.0
    duplicate_cooldown = int(base_duplicate_cooldown)
    target_multiplier = 1.0
    stop_multiplier = 1.0
    max_hold_seconds = int(base_max_hold_seconds)
    block_new_paper = False
    block_reason: str | None = None
    midday_runner_bypass_score = 90.0

    if bucket == "OPEN_DRIVE":
        min_entry_tqs = max(58, min_entry_tqs - 4)
        min_runner_score = max(75.0, min_runner_score - 5.0)
        allocation_multiplier = 1.15
        duplicate_cooldown = max(120, int(duplicate_cooldown * 0.6))
        target_multiplier = 1.05
        max_hold_seconds = min(max_hold_seconds + 30, 240)
        adjustments.append("Open drive: lower entry gates, slightly larger size, extend holds for momentum.")
    elif bucket == "MIDDAY_CHOP":
        min_entry_tqs = max(min_entry_tqs + 6, 84)
        min_runner_score = max(min_runner_score + 5.0, 88.0)
        allocation_multiplier = 0.55
        duplicate_cooldown = max(duplicate_cooldown, 360)
        target_multiplier = 0.9
        stop_multiplier = 0.85
        max_hold_seconds = min(max_hold_seconds, 120)
        block_new_paper = True
        block_reason = "Midday chop window: paper entries paused unless runner score >= 90 with chart alignment."
        adjustments.append("Midday chop: block new paper trades except A+ runners; tighter stops and shorter holds.")
    elif bucket == "CLOSING_MOMENTUM":
        min_entry_tqs = max(70, min_entry_tqs)
        min_runner_score = max(80.0, min_runner_score - 2.0)
        allocation_multiplier = 1.0
        duplicate_cooldown = max(180, int(duplicate_cooldown * 0.8))
        target_multiplier = 1.0
        max_hold_seconds = min(max_hold_seconds, 180)
        adjustments.append("Closing momentum: moderate gates for continuation setups.")
    elif bucket == "NORMAL":
        adjustments.append("Normal session: use configured high-PF thresholds.")
    elif bucket in {"PREMARKET", "CLOSED"}:
        block_new_paper = True
        block_reason = "Outside live F&O session: analysis only, no new paper entries."
        allocation_multiplier = 0.0
        adjustments.append("Closed/pre-market: paper entries disabled.")

    if regime in {"REVERSAL_RISK", "CLOSED_MARKET_ANALYSIS"}:
        min_entry_tqs = max(min_entry_tqs, 84)
        min_runner_score = max(min_runner_score, 90.0)
        allocation_multiplier = min(allocation_multiplier, 0.5)
        block_new_paper = True
        block_reason = block_reason or "Risk regime: reversal/chop risk elevated; paper entries paused."
        adjustments.append("Risk regime: tighten gates and pause new paper entries.")
    elif regime == "TREND_EXPANSION" and bucket in {"OPEN_DRIVE", "CLOSING_MOMENTUM", "NORMAL"}:
        allocation_multiplier = min(1.25, allocation_multiplier * 1.1)
        target_multiplier = max(target_multiplier, 1.05)
        adjustments.append("Trend expansion: modest size/target boost in active session windows.")

    return {
        "sessionBucket": bucket,
        "sessionNote": SESSION_NOTES[bucket],
        "blockNewPaperTrades": block_new_paper,
        "blockReason": block_reason,
        "middayRunnerBypassScore": midday_runner_bypass_score,
        "minEntryTqs": int(min_entry_tqs),
        "minRunnerScore": round(min_runner_score, 2),
        "allocationPctMultiplier": round(allocation_multiplier, 3),
        "effectiveAllocationPct": round(max(0.0, base_allocation_pct * allocation_multiplier), 2),
        "duplicateCooldownSeconds": int(duplicate_cooldown),
        "targetPointsMultiplier": round(target_multiplier, 3),
        "stopPointsMultiplier": round(stop_multiplier, 3),
        "maxHoldSeconds": int(max_hold_seconds),
        "adjustments": adjustments,
    }
