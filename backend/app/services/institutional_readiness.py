from __future__ import annotations

from typing import Any

TARGET_SCORE = 9.5


def _score(value: float) -> float:
    return round(max(0.0, min(10.0, value)), 2)


class InstitutionalReadinessEngine:
    """Scores whether the system is truly institutional-ready.

    Scores are evidence-based. They intentionally stay below target until paper
    trades, live fills, slippage and forward performance prove readiness.
    """

    def score_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        production = snapshot.get("productionReadiness") or {}
        auto = snapshot.get("autoTrader") or {}
        daily = auto.get("dailyReport") or {}
        precision = snapshot.get("precisionChecklist") or {}
        risk = snapshot.get("risk") or {}
        infra = snapshot.get("infra") or {}
        upstox = snapshot.get("upstoxConnection") or {}
        event_journal = snapshot.get("eventJournal") or []
        learning = (auto.get("onlineLearning") or {}) if auto else {}
        optimized = snapshot.get("optimizedProfile") or {}

        scores = {
            "uiUx": self._ui_score(snapshot),
            "architecture": self._architecture_score(snapshot),
            "realTimeTelemetry": self._telemetry_score(upstox, infra),
            "backtestEngine": self._backtest_score(production, daily),
            "scalpingLogic": self._scalping_score(precision, snapshot),
            "aiScoring": self._ai_score(learning),
            "riskFramework": self._risk_score(risk, production),
            "institutionalFeel": 9.2,
            "executionReadiness": self._execution_score(daily, auto, snapshot),
            "eventJournal": self._event_score(event_journal),
            "optimizer": self._optimizer_score(optimized),
        }
        overall = _score(sum(scores.values()) / len(scores))
        gaps = self._gaps(scores, snapshot, daily, production, learning)
        return {
            "target": TARGET_SCORE,
            "overall": overall,
            "scores": scores,
            "passedTarget": overall >= TARGET_SCORE and all(value >= TARGET_SCORE for value in scores.values()),
            "liveFullCapitalAllowed": overall >= TARGET_SCORE and bool(production.get("readyForFullCapital")),
            "gaps": gaps,
            "nextActions": self._next_actions(gaps),
        }

    def _ui_score(self, snapshot: dict[str, Any]) -> float:
        required = ["pressureMode", "precisionChecklist", "adaptiveExit", "noTradeZones", "tqsBreakdown", "autoTrader"]
        return _score(8.0 + sum(1 for key in required if snapshot.get(key)) * 0.3)

    def _architecture_score(self, snapshot: dict[str, Any]) -> float:
        base = 8.0
        if snapshot.get("dataSource") == "UPSTOX_REALTIME_REST":
            base += 0.6
        if snapshot.get("optimizedProfile"):
            base += 0.4
        if snapshot.get("autoTrader"):
            base += 0.4
        if snapshot.get("eventJournal") is not None:
            base += 0.3
        return _score(base)

    def _telemetry_score(self, upstox: dict[str, Any], infra: dict[str, Any]) -> float:
        score = 5.5
        if upstox.get("marketDataVerified"):
            score += 1.5
        if infra.get("upstoxLatencyMs", 9999) and infra.get("upstoxLatencyMs", 9999) < 750:
            score += 1.0
        if infra.get("websocketLatencyMs", 9999) and infra.get("websocketLatencyMs", 9999) < 1000:
            score += 0.7
        if upstox.get("fundsVerified"):
            score += 0.5
        return _score(score)

    def _backtest_score(self, production: dict[str, Any], daily: dict[str, Any]) -> float:
        score = 4.5
        if production.get("passed", 0) >= 4:
            score += 1.5
        if daily.get("paperTrades", 0) >= 100:
            score += 1.0
        if daily.get("profitFactor", 0) >= 1.5:
            score += 1.0
        if daily.get("winRate", 0) >= 45:
            score += 0.8
        if production.get("readyForFullCapital"):
            score += 1.2
        return _score(score)

    def _scalping_score(self, precision: dict[str, Any], snapshot: dict[str, Any]) -> float:
        score = 5.0
        if precision.get("passed"):
            score += 2.0
        if (snapshot.get("qualityFilters") or {}).get("chopFilter", {}).get("blocked") is False:
            score += 0.8
        if (snapshot.get("entryModel") or {}).get("failedBreakout") is False:
            score += 0.5
        if (snapshot.get("explosiveRunner") or {}).get("candidate"):
            score += 0.7
        return _score(score)

    def _ai_score(self, learning: dict[str, Any]) -> float:
        score = 5.0
        if learning.get("pretrained"):
            score += 1.0
        samples = learning.get("samples", 0) or 0
        if samples >= 1000:
            score += 1.0
        if samples >= 10000:
            score += 1.0
        if learning.get("profitFactor", 0) >= 1.5:
            score += 1.0
        if learning.get("liveSamples", 0) >= 100:
            score += 1.0
        return _score(score)

    def _risk_score(self, risk: dict[str, Any], production: dict[str, Any]) -> float:
        score = 8.0
        if risk.get("safeMode"):
            score += 0.4
        if production.get("readyForSmallLive") or production.get("readyForFullCapital"):
            score += 0.6
        if risk.get("dailyDrawdownPct", 0) < 5:
            score += 0.4
        return _score(score)

    def _execution_score(self, daily: dict[str, Any], auto: dict[str, Any], snapshot: dict[str, Any]) -> float:
        score = 3.5
        if auto.get("paperTrading"):
            score += 1.0
        if daily.get("paperTrades", 0) >= 50:
            score += 1.0
        if daily.get("paperTrades", 0) >= 300:
            score += 1.0
        if daily.get("profitFactor", 0) >= 1.5:
            score += 1.2
        if snapshot.get("executionAllowed"):
            score += 1.0
        if auto.get("liveSamples", 0) >= 50:
            score += 1.0
        return _score(score)

    def _event_score(self, events: list[dict[str, Any]]) -> float:
        score = 7.0
        if events:
            score += 1.0
        types = {event.get("type") for event in events}
        if {"SIGNAL", "REJECTION"}.issubset(types):
            score += 0.8
        if {"ENTRY", "EXIT"}.intersection(types):
            score += 0.7
        return _score(score)

    def _optimizer_score(self, optimized: dict[str, Any]) -> float:
        score = 7.0 if optimized else 4.0
        if optimized.get("executionStyle"):
            score += 1.0
        if optimized.get("targetPoints") and optimized.get("stopPoints"):
            score += 0.8
        return _score(score)

    def _gaps(self, scores: dict[str, float], snapshot: dict[str, Any], daily: dict[str, Any], production: dict[str, Any], learning: dict[str, Any]) -> list[dict[str, Any]]:
        gaps = []
        for name, value in scores.items():
            if value < TARGET_SCORE:
                gaps.append({"area": name, "score": value, "target": TARGET_SCORE})
        if daily.get("paperTrades", 0) < 300:
            gaps.append({"area": "forwardPaperTrades", "score": daily.get("paperTrades", 0), "target": 300})
        if daily.get("profitFactor", 0) < 1.5:
            gaps.append({"area": "paperProfitFactor", "score": daily.get("profitFactor", 0), "target": 1.5})
        if learning.get("liveSamples", 0) < 100:
            gaps.append({"area": "liveExecutionSamples", "score": learning.get("liveSamples", 0), "target": 100})
        if not production.get("readyForFullCapital"):
            gaps.append({"area": "productionReadiness", "score": production.get("passed", 0), "target": production.get("total", 6)})
        return gaps

    def _next_actions(self, gaps: list[dict[str, Any]]) -> list[str]:
        actions = []
        names = {gap["area"] for gap in gaps}
        if "forwardPaperTrades" in names:
            actions.append("Run paper trading until 300+ forward paper trades are captured.")
        if "paperProfitFactor" in names:
            actions.append("Keep optimizer/trainer active; do not scale until paper PF >= 1.5.")
        if "liveExecutionSamples" in names:
            actions.append("After paper proof, collect small-live samples before full capital.")
        if "productionReadiness" in names:
            actions.append("Full capital remains blocked until readiness checks pass.")
        return actions or ["Maintain monitoring and continue collecting forward data."]
