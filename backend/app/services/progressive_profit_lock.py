"""Progressive intraday profit lock — trade toward tier targets, stop if PnL breaches the floor."""
from __future__ import annotations

from typing import Any


def _parse_float_list(raw: str, *, fallback: list[float]) -> list[float]:
    try:
        values = [float(item.strip()) for item in str(raw or "").split(",") if item.strip()]
        return values or list(fallback)
    except (TypeError, ValueError):
        return list(fallback)


def build_progressive_tiers(capital: float, settings: Any) -> list[dict[str, Any]]:
    minimum_inr = float(getattr(settings, "paper_profit_lock_minimum_inr", 25000.0))
    tier_pcts = _parse_float_list(
        str(getattr(settings, "paper_profit_lock_tier_pcts", "22,55,78,112")),
        fallback=[22.0, 55.0, 78.0, 112.0],
    )
    tier_retain = _parse_float_list(
        str(getattr(settings, "paper_profit_lock_tier_retain_pcts", "50,75,100,100")),
        fallback=[50.0, 75.0, 100.0, 100.0],
    )
    minimum_retain = float(getattr(settings, "paper_profit_lock_minimum_retain_pct", 100.0))

    tiers: list[dict[str, Any]] = [
        {
            "name": "minimum_inr",
            "label": "₹25k minimum",
            "targetAmount": round(minimum_inr, 2),
            "targetPct": round(minimum_inr / capital * 100, 2) if capital > 0 else 0.0,
            "retainPct": minimum_retain,
            "kind": "inr",
        }
    ]
    for index, pct in enumerate(tier_pcts):
        retain = tier_retain[index] if index < len(tier_retain) else 100.0
        amount = capital * pct / 100 if capital > 0 else 0.0
        tiers.append(
            {
                "name": f"tier_{int(pct)}pct",
                "label": f"{pct:g}% capital",
                "targetAmount": round(amount, 2),
                "targetPct": round(pct, 2),
                "retainPct": retain,
                "kind": "pct",
            }
        )
    return tiers


def progressive_profit_lock_status(
    *,
    capital: float,
    net_pnl: float,
    peak_net_pnl: float,
    settings: Any,
    session_id: str | None = None,
    session_number: int | None = None,
) -> dict[str, Any]:
    """Return lock state: ratchet floor from peak PnL, allow trading above floor toward next tier."""
    enabled = bool(getattr(settings, "paper_progressive_profit_lock_enabled", True))
    tiers = build_progressive_tiers(capital, settings)
    evaluation_peak = max(float(net_pnl), float(peak_net_pnl))

    achieved: list[dict[str, Any]] = []
    locked_profit = 0.0
    for tier in tiers:
        target = float(tier["targetAmount"])
        if evaluation_peak >= target:
            floor = target * float(tier["retainPct"]) / 100
            achieved.append({**tier, "lockedFloor": round(floor, 2)})
            locked_profit = max(locked_profit, floor)

    active = achieved[-1] if achieved else None
    next_tier = next((tier for tier in tiers if float(tier["targetAmount"]) > evaluation_peak + 0.01), None)

    giveback = max(0.0, float(net_pnl) - locked_profit) if locked_profit > 0 else max(0.0, float(net_pnl))
    block_new = bool(enabled and locked_profit > 0 and float(net_pnl) <= locked_profit + 0.01)

    if not enabled:
        message = "Progressive profit lock disabled"
    elif not active:
        message = f"Trading toward ₹{tiers[0]['targetAmount']:,.0f} minimum lock"
    elif block_new:
        message = f"Profit floor ₹{locked_profit:,.0f} reached — no new entries until recovery above floor"
    elif next_tier:
        remaining = float(next_tier["targetAmount"]) - float(net_pnl)
        message = (
            f"{active['label']} floor locked at ₹{locked_profit:,.0f}; "
            f"trading toward {next_tier['label']} (₹{next_tier['targetAmount']:,.0f}, ₹{remaining:,.0f} remaining)"
        )
    else:
        message = f"Final tier {active['label']} locked at ₹{locked_profit:,.0f}"

    tier_rows = []
    for tier in tiers:
        target = float(tier["targetAmount"])
        tier_rows.append(
            {
                **tier,
                "achieved": evaluation_peak >= target,
                "lockedFloor": round(target * float(tier["retainPct"]) / 100, 2) if evaluation_peak >= target else 0.0,
                "remaining": round(max(0.0, target - float(net_pnl)), 2),
            }
        )

    return {
        "enabled": enabled,
        "capital": round(capital, 2),
        "netPnl": round(float(net_pnl), 2),
        "peakNetPnl": round(evaluation_peak, 2),
        "tiers": tier_rows,
        "achievedTiers": achieved,
        "activeTier": active,
        "nextTier": next_tier,
        "lockedProfit": round(locked_profit, 2),
        "givebackAvailable": round(giveback, 2),
        "blockNewTrades": block_new,
        "message": message,
        "sessionId": session_id,
        "sessionNumber": session_number,
        "mode": "progressive_inr_then_capital_pct",
    }
