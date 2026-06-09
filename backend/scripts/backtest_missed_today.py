#!/usr/bin/env python3
"""Backtest today's missed replay opportunities and train AI calibration."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings
from app.services.ai_learning import ContinuousAILearner
from app.services.auto_trader import AutoTraderEngine
from app.services.historical_trainer import HistoricalTrainer
from app.services.trading_control import TradingControl
from app.services.upstox_client import UpstoxClient


async def main() -> int:
    settings = get_settings()
    learner = ContinuousAILearner(settings.redis_url, settings.ai_learning_enabled, settings.ai_state_file)
    await learner.load()
    auto = AutoTraderEngine(settings, TradingControl(settings), learner)
    result = await auto.backtest_and_train_missed_today(
        target_trades=500,
        horizon_ticks=60,
        min_profit_points=8.0,
        include_losses=True,
    )
    historical = {"results": {}, "errors": {}}
    if settings.upstox_access_token or settings.upstox_api_key:
        trainer = HistoricalTrainer(settings, UpstoxClient(settings), learner)
        today = __import__("datetime").date.today().isoformat()
        for symbol in ["NIFTY", "SENSEX"]:
            try:
                historical["results"][symbol] = await trainer.train_option_runner(
                    symbol,
                    250,
                    None,
                    today,
                    today,
                    1,
                    40,
                    high_profit_only=True,
                )
            except Exception as exc:
                historical["errors"][symbol] = str(exc)
    payload = {**result, "historicalOptionTraining": historical}
    print(json.dumps(payload, indent=2))
    return 0 if result.get("available") or historical.get("results") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
