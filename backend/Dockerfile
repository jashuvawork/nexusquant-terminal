FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN python - <<'PY'
from app.main import market_engine
required = ["_backtest_metrics", "_suggested_trades", "_premarket_analysis", "_tomorrow_trade_plan", "_chop_filter", "_upstox_latency_ms"]
missing = [name for name in required if not hasattr(market_engine, name)]
if missing:
    raise SystemExit(f"Missing RealTimeMarketEngine helper methods: {missing}")
print("NexusQuant runtime validation passed")
PY

EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
