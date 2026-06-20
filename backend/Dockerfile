FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN python - <<'PY'
# Full module import — fails the image build if AutoTraderEngine or other startup imports break.
from app.main import app, market_engine

validation = market_engine.validate_runtime()
if not validation["ok"]:
    raise SystemExit(f"NexusQuant runtime validation failed: {validation}")
print(f"NexusQuant startup validation passed: app={app.title!r} runtime={validation}")
PY

EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
