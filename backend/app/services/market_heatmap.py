"""NSE-style constituent heatmap built from Upstox full market quotes."""
from __future__ import annotations

from typing import Any

from app.services.instrument_keys import display_symbol, index_constituent_symbols, resolve_instrument_key, resolve_instrument_keys
from app.services.market_movers import NIFTY50_WEIGHTS, quote_item

STOCK_WEIGHTS = dict(NIFTY50_WEIGHTS)


def _index_quote_payload(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index Upstox quote rows by instrument_token, trading_symbol, and response key."""
    indexed: dict[str, dict[str, Any]] = {}
    for key, value in (raw or {}).items():
        val = value or {}
        for alias in {key, key.replace(":", "|"), key.replace("|", ":")}:
            if alias:
                indexed[alias] = val
        token = str(val.get("instrument_token") or "")
        if token:
            indexed[token] = val
            indexed[token.replace(":", "|")] = val
            indexed[token.replace("|", ":")] = val
        sym = str(val.get("trading_symbol") or val.get("symbol") or "").strip().upper()
        if sym and sym not in {"NA", "N/A"}:
            indexed[sym] = val
    return indexed


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
    normalized = _index_quote_payload(quote_payload.get("data") or {})

    items: list[dict[str, Any]] = []
    for symbol in symbols:
        instrument_key = resolve_instrument_key(symbol)
        if not instrument_key:
            continue
        payload = (
            normalized.get(instrument_key)
            or normalized.get(instrument_key.replace("|", ":"))
            or normalized.get(symbol.upper())
        )
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

    merged = await client.full_market_quote_batched(keys)
    result = build_constituent_heatmap(index, merged)
    if not result["available"]:
        result["reason"] = result.get("reason") or f"Only {result['stockCount']}/{result.get('requested', 0)} constituents returned (need ≥10)"
    return result
