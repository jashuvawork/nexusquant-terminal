"""Daily profit-first strategy: rolling calibration to raise win rate and profit factor."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.session import IST


def _pf(summary: dict[str, Any]) -> float:
    return float(summary.get("profitFactor") or 0)


def _win_rate(summary: dict[str, Any]) -> float:
    return float(summary.get("winRate") or 0)


def build_daily_improvement_plan(
    *,
    today_summary: dict[str, Any],
    rolling_summary: dict[str, Any],
    by_side: dict[str, dict[str, Any]],
    by_bucket: dict[str, dict[str, Any]],
    by_symbol: dict[str, dict[str, Any]],
    missed_count: int,
    target_profit_factor: float = 1.5,
    target_win_rate_pct: float = 45.0,
    min_trades_for_calibration: int = 8,
    unified_scalp_session_profile: bool = True,
) -> dict[str, Any]:
    """Produce actionable gates for today based on recent paper performance."""
    rolling_trades = int(rolling_summary.get("paperTrades") or rolling_summary.get("trades") or 0)
    rolling_pf = _pf(rolling_summary)
    rolling_wr = _win_rate(rolling_summary)
    today_pf = _pf(today_summary)
    today_wr = _win_rate(today_summary)
    today_trades = int(today_summary.get("paperTrades") or today_summary.get("trades") or 0)

    # Probability estimates (honest model — not guarantees)
    if rolling_trades < 3:
        capture_est = 88
        win_est = 38
        pf_est = 1.0
        phase = "LEARNING"
    elif rolling_pf >= target_profit_factor and rolling_wr >= target_win_rate_pct:
        capture_est = 82
        win_est = min(58, rolling_wr + 5)
        pf_est = rolling_pf
        phase = "PROFITABLE"
    elif rolling_pf >= 1.0:
        capture_est = 78
        win_est = max(40, rolling_wr)
        pf_est = rolling_pf
        phase = "RECOVERING"
    else:
        capture_est = 72
        win_est = max(28, rolling_wr)
        pf_est = max(0.2, rolling_pf)
        phase = "DEFENSIVE"

    min_runner_score = 92.0
    min_velocity_pct = 3.0
    min_volume_accel = 30.0
    allow_tier_b = False
    allow_tier_c = False
    blocked_sides: list[str] = []
    blocked_buckets: list[str] = []
    actions: list[str] = []

    if rolling_trades >= min_trades_for_calibration:
        if rolling_pf < 1.0:
            min_runner_score = 80.0
            min_velocity_pct = 2.5
            min_volume_accel = 35.0
            allow_tier_b = False
            allow_tier_c = False
            actions.append(f"Rolling PF {rolling_pf:.2f} < 1.0 — A+ entries only (score≥80, vel≥2.5%).")
        elif rolling_pf < target_profit_factor:
            min_runner_score = 76.0
            min_velocity_pct = 2.0
            allow_tier_b = True
            allow_tier_c = False
            actions.append(f"Rolling PF {rolling_pf:.2f} below target {target_profit_factor} — A/A+ only.")
        else:
            min_runner_score = 88.0
            min_velocity_pct = 2.5
            allow_tier_b = False
            allow_tier_c = False
            actions.append(f"Edge proven (PF {rolling_pf:.2f}) — elite runners only (score≥88).")

        if rolling_wr < 35:
            min_runner_score = max(min_runner_score, 78.0)
            actions.append(f"Win rate {rolling_wr:.1f}% low — raise runner floor.")

        for side, summary in by_side.items():
            if int(summary.get("paperTrades") or summary.get("trades") or 0) >= 5:
                if float(summary.get("netPnl") or 0) < 0 and _pf(summary) < 0.9:
                    blocked_sides.append(side)
                    actions.append(f"Block {side} today — PF {_pf(summary):.2f}, net ₹{summary.get('netPnl')}.")

        weak_bucket = "OPEN_DRIVE" if unified_scalp_session_profile else "MIDDAY_CHOP"
        for bucket, summary in by_bucket.items():
            if bucket == weak_bucket and int(summary.get("paperTrades") or 0) >= 4:
                if _pf(summary) < 1.0:
                    blocked_buckets.append(bucket)
                    label = "Open drive" if unified_scalp_session_profile else "Midday chop"
                    actions.append(f"{label} underperforming — require A+ override only in {bucket}.")

    if today_trades >= 2 and today_pf < 0.8 and float(today_summary.get("netPnl") or 0) < -5000:
        min_runner_score = max(min_runner_score, 82.0)
        allow_tier_b = False
        allow_tier_c = False
        actions.append("Today losing — tighten to elite momentum only until recovery.")

    if today_trades >= 4 and today_wr < 30 and float(today_summary.get("netPnl") or 0) < 0:
        min_runner_score = max(min_runner_score, 85.0)
        allow_tier_b = False
        allow_tier_c = False
        weak_bucket = "OPEN_DRIVE" if unified_scalp_session_profile else "MIDDAY_CHOP"
        blocked_buckets.append(weak_bucket)
        label = "open drive" if unified_scalp_session_profile else "midday chop"
        actions.append(f"Today win rate {today_wr:.1f}% — block weak tiers and {label}.")

    if missed_count > 30 and rolling_pf >= target_profit_factor:
        actions.append(f"{missed_count} near-misses logged — edge OK; selective capture preferred over blind volume.")
    elif missed_count > 30 and rolling_pf >= 1.0 and rolling_trades >= min_trades_for_calibration:
        actions.append(f"{missed_count} near-misses logged — elite-only mode; no tier loosening.")

    if not actions:
        actions.append("Collect more paper trades (target 8+) for full daily calibration.")

    return {
        "tradingDay": datetime.now(IST).date().isoformat(),
        "phase": phase,
        "probabilityEstimate": {
            "capturePct": capture_est,
            "winRatePct": round(win_est, 1),
            "profitFactor": round(pf_est, 2),
            "note": "Estimates blend rolling paper stats with current gate strictness; not a guarantee.",
        },
        "targets": {
            "profitFactor": target_profit_factor,
            "winRatePct": target_win_rate_pct,
        },
        "rolling": {
            "trades": rolling_trades,
            "profitFactor": round(rolling_pf, 3),
            "winRatePct": round(rolling_wr, 2),
            "netPnl": round(float(rolling_summary.get("netPnl") or 0), 2),
        },
        "today": {
            "trades": today_trades,
            "profitFactor": round(today_pf, 3),
            "winRatePct": round(today_wr, 2),
            "netPnl": round(float(today_summary.get("netPnl") or 0), 2),
        },
        "gates": {
            "minRunnerScore": min_runner_score,
            "minVelocityPct": min_velocity_pct,
            "minVolumeAccel": min_volume_accel,
            "allowTierB": allow_tier_b,
            "allowTierC": allow_tier_c,
            "blockedSides": blocked_sides,
            "blockedBuckets": blocked_buckets,
        },
        "dailyActions": actions,
    }
