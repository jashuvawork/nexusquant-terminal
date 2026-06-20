#!/usr/bin/env python3
"""Backtest unified scalp profile: baseline (+6/-6) vs ACS asymmetric exits."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import get_settings
from app.services.historical_trainer import HistoricalTrainer
from app.services.risk_profiles import _scalp_acs_session_params
from app.services.upstox_client import UpstoxClient
from app.services.ai_learning import ContinuousAILearner

IST = ZoneInfo("Asia/Kolkata")
API = "https://api.nexusquant.uk"

UNIFIED_MIN_TQS = 78
UNIFIED_MIN_RUNNER = 82
ACS_MIN_RUNNER = 92
LEGACY_QUICK_PROFIT_POINTS = 6.0
LEGACY_STOP_POINTS = 6.0
MAX_HOLD_SECONDS = 180
DEDUPE_SECONDS = 120
MICRO_TRAIL = 1.25
MICRO_MIN_GAIN = 3.0
GIVEBACK_PCT = 0.4
PREMIUM_MIN = 80.0
PREMIUM_MAX = 185.0
VELOCITY_MIN_PCT = 2.0


def session_bucket_ist(ts: datetime) -> str:
    t = ts.astimezone(IST).time()
    if time(9, 15) <= t <= time(10, 30):
        return "OPEN_DRIVE"
    if time(11, 30) <= t <= time(13, 30):
        return "MIDDAY_CHOP"
    if time(14, 30) <= t <= time(15, 15):
        return "CLOSING_MOMENTUM"
    if time(9, 15) <= t <= time(15, 30):
        return "NORMAL"
    return "OUTSIDE_HOURS"


def pf(trades: list[dict[str, Any]]) -> float:
    gp = sum(float(t["pnl"]) for t in trades if float(t["pnl"]) > 0)
    gl = abs(sum(float(t["pnl"]) for t in trades if float(t["pnl"]) < 0))
    return round(gp / gl, 3) if gl else round(gp, 3)


def wr(trades: list[dict[str, Any]]) -> float:
    if not trades:
        return 0.0
    return round(100 * sum(1 for t in trades if float(t["pnl"]) > 0) / len(trades), 1)


def wilson_ci(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = wins / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / denom
    return (round(100 * max(0, center - margin), 1), round(100 * min(1, center + margin), 1))


@dataclass
class Summary:
    source: str
    trades: list[dict[str, Any]]

    def by_bucket(self) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for t in self.trades:
            out[str(t.get("bucket") or "UNKNOWN")].append(t)
        return out

    def report(self) -> dict[str, Any]:
        wins = sum(1 for t in self.trades if float(t["pnl"]) > 0)
        n = len(self.trades)
        lo, hi = wilson_ci(wins, n)
        buckets = {}
        for bucket, ts in sorted(self.by_bucket().items(), key=lambda x: -len(x[1])):
            bw = sum(1 for t in ts if float(t["pnl"]) > 0)
            blo, bhi = wilson_ci(bw, len(ts))
            buckets[bucket] = {
                "trades": len(ts),
                "winRatePct": wr(ts),
                "winProb95Ci": [blo, bhi],
                "profitFactor": pf(ts),
                "netPnlPerUnit": round(sum(float(t["pnl"]) for t in ts), 2),
                "avgPnlPerUnit": round(sum(float(t["pnl"]) for t in ts) / len(ts), 2) if ts else 0,
            }
        return {
            "source": self.source,
            "trades": n,
            "wins": wins,
            "losses": n - wins,
            "winRatePct": wr(self.trades),
            "winProbability95CiPct": [lo, hi],
            "profitFactor": pf(self.trades),
            "avgPnlPerUnit": round(sum(float(t["pnl"]) for t in self.trades) / n, 2) if n else 0,
            "byBucket": buckets,
        }


def fetch_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=120) as resp:
        return json.loads(resp.read().decode())


def iter_replay_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for candidate in payload.get("executionCandidates") or []:
        candidates.append(candidate)
    for snapshot in (payload.get("snapshots") or {}).values():
        if not isinstance(snapshot, dict):
            continue
        for runner in snapshot.get("explosiveRunnerWatchlist") or []:
            if not isinstance(runner, dict):
                continue
            candidates.append({
                "id": runner.get("id"),
                "symbol": runner.get("symbol") or snapshot.get("symbol"),
                "side": runner.get("side"),
                "strike": runner.get("strike"),
                "instrumentKey": runner.get("instrumentKey"),
                "lastPremium": runner.get("lastPremium") or runner.get("premium"),
                "strategyType": "EXPLOSIVE_RUNNER",
                "runnerSignal": runner,
                "tqs": runner.get("score"),
                "chopBlocked": False,
            })
    return candidates


def passes_velocity_gate(candidate: dict[str, Any]) -> bool:
    runner = candidate.get("runnerSignal") or candidate
    if runner.get("momentumOverride"):
        return True
    if not runner.get("momentumSurge"):
        return False
    metrics = runner.get("metrics") or {}
    premium_velocity = float(runner.get("premiumVelocityPct") or metrics.get("premiumVelocity") or 0)
    if premium_velocity < VELOCITY_MIN_PCT:
        return False
    if premium_velocity < 3.0 and not runner.get("momentumAligned"):
        return False
    return True


def passes_unified_scalp_entry(candidate: dict[str, Any], global_tqs: int | None, *, acs_mode: bool) -> bool:
    if candidate.get("chopBlocked"):
        return False
    premium = float(candidate.get("lastPremium") or candidate.get("premium") or 0)
    if premium <= 0 or not (PREMIUM_MIN <= premium <= PREMIUM_MAX):
        return False
    tqs = int(candidate.get("tqs") or global_tqs or 0)
    min_tqs = UNIFIED_MIN_TQS
    min_runner = ACS_MIN_RUNNER if acs_mode else UNIFIED_MIN_RUNNER
    if tqs < min_tqs:
        return False
    runner = candidate.get("runnerSignal") or {}
    runner_score = float(runner.get("score") or tqs)
    if runner_score < min_runner and str(candidate.get("strategyType") or "").upper() == "EXPLOSIVE_RUNNER":
        return False
    st = str(candidate.get("strategyType") or "SCALP").upper()
    if st == "EXPLOSIVE_RUNNER":
        if not (runner.get("eliteRunner") or runner.get("momentumOverride") or runner.get("momentumSurge")):
            return False
    if acs_mode and not passes_velocity_gate(candidate):
        return False
    return True


def simulate_legacy_scalp_exit(entry: float, future: list[tuple[datetime, float]]) -> tuple[float, str]:
    best = entry
    opened = future[0][0] if future else datetime.now(timezone.utc)
    for ts, price in future:
        age = (ts - opened).total_seconds()
        best = max(best, price)
        unrealized = price - entry
        best_gain = best - entry
        if price >= entry + LEGACY_QUICK_PROFIT_POINTS:
            return LEGACY_QUICK_PROFIT_POINTS - 0.5, "quick_profit_target_hit"
        if best_gain >= MICRO_MIN_GAIN:
            if price <= best - MICRO_TRAIL:
                return unrealized - 0.5, "micro_scalp_profit_lock"
            if unrealized >= 2.0 and unrealized < best_gain * (1.0 - GIVEBACK_PCT):
                return unrealized - 0.5, "micro_scalp_giveback_lock"
        if price <= entry - LEGACY_STOP_POINTS:
            return -LEGACY_STOP_POINTS - 0.5, "momentum_decay_stop"
        if age >= MAX_HOLD_SECONDS:
            if unrealized >= MICRO_MIN_GAIN:
                return unrealized - 0.5, "scalp_time_stop_profit_lock"
            return unrealized - 0.5, "scalp_time_stop"
    if not future:
        return 0.0, "no_future_data"
    final = future[-1][1]
    return final - entry - 0.5, "replay_end"


def simulate_acs_scalp_exit(
    entry: float,
    future: list[tuple[datetime, float]],
    bucket: str,
) -> tuple[float, str]:
    acs = _scalp_acs_session_params(bucket)
    stop = float(acs["controlledStopPoints"])
    cap = float(acs["runnerCapPoints"])
    arm = float(acs["runnerArmPoints"])
    min_lock = float(acs["runnerMinLockPoints"])
    retain = float(acs["runnerRetainPct"])
    micro_arm = 4.0
    micro_trail = 1.25
    decay_seconds = 45.0
    decay_min_gain = 0.5
    breakeven_shift = 3.0

    best = entry
    opened = future[0][0]
    breakeven_armed = False
    for ts, price in future:
        age = (ts - opened).total_seconds()
        best = max(best, price)
        unrealized = price - entry
        best_gain = best - entry
        if best_gain >= breakeven_shift:
            breakeven_armed = True
        effective_stop = entry + 1.5 if breakeven_armed else entry - stop
        if price >= entry + cap:
            return cap - 0.5, "acs_cap_target"
        if age >= decay_seconds and best_gain < decay_min_gain:
            return unrealized - 0.5, "acs_early_decay"
        if best_gain >= arm:
            floor_price = entry + max(min_lock, best_gain * retain)
            if price <= floor_price:
                return unrealized - 0.5, "acs_asymmetric_trail"
        if micro_arm <= best_gain < arm and price <= best - micro_trail:
            return unrealized - 0.5, "acs_micro_lock"
        if price <= effective_stop:
            return unrealized - 0.5, "acs_controlled_stop"
        if age >= MAX_HOLD_SECONDS:
            return unrealized - 0.5, "acs_time_stop"
    final = future[-1][1]
    return final - entry - 0.5, "replay_end"


def backtest_replay_snapshots(snapshots: list[dict[str, Any]], *, acs_mode: bool) -> Summary:
    trades: list[dict[str, Any]] = []
    seen: dict[str, datetime] = {}
    price_index: dict[str, list[tuple[datetime, float]]] = defaultdict(list)

    for item in snapshots:
        ts_raw = item.get("timestamp")
        if not ts_raw:
            continue
        ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        if session_bucket_ist(ts) == "OUTSIDE_HOURS":
            continue
        payload = item.get("payload") or {}
        for candidate in iter_replay_candidates(payload):
            key = str(candidate.get("instrumentKey") or candidate.get("id") or "")
            premium = float(candidate.get("lastPremium") or candidate.get("premium") or 0)
            if key and premium > 0:
                price_index[key].append((ts, premium))

    for item in snapshots:
        ts_raw = item.get("timestamp")
        if not ts_raw:
            continue
        ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        bucket = session_bucket_ist(ts)
        if bucket == "OUTSIDE_HOURS":
            continue
        if acs_mode:
            acs = _scalp_acs_session_params(bucket)
            if acs.get("blockScalp"):
                continue
        payload = item.get("payload") or {}
        global_tqs = int(payload.get("tradeQualityScore") or 0)
        for candidate in iter_replay_candidates(payload):
            if str(candidate.get("strategyType") or "").upper() not in {"SCALP", "EXPLOSIVE_RUNNER"}:
                continue
            if not passes_unified_scalp_entry(candidate, global_tqs, acs_mode=acs_mode):
                continue
            key = str(candidate.get("instrumentKey") or candidate.get("id") or "")
            if not key:
                continue
            last = seen.get(key)
            if last and (ts - last).total_seconds() < DEDUPE_SECONDS:
                continue
            entry = float(candidate.get("lastPremium") or 0)
            if entry <= 0:
                continue
            future = [(t, p) for t, p in price_index.get(key, []) if t > ts][:40]
            if len(future) < 2:
                continue
            if acs_mode:
                pnl, reason = simulate_acs_scalp_exit(entry, future, bucket)
            else:
                pnl, reason = simulate_legacy_scalp_exit(entry, future)
            seen[key] = ts
            trades.append({
                "time": ts.isoformat(),
                "bucket": bucket,
                "symbol": candidate.get("symbol"),
                "side": candidate.get("side"),
                "strategyType": candidate.get("strategyType"),
                "entry": round(entry, 2),
                "pnl": round(pnl, 2),
                "exitReason": reason,
                "source": "replay_option_premium_acs" if acs_mode else "replay_option_premium_legacy",
            })
    label = "replay_acs_scalp" if acs_mode else "replay_legacy_scalp"
    return Summary(label, trades)


async def backtest_index_candles(from_date: str, to_date: str) -> Summary:
    settings = get_settings()
    client = UpstoxClient(settings)
    learner = ContinuousAILearner(settings.redis_url, settings.ai_learning_enabled)
    trainer = HistoricalTrainer(settings, client, learner)
    all_samples: list[dict[str, Any]] = []
    for symbol in ("NIFTY", "SENSEX", "BANKNIFTY"):
        result = await trainer.train(symbol, target_trades=2000, from_date=from_date, to_date=to_date, interval=1)
        instrument_key = settings.instrument_key_for(symbol)
        candles: list[dict[str, Any]] = []
        for chunk in result.get("chunks") or []:
            try:
                payload = await client.historical_candles(
                    instrument_key, "minutes", 1, chunk["to"], chunk["from"]
                )
                candles.extend(trainer._parse_candles(payload))
            except Exception:
                continue
        samples = trainer._generate_scalp_samples(symbol, candles, target=5000)
        for s in samples:
            tqs = float(s.get("tqs") or 0)
            if tqs < UNIFIED_MIN_TQS:
                continue
            ts_raw = s.get("time")
            try:
                ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            bucket = session_bucket_ist(ts)
            if bucket != "OUTSIDE_HOURS":
                all_samples.append({**s, "bucket": bucket, "source": "index_candle_scalp_proxy"})
    return Summary("index_candle_scalp_proxy_unified_gates", all_samples)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-only", action="store_true", help="Skip slow index candle fetch")
    args = parser.parse_args()

    replay_path = Path("/tmp/replay_full.json")
    if not replay_path.exists():
        with urllib.request.urlopen(f"{API}/api/auto-trader/replay?limit=20000", timeout=120) as resp:
            replay_path.write_bytes(resp.read())
    replay = json.loads(replay_path.read_text())
    snapshots = replay.get("snapshots") or []

    legacy_summary = backtest_replay_snapshots(snapshots, acs_mode=False)
    acs_summary = backtest_replay_snapshots(snapshots, acs_mode=True)

    first_ts = snapshots[0].get("timestamp") if snapshots else None
    last_ts = snapshots[-1].get("timestamp") if snapshots else None
    from_date = "2026-06-10"
    to_date = "2026-06-18"
    if first_ts:
        from_date = datetime.fromisoformat(str(first_ts).replace("Z", "+00:00")).astimezone(IST).date().isoformat()
    if last_ts:
        to_date = datetime.fromisoformat(str(last_ts).replace("Z", "+00:00")).astimezone(IST).date().isoformat()

    out: dict[str, Any] = {
        "methodology": {
            "entryGates": {
                "minTqs": UNIFIED_MIN_TQS,
                "minRunnerScore": UNIFIED_MIN_RUNNER,
                "premiumRange": [PREMIUM_MIN, PREMIUM_MAX],
                "velocityMinPct": VELOCITY_MIN_PCT,
                "blockClosingMomentum": True,
            },
            "legacyExits": {"quickProfit": LEGACY_QUICK_PROFIT_POINTS, "stop": LEGACY_STOP_POINTS},
            "acsExits": "controlled stop 3-3.5pt, breakeven +3, trail arm +4.5-5, cap 12-15pt, early decay 45s",
        },
        "replayLegacy": legacy_summary.report(),
        "replayAcs": acs_summary.report(),
        "improvement": {
            "profitFactorDelta": round(acs_summary.report()["profitFactor"] - legacy_summary.report()["profitFactor"], 3),
            "winRateDeltaPct": round(acs_summary.report()["winRatePct"] - legacy_summary.report()["winRatePct"], 1),
        },
        "dataCoverage": {
            "replaySnapshots": len(snapshots),
            "replayRangeUtc": [first_ts, last_ts],
        },
    }

    if not args.replay_only:
        index_summary = await backtest_index_candles(from_date, to_date)
        out["indexCandleProxy"] = index_summary.report()

    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
