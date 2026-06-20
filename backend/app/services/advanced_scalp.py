"""Advanced scalp techniques: regime lanes, EV gate, vol-scaled ACS, partials, ML exit overlay."""

from __future__ import annotations

from typing import Any

REGIME_MOMENTUM = "TREND_EXPANSION"
REGIME_RANGE = "RANGE_ABSORPTION"
REGIME_REVERSAL = "REVERSAL_RISK"
LANE_MOMENTUM = "MOMENTUM"
LANE_FADE = "FADE"
ALL_DAY_SCALP_BUCKETS = frozenset({"OPEN_DRIVE", "NORMAL", "MIDDAY_CHOP", "CLOSING_MOMENTUM"})


def _runner(candidate: dict[str, Any]) -> dict[str, Any]:
    return candidate.get("runnerSignal") or candidate


def _metrics(candidate: dict[str, Any]) -> dict[str, Any]:
    return _runner(candidate).get("metrics") or {}


def normalize_regime(regime: str | None) -> str:
    value = str(regime or "NORMAL").upper()
    if value in {REGIME_MOMENTUM, REGIME_RANGE, REGIME_REVERSAL, "CLOSED_MARKET_ANALYSIS"}:
        return value
    if value == "NORMAL":
        return REGIME_RANGE
    return value


def detect_fade_setup(candidate: dict[str, Any], *, session_bucket: str, regime: str) -> bool:
    """VAH/VAL rejection fade — available in every live session bucket."""
    if session_bucket not in ALL_DAY_SCALP_BUCKETS:
        return False
    if normalize_regime(regime) != REGIME_RANGE:
        return False
    runner = _runner(candidate)
    if runner.get("momentumSurge") or runner.get("momentumOverride"):
        return False
    metrics = _metrics(candidate)
    premium_velocity = float(runner.get("premiumVelocityPct") or metrics.get("premiumVelocity") or 0)
    if premium_velocity >= 2.0:
        return False
    side = str(candidate.get("side") or "").upper()
    chart_bias = str(candidate.get("chartBias") or "").upper()
    if side not in {"CALL", "PUT"}:
        return False
    if chart_bias in {"CALL", "PUT"} and chart_bias == side:
        return False
    profile = candidate.get("marketProfile") or candidate.get("profile") or {}
    spot = float(candidate.get("spot") or profile.get("spot") or 0)
    vah = float(profile.get("vah") or 0)
    val = float(profile.get("val") or 0)
    if spot > 0 and vah > val > 0:
        if side == "PUT" and spot <= val * 1.002:
            return True
        if side == "CALL" and spot >= vah * 0.998:
            return True
    if chart_bias == "WAIT" and not runner.get("momentumAligned"):
        return True
    return False


def classify_scalp_lane(candidate: dict[str, Any], *, regime: str, session_bucket: str) -> str | None:
    regime = normalize_regime(regime)
    if detect_fade_setup(candidate, session_bucket=session_bucket, regime=regime):
        return LANE_FADE
    if regime == REGIME_REVERSAL:
        runner = _runner(candidate)
        if runner.get("momentumOverride"):
            return LANE_MOMENTUM
        return None
    if regime == REGIME_MOMENTUM:
        return LANE_MOMENTUM
    if regime == REGIME_RANGE:
        runner = _runner(candidate)
        if runner.get("momentumSurge") or runner.get("momentumOverride"):
            return LANE_MOMENTUM
        return None
    runner = _runner(candidate)
    if runner.get("momentumSurge") or runner.get("momentumOverride"):
        return LANE_MOMENTUM
    return None


def passes_regime_gate(lane: str | None, regime: str, *, all_day_enabled: bool = True) -> tuple[bool, str | None]:
    regime = normalize_regime(regime)
    if regime == "CLOSED_MARKET_ANALYSIS":
        return False, "advanced scalp blocked: market closed"
    if all_day_enabled:
        if lane is None:
            return False, "advanced scalp blocked: no valid momentum or fade lane"
        return True, None
    if regime == REGIME_REVERSAL:
        return False, "advanced scalp blocked: reversal-risk regime"
    if lane is None:
        return False, "advanced scalp blocked: no valid momentum or fade lane for regime"
    if lane == LANE_MOMENTUM and regime not in {REGIME_MOMENTUM, REGIME_RANGE}:
        return False, "momentum scalp blocked outside trend/range expansion"
    return True, None


def cell_key(session_bucket: str, side: str, symbol: str) -> str:
    return f"{session_bucket}|{side.upper()}|{symbol.upper()}"


def passes_ev_gate(
    summary: dict[str, Any] | None,
    *,
    min_trades: int,
    min_profit_factor: float,
    min_expectancy: float,
) -> tuple[bool, str | None]:
    if not summary:
        return True, None
    trades = int(summary.get("paperTrades") or summary.get("trades") or 0)
    if trades < min_trades:
        return True, None
    pf = float(summary.get("profitFactor") or 0)
    expectancy = float(summary.get("avgPnl") or summary.get("expectancy") or 0)
    if pf < min_profit_factor or expectancy < min_expectancy:
        return False, f"EV gate: cell PF {pf:.2f} / expectancy {expectancy:.2f} below floor"
    return True, None


def passes_absorption_gate(candidate: dict[str, Any], quality: dict[str, Any], *, enabled: bool) -> tuple[bool, str | None]:
    if not enabled:
        return True, None
    runner = _runner(candidate)
    if runner.get("momentumOverride"):
        return True, None
    metrics = _metrics(candidate)
    premium_velocity = float(runner.get("premiumVelocityPct") or metrics.get("premiumVelocity") or 0)
    delta_velocity = abs(float(metrics.get("deltaVelocity") or 0))
    volume_accel = float(runner.get("volumeAcceleration") or metrics.get("volumeAcceleration") or 0)
    spread_cost = float(quality.get("spreadCost") or 0)
    slippage = float(quality.get("slippageEstimate") or 0)
    premium = float(candidate.get("lastPremium") or runner.get("premium") or 0)
    spread_pct = ((spread_cost + slippage) / premium * 100) if premium > 0 else 999.0
    if not runner.get("momentumSurge"):
        return False, "absorption gate: momentum surge required"
    if premium_velocity < 2.0:
        return False, "absorption gate: premium velocity below 2%"
    if delta_velocity < 35:
        return False, "absorption gate: delta velocity too low"
    if volume_accel < 12:
        return False, "absorption gate: volume acceleration too low"
    if spread_pct > 2.5:
        return False, "absorption gate: spread widening — cost > 2.5% of premium"
    return True, None


def _watchlist_side(snapshot: dict[str, Any], side: str) -> bool:
    for runner in snapshot.get("explosiveRunnerWatchlist") or []:
        if not isinstance(runner, dict):
            continue
        if str(runner.get("side") or "").upper() != side.upper():
            continue
        score = float(runner.get("score") or 0)
        if score < 80:
            continue
        if runner.get("momentumSurge") or runner.get("momentumAligned") or runner.get("momentumOverride"):
            return True
    return False


def passes_cross_index_gate(
    candidate: dict[str, Any],
    snapshots: dict[str, Any],
    *,
    enabled: bool,
    min_confirmations: int = 2,
) -> tuple[bool, str | None]:
    if not enabled:
        return True, None
    runner = _runner(candidate)
    if runner.get("momentumOverride"):
        return True, None
    side = str(candidate.get("side") or "").upper()
    if side not in {"CALL", "PUT"}:
        return True, None
    confirmations = 0
    symbols_checked: list[str] = []
    for symbol, snapshot in snapshots.items():
        if not isinstance(snapshot, dict):
            continue
        sym = str(symbol or snapshot.get("symbol") or "").upper()
        if sym not in {"NIFTY", "SENSEX", "BANKNIFTY"}:
            continue
        symbols_checked.append(sym)
        if _watchlist_side(snapshot, side):
            confirmations += 1
    if confirmations >= min_confirmations:
        return True, None
    return False, f"cross-index gate: only {confirmations}/{min_confirmations} indices confirm {side}"


def read_vix_from_payload(payload: dict[str, Any]) -> float:
    market = payload.get("marketSnapshot") or {}
    vix = float(market.get("vix") or market.get("indiaVix") or 0)
    if vix > 0:
        return vix
    for item in market.get("indices") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("symbol") or "").upper()
        if "VIX" in name:
            return float(item.get("ltp") or item.get("lastPrice") or 0)
    return 0.0


def volatility_scale_acs(
    acs: dict[str, float],
    *,
    vix: float,
    atr_points: float,
    enabled: bool,
) -> dict[str, float]:
    if not enabled:
        return acs
    scaled = dict(acs)
    if vix <= 0 and atr_points <= 0:
        return scaled
    if vix > 0:
        if vix < 13:
            scale_stop, scale_cap, scale_arm, scale_retain = 0.85, 0.75, 0.85, 0.55
        elif vix <= 18:
            scale_stop, scale_cap, scale_arm, scale_retain = 1.0, 1.0, 1.0, 0.58
        else:
            scale_stop, scale_cap, scale_arm, scale_retain = 1.25, 1.35, 1.15, 0.62
    else:
        if atr_points < 8:
            scale_stop, scale_cap, scale_arm, scale_retain = 0.9, 0.8, 0.9, 0.55
        elif atr_points <= 15:
            scale_stop, scale_cap, scale_arm, scale_retain = 1.0, 1.0, 1.0, 0.58
        else:
            scale_stop, scale_cap, scale_arm, scale_retain = 1.2, 1.25, 1.1, 0.62
    scaled["stop"] = round(scaled["stop"] * scale_stop, 2)
    scaled["cap"] = round(scaled["cap"] * scale_cap, 2)
    scaled["arm"] = round(scaled["arm"] * scale_arm, 2)
    scaled["retain"] = round(min(0.75, max(0.45, scale_retain if vix <= 0 else scaled["retain"])), 3)
    scaled["breakeven"] = round(scaled.get("breakeven", 3.0) * (0.9 if vix > 18 else 1.0), 2)
    return scaled


def lane_acs_overrides(lane: str) -> dict[str, float]:
    if lane == LANE_FADE:
        return {"stop": 2.0, "cap": 5.0, "arm": 3.0, "minLock": 1.5, "retain": 0.50, "breakeven": 2.0}
    return {}


def adaptive_decay_should_exit(
    *,
    age: float,
    best_gain: float,
    min_gain_floor: float,
    base_decay_seconds: float,
    enabled: bool,
) -> bool:
    if not enabled:
        return age >= base_decay_seconds and best_gain < min_gain_floor
    progress_rate = best_gain / max(age, 1.0)
    if age >= 30 and progress_rate < 0.02 and best_gain < max(0.5, min_gain_floor):
        return True
    if age >= 60 and progress_rate < 0.04 and best_gain < max(1.0, min_gain_floor * 1.5):
        return True
    return age >= base_decay_seconds and best_gain < min_gain_floor


def kelly_size_multiplier(
    rolling_pf: float,
    *,
    enabled: bool,
    pf_pause: float = 1.0,
    pf_base: float = 1.8,
    pf_boost: float = 2.5,
) -> float:
    if not enabled:
        return 1.0
    if rolling_pf < pf_pause:
        return 0.5
    if rolling_pf < pf_base:
        return 1.0
    if rolling_pf < pf_boost:
        return 1.3
    return 1.5


def ml_exit_recommendation(
    learner_state: dict[str, Any],
    *,
    age: float,
    best_gain: float,
    unrealized: float,
    premium_velocity: float,
    runner_score: float,
    regime: str,
    enabled: bool,
) -> tuple[bool, str | None]:
    """Return (should_exit, reason). Conservative heuristic using learner priors until enough samples."""
    if not enabled:
        return False, None
    if best_gain < 1.0:
        return False, None
    prior = (learner_state.get("prior") or {}).get("regimePriors") or {}
    regime_prior = prior.get(normalize_regime(regime)) or prior.get(REGIME_RANGE) or {}
    trail_bias = float(regime_prior.get("trailBias") or 1.0)
    calibration = learner_state.get("calibration") or {}
    chop_penalty = float(calibration.get("chopPenalty") or 0)
    learning_score = float(learner_state.get("learningScore") or 50)
    pf = float(learner_state.get("profitFactor") or 0)
    exit_score = 0.0
    if premium_velocity < 1.0 and best_gain >= 2.0:
        exit_score += 35
    if runner_score < 60 and best_gain >= 3.0:
        exit_score += 25
    if age >= 90 and unrealized < best_gain * 0.55:
        exit_score += 20
    if chop_penalty >= 3:
        exit_score += 15
    if pf > 0 and pf < 1.0 and unrealized > 0:
        exit_score += 10
    exit_score *= max(0.6, min(1.4, 2.0 - trail_bias))
    exit_score += max(0, (50 - learning_score) * 0.2)
    if exit_score >= 55 and unrealized >= 1.5:
        return True, "ML exit overlay: momentum/score decay with profit on table"
    return False, None


def evaluate_advanced_scalp_entry(
    candidate: dict[str, Any],
    *,
    payload: dict[str, Any],
    session_bucket: str,
    regime: str,
    quality: dict[str, Any],
    ev_summary: dict[str, Any] | None,
    settings: Any,
) -> dict[str, Any]:
    if not getattr(settings, "paper_advanced_scalp_enabled", True):
        return {"allowed": True, "lane": LANE_MOMENTUM, "reason": None, "sizeMultiplier": 1.0}

    runner = _runner(candidate)
    if runner.get("momentumOverride"):
        return {"allowed": True, "lane": LANE_MOMENTUM, "reason": None, "sizeMultiplier": 1.0}

    lane = str(candidate.get("scalpLane") or "") or classify_scalp_lane(candidate, regime=regime, session_bucket=session_bucket)
    if getattr(settings, "paper_scalp_regime_gate_enabled", True):
        all_day = bool(getattr(settings, "paper_all_day_scalp_enabled", True))
        ok, reason = passes_regime_gate(lane, regime, all_day_enabled=all_day)
        if not ok:
            return {"allowed": False, "lane": lane, "reason": reason, "sizeMultiplier": 1.0}

    if getattr(settings, "paper_scalp_ev_gate_enabled", True):
        ok, reason = passes_ev_gate(
            ev_summary,
            min_trades=int(getattr(settings, "paper_scalp_ev_min_trades", 8)),
            min_profit_factor=float(getattr(settings, "paper_scalp_ev_min_profit_factor", 1.0)),
            min_expectancy=float(getattr(settings, "paper_scalp_ev_min_expectancy", -0.5)),
        )
        if not ok:
            return {"allowed": False, "lane": lane, "reason": reason, "sizeMultiplier": 1.0}

    if lane == LANE_MOMENTUM and getattr(settings, "paper_scalp_absorption_gate_enabled", True):
        ok, reason = passes_absorption_gate(candidate, quality, enabled=True)
        if not ok:
            return {"allowed": False, "lane": lane, "reason": reason, "sizeMultiplier": 1.0}

    snapshots = payload.get("snapshots") or {}
    if getattr(settings, "paper_scalp_cross_index_enabled", True):
        ok, reason = passes_cross_index_gate(
            candidate,
            snapshots,
            enabled=True,
            min_confirmations=int(getattr(settings, "paper_scalp_cross_index_min", 2)),
        )
        if not ok and lane == LANE_MOMENTUM:
            return {"allowed": False, "lane": lane, "reason": reason, "sizeMultiplier": 1.0}

    return {"allowed": True, "lane": lane or LANE_MOMENTUM, "reason": None, "sizeMultiplier": 1.0}
