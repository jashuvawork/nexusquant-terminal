from __future__ import annotations

from typing import Any


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


class ExplosiveRunnerEngine:
    """Detects rare option premium expansion opportunities."""

    def __init__(self, option_premium_history_available: bool = False) -> None:
        self.option_premium_history_available = option_premium_history_available

    REQUIRED_DATA = [
        "option premium LTP",
        "option chain volume/OI",
        "bid/ask spread",
        "Greeks delta/gamma/theta/vega",
        "IV expansion",
        "underlying momentum",
        "market profile/opening range",
    ]
    IDEAL_DATA = [
        "historical option premium candles",
        "tick-level option trades",
        "level-2 DOM depth",
        "trade aggressor side",
        "multi-strike gamma exposure history",
    ]

    def evaluate(
        self,
        *,
        symbol: str,
        side: str,
        strike: int,
        expiry: str,
        instrument_key: str | None,
        premium: float,
        selected_md: dict[str, Any],
        greeks: dict[str, Any],
        orderflow: dict[str, Any],
        spread_quality: int,
        volume_state: dict[str, Any],
        heatmap: list[dict[str, Any]],
        market_profile: dict[str, Any],
        entry_model: dict[str, Any],
        tqs: int,
    ) -> dict[str, Any]:
        volume = _num(selected_md.get("volume")) or _num(volume_state.get("effectiveVolume"))
        oi = _num(selected_md.get("oi"))
        prev_oi = _num(selected_md.get("prev_oi"))
        oi_change_pct = ((oi - prev_oi) / prev_oi * 100) if prev_oi else 0.0
        delta = abs(_num(greeks.get("delta")))
        gamma = abs(_num(greeks.get("gamma")))
        iv_expansion = _num(greeks.get("ivExpansion"))
        breakout = _num(orderflow.get("breakoutVelocity"))
        delta_velocity = abs(_num(orderflow.get("deltaVelocity")))
        volume_accel = _num(orderflow.get("volumeAcceleration"))
        gamma_walls = [cell for cell in heatmap if _num(cell.get("gammaWall")) >= 70]
        near_profile_edge = premium > 0 and (market_profile.get("vah") != market_profile.get("val"))

        score = 0.0
        reasons: list[str] = []
        if premium > 0:
            score += 10
        if spread_quality >= 85:
            score += 12
            reasons.append("spread tradable")
        if volume_accel >= 70 or volume > 0:
            score += 12
            reasons.append("volume/participation available")
        if breakout >= 65:
            score += 15
            reasons.append("breakout velocity strong")
        if delta_velocity >= 60:
            score += 15
            reasons.append("delta velocity strong")
        if delta >= 0.45:
            score += 10
            reasons.append("delta responsive")
        if gamma >= 0.001:
            score += 8
            reasons.append("gamma convexity present")
        if iv_expansion >= 40:
            score += 8
            reasons.append("IV expansion supportive")
        if oi_change_pct > 5:
            score += 5
            reasons.append("OI expansion")
        if gamma_walls:
            score += 3
            reasons.append("gamma wall context")
        if entry_model.get("retestConfirmed"):
            score += 2
            reasons.append("retest confirmed")

        missing_ideal = [item for item in self.IDEAL_DATA if not (item == "historical option premium candles" and self.option_premium_history_available)]
        ideal_available = ["historical option premium candles"] if self.option_premium_history_available else []
        confidence = "LOW"
        if score >= 75 and tqs >= 70:
            confidence = "HIGH"
        elif score >= 55 and tqs >= 60:
            confidence = "MEDIUM"

        candidate = confidence in {"MEDIUM", "HIGH"} and premium > 0 and spread_quality >= 75
        target_pct = 33 if confidence == "HIGH" else 22 if confidence == "MEDIUM" else 11
        hard_stop_pct = 12 if confidence == "HIGH" else 8
        trail_pct = 18 if confidence == "HIGH" else 12
        partial_pct = 0.35 if confidence == "HIGH" else 0.5

        return {
            "strategyType": "EXPLOSIVE_RUNNER",
            "candidate": candidate,
            "confidence": confidence,
            "score": round(min(score, 100), 2),
            "symbol": symbol,
            "side": side,
            "strike": strike,
            "expiry": expiry,
            "instrumentKey": instrument_key,
            "premium": premium,
            "targetPremiumPct": target_pct,
            "hardStopPct": hard_stop_pct,
            "trailPct": trail_pct,
            "partialExitPct": partial_pct,
            "runnerPct": round(1 - partial_pct, 2),
            "reasons": reasons,
            "dataStatus": {
                "requiredAvailable": self.REQUIRED_DATA,
                "idealAvailable": ideal_available,
                "idealMissing": missing_ideal,
                "trainingMode": "exact_option_premium_history_available" if self.option_premium_history_available else "exact_live_current_snapshot_proxy_historical_until_option_premium_history_available",
            },
            "metrics": {
                "volume": volume,
                "oi": oi,
                "prevOi": prev_oi,
                "oiChangePct": round(oi_change_pct, 2),
                "delta": delta,
                "gamma": gamma,
                "ivExpansion": iv_expansion,
                "breakoutVelocity": breakout,
                "deltaVelocity": delta_velocity,
                "volumeAcceleration": volume_accel,
                "spreadQuality": spread_quality,
            },
        }
