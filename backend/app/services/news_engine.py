from __future__ import annotations

from typing import Any

POSITIVE = {
    "rally", "surge", "gain", "record", "beats", "growth", "upgrade", "bullish", "strong",
    "positive", "inflow", "recovery", "rebound", "rise", "boost", "support", "eases",
    "peace", "stable", "high", "soars", "climbs", "jumps", "up",
}
NEGATIVE = {
    "fall", "drop", "loss", "miss", "downgrade", "bearish", "weak", "negative", "outflow",
    "crash", "probe", "risk", "decline", "slump", "tension", "attack", "conflict",
    "pressure", "crisis", "strike", "sanction", "threat", "tumble", "plunge", "war",
    "sell", "down", "low", "hurt", "fear", "panic", "volatile",
}
EVENT = {
    "rbi", "fed", "inflation", "budget", "election", "policy", "rate", "cpi", "gdp",
    "war", "tariff", "crude", "rupee", "oil", "geopolitical", "iran", "china",
    "sanctions", "ceasefire", "fii", "dii", "sebi", "nse", "bse", "sensex", "nifty",
    "us-india", "rbi rate", "repo", "msci", "f&o", "expiry",
}


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
        positive_count = 0
        negative_count = 0
        for article in articles[:15]:
            title = str(article.get("title") or article.get("headline") or article.get("summary") or "")
            text = f"{title} {article.get('description') or ''}".lower()
            pos = sum(1 for word in POSITIVE if word in text)
            neg = sum(1 for word in NEGATIVE if word in text)
            evt = sum(1 for word in EVENT if word in text)
            score += pos - neg
            event_hits += min(evt, 3)
            positive_count += 1 if pos > neg else 0
            negative_count += 1 if neg > pos else 0
            parsed.append({
                "headline": title[:180],
                "title": title[:180],
                "sentiment": "positive" if pos > neg else "negative" if neg > pos else "neutral",
                "eventRisk": evt > 0,
                "source": article.get("source") or article.get("publisher"),
                "publishedAt": article.get("published_at") or article.get("publishedAt") or article.get("date"),
            })

        sentiment = "positive" if score > 2 else "negative" if score < -2 else "neutral"
        risk = "HIGH" if event_hits >= 4 or abs(score) >= 5 else "MEDIUM" if event_hits >= 2 or abs(score) >= 3 else "LOW"

        avoid_fresh = risk == "HIGH" and sentiment == "negative"
        raise_tqs = risk in {"HIGH", "MEDIUM"} or sentiment == "negative"
        allow_runner_bias = sentiment == "positive" and risk == "LOW" and positive_count >= 3

        bias = "CALL" if allow_runner_bias else "PUT" if (sentiment == "negative" and risk != "LOW") else None
        news_score = max(-100, min(100, score * 10))

        if avoid_fresh:
            trading_implication = "AVOID new entries: HIGH event risk with bearish news flow"
        elif raise_tqs and not allow_runner_bias:
            trading_implication = "CAUTIOUS: event risk elevated — raise quality bar, prefer runners over scalps"
        elif allow_runner_bias:
            trading_implication = "BULLISH BIAS confirmed: positive news supports CALL runner entries"
        else:
            trading_implication = "NEUTRAL: standard quality gates apply"

        return {
            "available": bool(articles),
            "unavailableReason": unavailable_reason,
            "sentiment": sentiment,
            "score": score,
            "newsScore": news_score,
            "eventRisk": risk,
            "bias": bias,
            "tradingImplication": trading_implication,
            "articles": parsed,
            "headlines": parsed[:5],
            "confidence": "HIGH" if len(articles) >= 8 else "MEDIUM" if len(articles) >= 4 else "LOW",
            "positiveCount": positive_count,
            "negativeCount": negative_count,
            "articleCount": len(parsed),
            "impact": {
                "raiseTqs": raise_tqs,
                "allowRunnerBias": allow_runner_bias,
                "avoidFreshTrades": avoid_fresh,
                "biasSide": bias,
                "newsScore": news_score,
            },
        }
