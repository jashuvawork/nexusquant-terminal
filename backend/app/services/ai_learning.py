from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

try:
    import redis.asyncio as redis  # type: ignore
except Exception:  # pragma: no cover
    redis = None

AI_STATE_KEY = "nexusquant:ai:continuous_learning_state"

PRETRAINED_PRIOR = {
    "version": "institutional-prior-v1",
    "description": "Seeded expert prior for Indian index-option scalping. It is not trained on private history; it encodes initial institutional weights before live/paper outcomes accumulate.",
    "componentWeights": {
        "delta_engine": 0.16,
        "momentum_engine": 0.14,
        "heatmap_engine": 0.12,
        "volume_engine": 0.10,
        "regime_engine": 0.10,
        "spread_analysis": 0.09,
        "option_chain_bias": 0.10,
        "gamma_positioning": 0.10,
        "iv_expansion": 0.05,
        "market_profile_alignment": 0.04,
    },
    "sessionPriors": {
        "OPEN_DRIVE": {"minimumTqsBias": -4, "cooldownBiasSeconds": -5, "sizeBias": 1.15},
        "MIDDAY_CHOP": {"minimumTqsBias": 8, "cooldownBiasSeconds": 25, "sizeBias": 0.55},
        "CLOSING_MOMENTUM": {"minimumTqsBias": 2, "cooldownBiasSeconds": 0, "sizeBias": 0.9},
        "CLOSED": {"minimumTqsBias": 99, "cooldownBiasSeconds": 60, "sizeBias": 0},
        "PREMARKET": {"minimumTqsBias": 99, "cooldownBiasSeconds": 60, "sizeBias": 0},
    },
    "regimePriors": {
        "TREND_EXPANSION": {"sizeBias": 1.15, "trailBias": 1.2},
        "RANGE_ABSORPTION": {"sizeBias": 0.75, "trailBias": 0.8},
        "REVERSAL_RISK": {"sizeBias": 0.25, "trailBias": 0.6},
        "CLOSED_MARKET_ANALYSIS": {"sizeBias": 0, "trailBias": 0},
    },
}


def _initial_state() -> dict[str, Any]:
    return {
        "pretrained": True,
        "prior": deepcopy(PRETRAINED_PRIOR),
        "samples": 0,
        "paperSamples": 0,
        "liveSamples": 0,
        "wins": 0,
        "losses": 0,
        "grossProfit": 0.0,
        "grossLoss": 0.0,
        "profitFactor": 0.0,
        "learningScore": 50.0,
        "calibration": {
            "tqsBias": 0.0,
            "spreadPenalty": 0.0,
            "chopPenalty": 0.0,
            "volumeReward": 0.0,
            "sessionAdjustments": {},
        },
        "lastFeatures": {},
        "lastOutcome": None,
        "lastUpdatedAt": None,
    }


class ContinuousAILearner:
    """Persistent online learner seeded with expert priors.

    It updates every backend tick from real-data-derived signals and paper/live
    outcomes. It does not claim historical profitability until enough samples
    have accumulated in this deployment/account.
    """

    _state: dict[str, Any] = _initial_state()

    def __init__(self, redis_url: str, enabled: bool = True) -> None:
        self.redis_url = redis_url
        self.enabled = enabled

    async def load(self) -> dict[str, Any]:
        if redis is not None:
            try:
                client = redis.from_url(self.redis_url, encoding="utf-8", decode_responses=True)
                raw = await client.get(AI_STATE_KEY)
                await client.aclose()
                if raw:
                    ContinuousAILearner._state = json.loads(raw)
            except Exception:
                pass
        return ContinuousAILearner._state

    async def persist(self) -> None:
        if redis is None:
            return
        try:
            client = redis.from_url(self.redis_url, encoding="utf-8", decode_responses=True)
            await client.set(AI_STATE_KEY, json.dumps(ContinuousAILearner._state))
            await client.aclose()
        except Exception:
            pass

    async def update_from_tick(self, payload: dict[str, Any], exits: list[dict[str, Any]], mode: str = "paper") -> dict[str, Any]:
        state = await self.load()
        if not self.enabled:
            return self.status_from_state(state)

        candidates = payload.get("executionCandidates") or []
        snapshots = payload.get("snapshots") or {}
        tqs_values = [float(candidate.get("tqs") or 0) for candidate in candidates]
        avg_tqs = sum(tqs_values) / len(tqs_values) if tqs_values else float(payload.get("tradeQualityScore") or 50)
        blocked_count = sum(1 for candidate in candidates if candidate.get("chopBlocked"))
        volume_confirmed = sum(1 for candidate in candidates if candidate.get("effectiveVolume", 0) > 0)
        pnl = sum(float(item.get("pnl") or 0) for item in exits)

        state["samples"] += 1
        if mode == "live":
            state["liveSamples"] += len(exits)
        else:
            state["paperSamples"] += len(exits)

        if pnl > 0:
            state["wins"] += 1
            state["grossProfit"] += pnl
            state["lastOutcome"] = "win"
        elif pnl < 0:
            state["losses"] += 1
            state["grossLoss"] += abs(pnl)
            state["lastOutcome"] = "loss"

        gross_loss = float(state["grossLoss"])
        state["profitFactor"] = round(float(state["grossProfit"]) / gross_loss, 3) if gross_loss else round(float(state["grossProfit"]), 3)

        calibration = state["calibration"]
        # Conservative online nudges: reward clean volume/high TQS, penalize chop and losses.
        calibration["tqsBias"] = round(max(-10, min(10, float(calibration.get("tqsBias", 0)) + (avg_tqs - 70) * 0.001 + (-0.05 if pnl < 0 else 0.02 if pnl > 0 else 0))), 4)
        calibration["chopPenalty"] = round(max(0, min(10, float(calibration.get("chopPenalty", 0)) + blocked_count * 0.01 + (0.05 if pnl < 0 else -0.01))), 4)
        calibration["volumeReward"] = round(max(0, min(10, float(calibration.get("volumeReward", 0)) + volume_confirmed * 0.005 + (0.02 if pnl > 0 else 0))), 4)
        calibration["spreadPenalty"] = round(max(0, min(10, float(calibration.get("spreadPenalty", 0)) + (0.03 if pnl < 0 else -0.005))), 4)

        state["learningScore"] = round(max(0, min(100, float(state["learningScore"]) * 0.985 + avg_tqs * 0.015 + (1 if pnl > 0 else -1 if pnl < 0 else 0))), 3)
        state["lastFeatures"] = {
            "candidateCount": len(candidates),
            "avgTqs": round(avg_tqs, 2),
            "blockedCount": blocked_count,
            "volumeConfirmed": volume_confirmed,
            "symbols": list(snapshots.keys()),
        }
        state["lastUpdatedAt"] = datetime.now(timezone.utc).isoformat()
        ContinuousAILearner._state = state
        await self.persist()
        return self.status_from_state(state)


    async def train_from_historical_samples(self, samples: list[dict[str, Any]]) -> dict[str, Any]:
        state = await self.load()
        if not samples:
            return {**self.status_from_state(state), "historicalTraining": {"samplesAdded": 0, "message": "No samples supplied"}}
        wins = [sample for sample in samples if float(sample.get("pnl", 0)) > 0]
        losses = [sample for sample in samples if float(sample.get("pnl", 0)) < 0]
        gross_profit = sum(float(sample.get("pnl", 0)) for sample in wins)
        gross_loss = abs(sum(float(sample.get("pnl", 0)) for sample in losses))
        avg_tqs = sum(float(sample.get("tqs", 0)) for sample in samples) / len(samples)
        chop_losses = sum(1 for sample in losses if sample.get("regime") in {"RANGE_ABSORPTION", "REVERSAL_RISK"})
        trend_wins = sum(1 for sample in wins if sample.get("regime") == "TREND_EXPANSION")

        state["samples"] += len(samples)
        state["paperSamples"] += len(samples)
        state["wins"] += len(wins)
        state["losses"] += len(losses)
        state["grossProfit"] += gross_profit
        state["grossLoss"] += gross_loss
        total_loss = float(state["grossLoss"])
        state["profitFactor"] = round(float(state["grossProfit"]) / total_loss, 3) if total_loss else round(float(state["grossProfit"]), 3)
        calibration = state["calibration"]
        calibration["tqsBias"] = round(max(-10, min(10, float(calibration.get("tqsBias", 0)) + (avg_tqs - 70) * 0.002)), 4)
        calibration["chopPenalty"] = round(max(0, min(10, float(calibration.get("chopPenalty", 0)) + chop_losses * 0.01)), 4)
        calibration["volumeReward"] = round(max(0, min(10, float(calibration.get("volumeReward", 0)) + trend_wins * 0.005)), 4)
        state["learningScore"] = round(max(0, min(100, float(state["learningScore"]) * 0.9 + avg_tqs * 0.1 + (len(wins) - len(losses)) * 0.01)), 3)
        state["lastFeatures"] = {"historicalSamples": len(samples), "avgTqs": round(avg_tqs, 2), "wins": len(wins), "losses": len(losses)}
        state["lastOutcome"] = "historical_training"
        state["lastUpdatedAt"] = datetime.now(timezone.utc).isoformat()
        ContinuousAILearner._state = state
        await self.persist()
        return {**self.status_from_state(state), "historicalTraining": {"samplesAdded": len(samples), "wins": len(wins), "losses": len(losses), "grossProfit": round(gross_profit, 2), "grossLoss": round(gross_loss, 2)}}

    def status_from_state(self, state: dict[str, Any] | None = None) -> dict[str, Any]:
        state = state or ContinuousAILearner._state
        return {
            "enabled": self.enabled,
            "pretrained": bool(state.get("pretrained")),
            "priorVersion": state.get("prior", {}).get("version"),
            "mode": "pretrained_prior_plus_continuous_backend_learning",
            "samples": state.get("samples", 0),
            "paperSamples": state.get("paperSamples", 0),
            "liveSamples": state.get("liveSamples", 0),
            "wins": state.get("wins", 0),
            "losses": state.get("losses", 0),
            "profitFactor": state.get("profitFactor", 0),
            "learningScore": state.get("learningScore", 50),
            "calibration": state.get("calibration", {}),
            "lastFeatures": state.get("lastFeatures", {}),
            "lastOutcome": state.get("lastOutcome"),
            "lastUpdatedAt": state.get("lastUpdatedAt"),
            "note": "Seeded with institutional priors; continuously updates from paper/live outcomes in backend.",
        }

    async def status(self) -> dict[str, Any]:
        return self.status_from_state(await self.load())

    async def export_state(self) -> dict[str, Any]:
        return await self.load()

    async def reset(self) -> dict[str, Any]:
        ContinuousAILearner._state = _initial_state()
        await self.persist()
        return await self.status()
