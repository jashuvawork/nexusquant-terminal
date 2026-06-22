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
        chart_bias: str | None = None,
        option_direction: str | None = None,
        momentum_premium_velocity_pct: float = 5.0,
        momentum_override_velocity_pct: float = 10.0,
        momentum_override_volume_accel: float = 25.0,
        explosion_velocity_pct: float = 5.0,
        explosion_volume_accel: float = 35.0,
        max_catch_mode: bool = False,
        explosion_min_premium: float = 25.0,
        vertical_surge_velocity_pct: float = 6.0,
        elite_min_score: float = 92.0,
        elite_breakout_min: float = 70.0,
        elite_delta_velocity_min: float = 55.0,
        elite_spread_min: float = 78.0,
    ) -> dict[str, Any]:
        volume = _num(selected_md.get("volume")) or _num(volume_state.get("effectiveVolume"))
        oi = _num(selected_md.get("oi"))
        prev_oi = _num(selected_md.get("prev_oi"))
        oi_change_pct = ((oi - prev_oi) / prev_oi * 100) if prev_oi else 0.0
        delta = abs(_num(greeks.get("delta")))
        gamma = abs(_num(greeks.get("gamma")))
        iv_expansion = _num(greeks.get("ivExpansion"))
        breakout = _num(orderflow.get("breakoutVelocity"))
        delta_velocity = _num(orderflow.get("deltaVelocity"))
        volume_accel = _num(orderflow.get("volumeAcceleration"))
        premium_velocity = _num(orderflow.get("premiumVelocity"))
        price_velocity = _num(orderflow.get("priceVelocity"))
        gamma_walls = [cell for cell in heatmap if _num(cell.get("gammaWall")) >= 70]
        chart_bias = str(chart_bias or "WAIT").upper()
        option_direction = str(option_direction or "NEUTRAL").upper()

        score = 0.0
        reasons: list[str] = []
        if premium > 0:
            score += 10
        if spread_quality >= 85:
            score += 12
            reasons.append("spread tradable")
        elif spread_quality >= 70:
            score += 6
            reasons.append("spread acceptable for momentum")
        if volume_accel >= 70 or volume > 0:
            score += 12
            reasons.append("volume/participation available")
        if breakout >= 65:
            score += 15
            reasons.append("breakout velocity strong")
        if delta_velocity >= 60:
            score += 15
            reasons.append("delta velocity strong")
        elif delta_velocity >= 35:
            score += 8
            reasons.append("delta velocity building")
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

        # Standard momentum surge — max-catch uses 1.5% velocity floor
        surge_vel = 1.5 if max_catch_mode else momentum_premium_velocity_pct
        momentum_surge = (
            premium_velocity >= surge_vel
            or (breakout >= 55 and volume_accel >= 20)
            or (premium_velocity >= 1.5 and breakout >= 50 and delta_velocity >= 25)
            or (breakout >= 58 and abs(delta_velocity) >= 45 and spread_quality >= 75)
        )
        # MOMENTUM OVERRIDE: catch explosions early and gradual breakouts
        override_vel = 1.5 if max_catch_mode else momentum_override_velocity_pct
        override_vol = 15.0 if max_catch_mode else momentum_override_volume_accel
        momentum_override = (
            premium_velocity >= override_vel
            and volume_accel >= override_vol
            and spread_quality >= 50
            and premium > 0
        ) or (
            premium_velocity >= 1.5
            and volume_accel >= 15
            and breakout >= 45
            and spread_quality >= 50
            and premium >= explosion_min_premium
        ) or (
            premium_velocity >= 3.0
            and volume_accel >= 30
            and breakout >= 55
            and spread_quality >= 58
            and premium >= explosion_min_premium
        ) or (
            premium_velocity >= 6.0
            and volume_accel >= max(50.0, momentum_override_volume_accel * 2)
            and (price_velocity >= 0.05 or price_velocity <= -0.05)
            and spread_quality >= 65
        ) or (
            premium_velocity >= vertical_surge_velocity_pct
            and volume_accel >= 12
            and spread_quality >= 45
            and premium >= explosion_min_premium
        ) or (
            premium_velocity >= explosion_velocity_pct
            and volume_accel >= explosion_volume_accel
            and momentum_surge
            and spread_quality >= 50
            and premium >= explosion_min_premium
        )
        if momentum_override:
            score += min(30, 15 + premium_velocity * 1.5)
            reasons.append(f"MOMENTUM OVERRIDE: premium velocity {premium_velocity:.1f}% + volume surge")
            momentum_surge = True
        elif premium_velocity >= momentum_premium_velocity_pct:
            score += min(22, 10 + premium_velocity * 1.2)
            reasons.append(f"premium surge {premium_velocity:.1f}%")
        if price_velocity >= 0.08 and side == "CALL":
            score += 6
            reasons.append("underlying bullish impulse")
        elif price_velocity <= -0.08 and side == "PUT":
            score += 6
            reasons.append("underlying bearish impulse")

        bullish_chart = chart_bias in {"CALL", "BULLISH", "BULLISH_TREND"}
        bearish_chart = chart_bias in {"PUT", "BEARISH", "BEARISH_TREND"}
        bullish_option = option_direction == "BULLISH"
        bearish_option = option_direction == "BEARISH"

        if side == "CALL" and (bullish_chart or bullish_option or delta_velocity > 25):
            directional_bias = "BULLISH"
            momentum_aligned = bullish_chart or bullish_option or (momentum_surge and delta_velocity > 20)
            if momentum_aligned:
                score += 10
                reasons.append("bullish momentum alignment")
        elif side == "PUT" and (bearish_chart or bearish_option or delta_velocity < -25):
            directional_bias = "BEARISH"
            momentum_aligned = bearish_chart or bearish_option or (momentum_surge and delta_velocity < -20)
            if momentum_aligned:
                score += 10
                reasons.append("bearish momentum alignment")
        else:
            directional_bias = "NEUTRAL"
            momentum_aligned = momentum_surge or momentum_override if max_catch_mode else False

        missing_ideal = [item for item in self.IDEAL_DATA if not (item == "historical option premium candles" and self.option_premium_history_available)]
        ideal_available = ["historical option premium candles"] if self.option_premium_history_available else []
        option_tape_override = (
            (spread_quality >= 70 and (volume_accel >= 45 or momentum_surge) and (breakout >= 60 or abs(delta_velocity) >= 35))
            or (spread_quality >= 88 and breakout >= 60 and abs(delta_velocity) >= 55)
        )
        elite_runner = (
            score >= elite_min_score
            and momentum_surge
            and momentum_aligned
            and spread_quality >= elite_spread_min
            and breakout >= elite_breakout_min
            and abs(delta_velocity) >= elite_delta_velocity_min
            and volume > 0
        )
        confidence = "LOW"
        if elite_runner:
            confidence = "HIGH"
            reasons.append("elite explosive runner: momentum, spread, delta and direction aligned")
        elif momentum_override and momentum_aligned:
            confidence = "HIGH"
            elite_runner = True  # momentum override promotes to elite
            reasons.append(f"MOMENTUM OVERRIDE ELITE: velocity {premium_velocity:.1f}% + volume + direction — bypassing normal engine gates")
        elif momentum_override:
            confidence = "HIGH"
            reasons.append(f"MOMENTUM OVERRIDE: velocity {premium_velocity:.1f}% + volume surge — entering regardless of TQS/regime")
        elif max_catch_mode and momentum_surge and score >= 52:
            confidence = "MEDIUM" if score < 70 else "HIGH"
            reasons.append("max-catch: momentum surge qualifies runner")
        elif score >= 85 and option_tape_override and breakout >= 62 and abs(delta_velocity) >= 58:
            confidence = "HIGH"
            reasons.append("option tape override: explosive premium momentum despite lower global TQS")
        elif score >= 75 and (tqs >= 70 or momentum_surge):
            confidence = "HIGH"
        elif score >= 70 and option_tape_override:
            confidence = "MEDIUM"
            reasons.append("option tape override: runner watch despite lower global TQS")
        elif score >= 55 and (tqs >= 60 or momentum_surge):
            confidence = "MEDIUM"
        elif momentum_surge and momentum_aligned and score >= 68:
            confidence = "MEDIUM"
            reasons.append("momentum surge with directional alignment")

        spread_floor = 50 if max_catch_mode else (60 if momentum_override else (70 if momentum_surge and momentum_aligned else 75))
        candidate = confidence in {"MEDIUM", "HIGH"} and premium > 0 and spread_quality >= spread_floor
        if momentum_override and premium > 0:
            candidate = True
        if max_catch_mode and momentum_surge and premium > 0 and spread_quality >= 48 and score >= 52:
            candidate = True
            if confidence == "LOW":
                confidence = "MEDIUM"
                reasons.append("max-catch runner auto-candidate")  # momentum override always a candidate
        if momentum_surge and momentum_aligned and premium > 0 and spread_quality >= 65 and score >= 68:
            candidate = True
            if confidence == "LOW":
                confidence = "MEDIUM"
                reasons.append("momentum runner auto-candidate")

        # Expiry day: infinite gamma — on expiry morning a 100-200% move is normal
        # Set maximum targets for expiry day momentum options
        if momentum_override and momentum_aligned:
            target_pct = 60  # ₹130→₹208 open drive explosion
            hard_stop_pct = 8
            trail_pct = 15   # tightest trail — lock in gains immediately on volatile expiry options
            partial_pct = 0.25
        elif elite_runner:
            target_pct = 45
            hard_stop_pct = 8
            trail_pct = 24
            partial_pct = 0.25
        elif momentum_override:
            target_pct = 50
            hard_stop_pct = 8
            trail_pct = 18
            partial_pct = 0.35
        else:
            target_pct = 33 if confidence == "HIGH" else 22 if confidence == "MEDIUM" else 11
            hard_stop_pct = 8 if confidence == "HIGH" else 7
            trail_pct = 18 if confidence == "HIGH" else 12
            partial_pct = 0.35 if confidence == "HIGH" else 0.5

        return {
            "strategyType": "EXPLOSIVE_RUNNER",
            "candidate": candidate,
            "confidence": confidence,
            "score": round(min(score, 100), 2),
            "momentumOverride": momentum_override,
            "premiumVelocityPct": round(premium_velocity, 2),
            "volumeAcceleration": round(volume_accel, 2),
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
            "eliteRunner": elite_runner,
            "maxPointsPlan": {
                "targetPremiumPct": target_pct,
                "hardStopPct": hard_stop_pct,
                "trailPct": trail_pct,
                "partialExitPct": partial_pct,
                "holdBias": "maximize_points_with_trailing_lock" if elite_runner else "standard_runner",
            },
            "momentumSurge": momentum_surge,
            "directionalBias": directional_bias,
            "momentumAligned": momentum_aligned,
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
                "premiumVelocity": round(premium_velocity, 2),
                "priceVelocity": round(price_velocity, 3),
                "spreadQuality": spread_quality,
            },
        }
