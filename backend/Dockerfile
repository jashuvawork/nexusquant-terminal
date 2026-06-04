FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN python - <<'PY'
from app.main import market_engine
validation = market_engine.validate_runtime()
if not validation["ok"]:
    raise SystemExit(f"NexusQuant runtime validation failed: {validation}")
print(f"NexusQuant runtime validation passed: {validation}")
PY

EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
