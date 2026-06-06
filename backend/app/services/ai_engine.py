from __future__ import annotations

from dataclasses import dataclass
from typing import Any

try:  # XGBoost can be mounted later as a trained model artifact.
    import xgboost as xgb  # type: ignore
except Exception:  # pragma: no cover
    xgb = None


@dataclass(frozen=True)
class EngineWeight:
    name: str
    key: str
    weight: float


ENGINE_WEIGHTS = [
    EngineWeight("Delta Engine", "delta_engine", 0.16),
    EngineWeight("Momentum Engine", "momentum_engine", 0.14),
    EngineWeight("Heatmap Engine", "heatmap_engine", 0.12),
    EngineWeight("Volume Engine", "volume_engine", 0.10),
    EngineWeight("Regime Engine", "regime_engine", 0.10),
    EngineWeight("Spread Analysis", "spread_analysis", 0.09),
    EngineWeight("Option Chain Bias", "option_chain_bias", 0.10),
    EngineWeight("Gamma Positioning", "gamma_positioning", 0.10),
    EngineWeight("IV Expansion", "iv_expansion", 0.05),
    EngineWeight("Market Profile Alignment", "market_profile_alignment", 0.04),
]


def clamp_score(value: float | int | None) -> int:
    if value is None:
        return 0
    return round(max(0, min(100, float(value))))


class TradeQualityScorer:
    """Weighted real-data scorer with a slot for a trained XGBoost model."""

    def __init__(self) -> None:
        self.model: Any | None = None
        if xgb is not None:
            self.model = None

    def score(self, features: dict[str, float]) -> tuple[int, list[dict[str, Any]]]:
        matrix: list[dict[str, Any]] = []
        for engine in ENGINE_WEIGHTS:
            score = clamp_score(features.get(engine.key))
            matrix.append(
                {
                    "engine": engine.name,
                    "score": score,
                    "weight": engine.weight,
                    "status": "pass" if score >= 78 else "watch" if score >= 62 else "fail",
                }
            )
        tqs = round(sum(item["score"] * item["weight"] for item in matrix))
        return tqs, matrix
