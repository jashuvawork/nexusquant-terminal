from __future__ import annotations

from typing import Any


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


LTP_BINS = [
    (0, 25),
    (25, 50),
    (50, 100),
    (100, 150),
    (150, 250),
    (250, 500),
    (500, 1_000_000),
]


class LtpRangeAnalyzer:
    def analyze_option_chain(self, option_chain: dict[str, Any]) -> dict[str, Any]:
        rows = option_chain.get("data") or []
        bins: dict[str, dict[str, Any]] = {}
        for low, high in LTP_BINS:
            label = f"{low}-{high}" if high < 1_000_000 else f"{low}+"
            bins[label] = {
                "range": label,
                "low": low,
                "high": high if high < 1_000_000 else None,
                "contracts": 0,
                "avgSpreadPct": 0.0,
                "totalVolume": 0,
                "totalOi": 0,
                "avgDelta": 0.0,
                "avgGamma": 0.0,
                "score": 0.0,
                "examples": [],
            }
        accum: dict[str, dict[str, float]] = {key: {"spread": 0, "delta": 0, "gamma": 0} for key in bins}
        for row in rows:
            strike = row.get("strike_price")
            for side_key, side in [("call_options", "CALL"), ("put_options", "PUT")]:
                opt = row.get(side_key) or {}
                md = opt.get("market_data") or {}
                greeks = opt.get("option_greeks") or {}
                ltp = _num(md.get("ltp"))
                if ltp <= 0:
                    continue
                label = self._bin_label(ltp)
                item = bins[label]
                bid = _num(md.get("bid_price"))
                ask = _num(md.get("ask_price"))
                spread_pct = ((ask - bid) / ltp * 100) if bid > 0 and ask > 0 else 100
                volume = _num(md.get("volume"))
                oi = _num(md.get("oi"))
                delta = abs(_num(greeks.get("delta")))
                gamma = abs(_num(greeks.get("gamma")))
                item["contracts"] += 1
                item["totalVolume"] += volume
                item["totalOi"] += oi
                accum[label]["spread"] += spread_pct
                accum[label]["delta"] += delta
                accum[label]["gamma"] += gamma
                if len(item["examples"]) < 5:
                    item["examples"].append({"strike": strike, "side": side, "ltp": ltp, "instrumentKey": opt.get("instrument_key"), "spreadPct": round(spread_pct, 2), "volume": volume, "oi": oi})
        for label, item in bins.items():
            count = item["contracts"]
            if count:
                item["avgSpreadPct"] = round(accum[label]["spread"] / count, 2)
                item["avgDelta"] = round(accum[label]["delta"] / count, 3)
                item["avgGamma"] = round(accum[label]["gamma"] / count, 5)
                liquidity_score = min(40, item["totalVolume"] / 100000)
                oi_score = min(20, item["totalOi"] / 1000000)
                spread_score = max(0, 25 - item["avgSpreadPct"] * 2)
                delta_score = min(15, item["avgDelta"] * 25)
                item["score"] = round(liquidity_score + oi_score + spread_score + delta_score, 2)
        ranked = sorted(bins.values(), key=lambda item: item["score"], reverse=True)
        return {
            "analysisType": "CURRENT_OPTION_PREMIUM_LTP_RANGE",
            "bestRange": ranked[0] if ranked else None,
            "ranges": ranked,
            "note": "This is exact current Upstox option-chain premium LTP range analysis, not historical premium-candle backtest.",
        }

    def _bin_label(self, ltp: float) -> str:
        for low, high in LTP_BINS:
            if low <= ltp < high:
                return f"{low}-{high}" if high < 1_000_000 else f"{low}+"
        return "500+"
