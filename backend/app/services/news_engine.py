from __future__ import annotations

from typing import Any

POSITIVE = {"rally", "surge", "gain", "record", "beats", "growth", "upgrade", "bullish", "strong", "positive", "inflow"}
NEGATIVE = {"fall", "drop", "loss", "miss", "downgrade", "bearish", "weak", "negative", "outflow", "crash", "probe", "risk"}
EVENT = {"rbi", "fed", "inflation", "budget", "election", "policy", "rate", "cpi", "gdp", "war", "tariff", "crude", "rupee"}


def _items(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not payload:
        return []
    data = payload.get("data") or payload
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    for key in ["news", "headlines", "articles", "items", "results"]:
        value = data.get(key) if isinstance(data, dict) else None
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


class NewsEngine:
    def analyze(self, payload: dict[str, Any] | None, unavailable_reason: str | None = None) -> dict[str, Any]:
        articles = _items(payload)
        score = 0
        event_hits = 0
        parsed = []
        for article in articles[:10]:
            title = str(article.get("title") or article.get("headline") or article.get("summary") or "")
            text = f"{title} {article.get('description') or ''}".lower()
            pos = sum(1 for word in POSITIVE if word in text)
            neg = sum(1 for word in NEGATIVE if word in text)
            evt = sum(1 for word in EVENT if word in text)
            score += pos - neg
            event_hits += evt
            parsed.append({
                "title": title[:220],
                "sentiment": "positive" if pos > neg else "negative" if neg > pos else "neutral",
                "eventRisk": evt > 0,
                "source": article.get("source") or article.get("publisher"),
                "publishedAt": article.get("published_at") or article.get("publishedAt") or article.get("date"),
            })
        sentiment = "positive" if score > 1 else "negative" if score < -1 else "neutral"
        risk = "HIGH" if event_hits >= 3 or abs(score) >= 4 else "MEDIUM" if event_hits or abs(score) >= 2 else "LOW"
        return {
            "available": bool(articles),
            "unavailableReason": unavailable_reason,
            "sentiment": sentiment,
            "score": score,
            "eventRisk": risk,
            "articles": parsed,
            "impact": {
                "raiseTqs": risk == "HIGH" or sentiment == "negative",
                "allowRunnerBias": sentiment == "positive" and risk != "HIGH",
                "avoidFreshTrades": risk == "HIGH" and sentiment == "negative",
            },
        }
