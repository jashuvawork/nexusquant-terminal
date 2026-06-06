from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from app.core.config import Settings

FINNHUB_URL = "https://finnhub.io/api/v1/news"

SYMBOL_KEYWORDS = {
    "NIFTY": ["nifty", "nse", "india", "sensex", "rbi", "bank nifty", "indian market"],
    "SENSEX": ["sensex", "bse", "india", "nifty", "rbi", "indian market"],
}


class NewsProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def fetch(self, symbol: str) -> dict[str, Any]:
        provider = self.settings.news_provider.lower().strip()
        if provider != "finnhub":
            return {"status": "disabled", "provider": provider, "data": [], "reason": f"Unsupported NEWS_PROVIDER={self.settings.news_provider}"}
        if not self.settings.finnhub_api_key:
            return {"status": "missing_api_key", "provider": "finnhub", "data": [], "reason": "FINNHUB_API_KEY is not configured"}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(FINNHUB_URL, params={"category": "general", "token": self.settings.finnhub_api_key})
            if response.status_code >= 400:
                return {"status": "error", "provider": "finnhub", "data": [], "reason": f"Finnhub HTTP {response.status_code}: {response.text[:300]}"}
            raw = response.json()
            items = raw if isinstance(raw, list) else []
            filtered = self._filter(symbol, items)
            return {
                "status": "ok",
                "provider": "finnhub",
                "fetchedAt": datetime.now(timezone.utc).isoformat(),
                "data": filtered[: self.settings.news_lookback_items],
                "totalFetched": len(items),
                "matched": len(filtered),
            }
        except Exception as exc:
            return {"status": "error", "provider": "finnhub", "data": [], "reason": str(exc)}

    def _filter(self, symbol: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        keywords = SYMBOL_KEYWORDS.get(symbol.upper(), SYMBOL_KEYWORDS["NIFTY"])
        matched = []
        for item in items:
            headline = str(item.get("headline") or item.get("title") or "")
            summary = str(item.get("summary") or item.get("description") or "")
            text = f"{headline} {summary}".lower()
            if any(keyword in text for keyword in keywords):
                matched.append({
                    "title": headline,
                    "description": summary,
                    "source": item.get("source"),
                    "url": item.get("url"),
                    "publishedAt": item.get("datetime"),
                    "provider": "finnhub",
                })
        # If no India-specific match, return top macro headlines so event-risk still has context.
        if not matched:
            for item in items[: self.settings.news_lookback_items]:
                matched.append({
                    "title": str(item.get("headline") or item.get("title") or ""),
                    "description": str(item.get("summary") or item.get("description") or ""),
                    "source": item.get("source"),
                    "url": item.get("url"),
                    "publishedAt": item.get("datetime"),
                    "provider": "finnhub",
                })
        return matched
