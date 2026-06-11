from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def quote_item(instrument_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    last_price = float(payload.get("last_price") or payload.get("ltp") or 0)
    ohlc = payload.get("ohlc") or {}
    previous_close = float(ohlc.get("close") or payload.get("close") or payload.get("prev_close") or 0)
    net_change = float(payload.get("net_change") or (last_price - previous_close if previous_close else 0))
    change_pct = round((net_change / previous_close) * 100, 3) if previous_close else 0
    volume = int(float(payload.get("volume") or payload.get("volume_traded") or 0))
    average_price = float(payload.get("average_price") or payload.get("avg_price") or last_price or 0)
    return {
        "instrumentKey": instrument_key,
        "symbol": payload.get("symbol") or payload.get("trading_symbol") or instrument_key,
        "lastPrice": last_price,
        "previousClose": previous_close,
        "netChange": round(net_change, 3),
        "changePct": change_pct,
        "volume": volume,
        "value": round(volume * average_price, 2),
        "averagePrice": average_price,
    }


def summarize_market_movers(instruments: list[str], quote_payload: dict[str, Any]) -> dict[str, Any]:
    data = quote_payload.get("data") or {}
    items = [quote_item(key, value or {}) for key, value in data.items()]
    gainers = sorted(items, key=lambda item: item["changePct"], reverse=True)
    losers = sorted(items, key=lambda item: item["changePct"])
    most_active_volume = sorted(items, key=lambda item: item["volume"], reverse=True)
    most_active_value = sorted(items, key=lambda item: item["value"], reverse=True)
    advancing = sum(1 for item in items if item["changePct"] > 0)
    declining = sum(1 for item in items if item["changePct"] < 0)
    unchanged = max(0, len(items) - advancing - declining)
    breadth_total = advancing + declining
    breadth_score = round((advancing / breadth_total) * 100, 2) if breadth_total else 50.0
    return {
        "source": "upstox_full_market_quote",
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "configuredInstruments": instruments,
        "count": len(items),
        "breadthQuality": {
            "sufficient": len(items) >= 20,
            "minimumRecommended": 20,
            "message": "Breadth is reliable with broad stock/sector coverage." if len(items) >= 20 else "Breadth is directionally useful but limited; add more stock/sector instruments for institutional-grade confirmation.",
        },
        "breadth": {
            "advancing": advancing,
            "declining": declining,
            "unchanged": unchanged,
            "score": breadth_score,
            "bias": "BULLISH" if breadth_score >= 60 else "BEARISH" if breadth_score <= 40 else "NEUTRAL",
        },
        "gainers": gainers[:10],
        "losers": losers[:10],
        "mostActiveVolume": most_active_volume[:10],
        "mostActiveValue": most_active_value[:10],
        "indices": [item for item in items if "INDEX|" in item["instrumentKey"]],
    }
