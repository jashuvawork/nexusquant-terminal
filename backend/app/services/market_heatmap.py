"""NSE-style constituent heatmap built from Upstox full market quotes."""
from __future__ import annotations

from typing import Any

from app.services.instrument_keys import display_symbol, index_constituent_symbols, resolve_instrument_keys
from app.services.market_movers import NIFTY50_WEIGHTS, quote_item

STOCK_WEIGHTS = dict(NIFTY50_WEIGHTS)


def _tone(change_pct: float) -> str:
    if change_pct > 0.5:
        return "bullish"
    if change_pct < -0.5:
        return "bearish"
    return "neutral"


def heatmap_item(instrument_key: str, payload: dict[str, Any], *, weight: float, display: str | None = None) -> dict[str, Any]:
    parsed = quote_item(instrument_key, payload or {})
    ohlc = (payload or {}).get("ohlc") or {}
    symbol = display or display_symbol(instrument_key, payload)
    last_price = float(parsed["lastPrice"])
    volume = int(parsed["volume"])
    average_price = float(parsed["averagePrice"] or last_price or 0)
    vwap = average_price if average_price > 0 else float(ohlc.get("close") or last_price or 0)
    high = float(ohlc.get("high") or last_price or 0)
    low = float(ohlc.get("low") or last_price or 0)
    traded_value_cr = round((volume * average_price) / 1e7, 2) if volume > 0 and average_price > 0 else 0.0
    volume_lakhs = round(volume / 100_000, 2) if volume > 0 else 0.0
    change_pct = float(parsed["changePct"])
    return {
        "symbol": symbol,
        "instrumentKey": instrument_key,
        "ltp": last_price,
        "prevClose": parsed["previousClose"],
        "changePct": change_pct,
        "netChange": parsed["netChange"],
        "volume": volume,
        "volumeLakhs": volume_lakhs,
        "tradedValueCr": traded_value_cr,
        "vwap": round(vwap, 2),
        "high": round(high, 2),
        "low": round(low, 2),
        "weight": weight,
        "tone": _tone(change_pct),
    }


def build_constituent_heatmap(index: str, quote_payload: dict[str, Any]) -> dict[str, Any]:
    """Build NSE-style heatmap for index constituents using ISIN instrument keys."""
    idx = index.upper()
    symbols = index_constituent_symbols(idx)
    keys = resolve_instrument_keys(symbols)
    raw = quote_payload.get("data") or {}

    # Upstox may return keys with ':' instead of '|'
    normalized: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        normalized[key.replace(":", "|")] = value or {}
        normalized[key] = value or {}

    items: list[dict[str, Any]] = []
    for symbol in symbols:
        instrument_key = resolve_instrument_key(symbol)
        if not instrument_key:
            continue
        payload = normalized.get(instrument_key) or normalized.get(instrument_key.replace("|", ":"))
        if not payload:
            continue
        weight = float(STOCK_WEIGHTS.get(symbol, 0.3))
        items.append(heatmap_item(instrument_key, payload, weight=weight, display=symbol))

    items.sort(key=lambda row: row["weight"], reverse=True)
    advancing = sum(1 for row in items if row["changePct"] > 0)
    declining = sum(1 for row in items if row["changePct"] < 0)
    weight_adv = sum(row["weight"] for row in items if row["changePct"] > 0)
    weight_dec = sum(row["weight"] for row in items if row["changePct"] < 0)
    total_weight = weight_adv + weight_dec
    breadth_score = round(weight_adv / total_weight * 100, 1) if total_weight else 50.0

    return {
        "index": idx,
        "available": len(items) >= 10,
        "source": "upstox_constituent_equity",
        "requested": len(symbols),
        "resolved": len(keys),
        "stockCount": len(items),
        "advancing": advancing,
        "declining": declining,
        "breadthScore": breadth_score,
        "breadthBias": "BULLISH" if breadth_score >= 60 else "BEARISH" if breadth_score <= 40 else "NEUTRAL",
        "stocks": items,
    }


async def fetch_constituent_heatmap(index: str, client: Any) -> dict[str, Any]:
    symbols = index_constituent_symbols(index)
    keys = resolve_instrument_keys(symbols)
    if not keys:
        return {"index": index.upper(), "available": False, "reason": "No instrument keys resolved for constituents", "stocks": []}

    # Upstox allows up to 500 keys per quote request; batch if needed
    batches = [keys[i : i + 100] for i in range(0, len(keys), 100)]
    merged: dict[str, Any] = {"data": {}}
    for batch in batches:
        payload = await client.full_market_quote(batch)
        merged["data"].update(payload.get("data") or {})

    result = build_constituent_heatmap(index, merged)
    if not result["available"]:
        result["reason"] = f"Only {result['stockCount']} constituents returned (need ≥10)"
    return result
