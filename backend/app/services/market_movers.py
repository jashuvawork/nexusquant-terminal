from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

NIFTY50_WEIGHTS: dict[str, float] = {
    "HDFCBANK": 13.0, "ICICIBANK": 8.5, "RELIANCE": 8.0, "INFY": 6.0, "BHARTIARTL": 4.5,
    "ITC": 4.2, "LT": 3.8, "TCS": 3.5, "AXISBANK": 3.2, "SBIN": 3.0,
    "KOTAKBANK": 2.8, "WIPRO": 2.2, "HCLTECH": 2.0, "BAJFINANCE": 1.9, "ADANIENT": 1.8,
    "TATAMOTORS": 1.7, "M&M": 1.6, "NTPC": 1.5, "ONGC": 1.4, "POWERGRID": 1.3,
    "SUNPHARMA": 1.3, "JSWSTEEL": 1.2, "TITAN": 1.2, "HINDUNILVR": 1.1, "NESTLEIND": 1.0,
    "ULTRACEMCO": 1.0, "MARUTI": 1.0, "BAJAJFINSV": 0.9, "DRREDDY": 0.9, "CIPLA": 0.8,
    "ADANIPORTS": 0.8, "ASIANPAINT": 0.8, "EICHERMOT": 0.7, "TRENT": 0.7, "TATASTEEL": 0.7,
    "BEL": 0.7, "HEROMOTOCO": 0.6, "HINDALCO": 0.6, "COALINDIA": 0.6, "SIEMENS": 0.5,
    "SBILIFE": 0.5, "HDFCLIFE": 0.5, "TECHM": 0.5, "APOLLOHOSP": 0.5, "MAXHEALTH": 0.4,
    "TATACONSUM": 0.4, "JIOFIN": 0.4, "SHRIRAMFIN": 0.4, "ETERNAL": 0.4, "BAJAJ-AUTO": 0.4,
}

BANKING_STOCKS = {"HDFCBANK", "ICICIBANK", "AXISBANK", "SBIN", "KOTAKBANK", "INDUSINDBK", "JIOFIN", "SBILIFE", "HDFCLIFE", "BAJFINANCE", "BAJAJFINSV", "SHRIRAMFIN"}
IT_STOCKS = {"INFY", "TCS", "HCLTECH", "WIPRO", "TECHM"}
AUTO_STOCKS = {"MARUTI", "TATAMOTORS", "M&M", "HEROMOTOCO", "BAJAJ-AUTO", "EICHERMOT"}


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


def _symbol_from_key(key: str) -> str:
    return key.split("|")[-1] if "|" in key else key


def summarize_market_movers(instruments: list[str], quote_payload: dict[str, Any]) -> dict[str, Any]:
    data = quote_payload.get("data") or {}
    items = [quote_item(key, value or {}) for key, value in data.items()]

    # Upstox response replaces | with : in keys (e.g. NSE_INDEX:Nifty 50)
    indices = [i for i in items if "INDEX" in i["instrumentKey"].upper()]
    stocks = [i for i in items if "INDEX" not in i["instrumentKey"].upper() and "NSE_EQ" not in i["instrumentKey"].upper()]

    gainers = sorted(items, key=lambda i: i["changePct"], reverse=True)
    losers = sorted(items, key=lambda i: i["changePct"])
    most_active_volume = sorted(items, key=lambda i: i["volume"], reverse=True)
    most_active_value = sorted(items, key=lambda i: i["value"], reverse=True)

    # Standard breadth (all instruments)
    advancing = sum(1 for i in items if i["changePct"] > 0)
    declining = sum(1 for i in items if i["changePct"] < 0)
    unchanged = max(0, len(items) - advancing - declining)
    breadth_total = advancing + declining
    breadth_score = round((advancing / breadth_total) * 100, 2) if breadth_total else 50.0

    # Stock-only breadth (Nifty 50 constituent level — higher resolution)
    stock_advancing = sum(1 for i in stocks if i["changePct"] > 0)
    stock_declining = sum(1 for i in stocks if i["changePct"] < 0)
    stock_total = stock_advancing + stock_declining
    stock_score = round((stock_advancing / stock_total) * 100, 2) if stock_total else breadth_score

    # Value-weighted breadth (large-caps weighted by market cap proxy = traded value)
    weighted_up = sum(i["value"] for i in stocks if i["changePct"] > 0)
    weighted_down = sum(i["value"] for i in stocks if i["changePct"] < 0)
    weighted_total = weighted_up + weighted_down
    weighted_score = round((weighted_up / weighted_total) * 100, 2) if weighted_total else breadth_score

    # Known-weight adjusted breadth (NIFTY50_WEIGHTS)
    weight_up = sum(NIFTY50_WEIGHTS.get(_symbol_from_key(i["instrumentKey"]), 0.3) for i in stocks if i["changePct"] > 0)
    weight_dn = sum(NIFTY50_WEIGHTS.get(_symbol_from_key(i["instrumentKey"]), 0.3) for i in stocks if i["changePct"] < 0)
    weight_total = weight_up + weight_dn
    weight_score = round((weight_up / weight_total) * 100, 2) if weight_total else breadth_score

    # Final breadth — prefer stock-based if available, else index-based
    final_score = stock_score if stock_total >= 20 else (weighted_score if weighted_total > 0 else breadth_score)
    final_score = round(final_score, 2)

    # Sector breakdown (banking, IT, auto)
    def _sector_score(ticker_set: set[str]) -> dict[str, Any]:
        sector = [i for i in stocks if _symbol_from_key(i["instrumentKey"]) in ticker_set]
        adv = sum(1 for i in sector if i["changePct"] > 0)
        dec = sum(1 for i in sector if i["changePct"] < 0)
        tot = adv + dec
        score = round((adv / tot) * 100, 1) if tot else 50.0
        avg_chg = round(sum(i["changePct"] for i in sector) / len(sector), 3) if sector else 0.0
        return {"advancing": adv, "declining": dec, "count": len(sector), "score": score,
                "bias": "BULLISH" if score >= 60 else "BEARISH" if score <= 40 else "NEUTRAL",
                "avgChangePct": avg_chg}

    banking_breadth = _sector_score(BANKING_STOCKS)
    it_breadth = _sector_score(IT_STOCKS)
    auto_breadth = _sector_score(AUTO_STOCKS)

    # Nifty 50 top movers by known weight
    def _top_movers() -> list[dict[str, Any]]:
        weighted = []
        for item in stocks:
            sym = _symbol_from_key(item["instrumentKey"])
            w = NIFTY50_WEIGHTS.get(sym, 0.0)
            if w > 0:
                weighted.append({**item, "indexWeight": w, "weightedImpact": round(item["changePct"] * w / 100, 4)})
        return sorted(weighted, key=lambda i: abs(i["weightedImpact"]), reverse=True)[:12]

    top_weight_movers = _top_movers()
    weighted_impact = sum(i["weightedImpact"] for i in top_weight_movers)

    sufficient = len(items) >= 15
    stock_sufficient = stock_total >= 20

    return {
        "source": "upstox_full_market_quote",
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "configuredInstruments": instruments,
        "count": len(items),
        "stockCount": len(stocks),
        "indexCount": len(indices),
        "breadthQuality": {
            "sufficient": sufficient,
            "stockSufficient": stock_sufficient,
            "minimumRecommended": 15,
            "message": (
                f"Institutional-grade breadth: {len(stocks)} stocks + {len(indices)} indices."
                if stock_sufficient else
                "Breadth is directionally useful but limited; add Nifty 50 constituent stocks for institutional-grade confirmation."
            ),
        },
        "breadth": {
            "advancing": advancing,
            "declining": declining,
            "unchanged": unchanged,
            "score": final_score,
            "stockScore": stock_score if stock_total > 0 else None,
            "weightedScore": weight_score if weight_total > 0 else None,
            "valueWeightedScore": weighted_score if weighted_total > 0 else None,
            "bias": "BULLISH" if final_score >= 60 else "BEARISH" if final_score <= 40 else "NEUTRAL",
            "stockAdvancing": stock_advancing,
            "stockDeclining": stock_declining,
        },
        "sectorBreadth": {
            "banking": banking_breadth,
            "it": it_breadth,
            "auto": auto_breadth,
        },
        "topWeightMovers": top_weight_movers,
        "weightedImpact": round(weighted_impact, 4),
        "gainers": gainers[:10],
        "losers": losers[:10],
        "mostActiveVolume": most_active_volume[:10],
        "mostActiveValue": most_active_value[:10],
        "indices": indices,
        "stocks": stocks,
        "breadthNote": f"{'Institutional-grade' if stock_sufficient else 'Index-level'} breadth from {len(indices)} indices + {len(stocks)} stocks",
    }
