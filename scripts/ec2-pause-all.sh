#!/usr/bin/env bash
# Stop all NexusQuant backend services on EC2 — no API, no background monitor, no auto-restart.
set -euo pipefail

ENV_FILE="${ENV_FILE:-/opt/nexusquant/env}"
PERSIST_DIR="${PERSIST_DIR:-/opt/nexusquant}"
CONTAINER_NAME="${CONTAINER_NAME:-nexusquant-api}"
PAUSE_FLAG="${PERSIST_DIR}/PAUSED"

echo "==> NexusQuant EC2 full pause"
date -Is

set_kv() {
  local key="$1" value="$2"
  sudo sed -i "/^${key}=/d" "${ENV_FILE}"
  echo "${key}=${value}" | sudo tee -a "${ENV_FILE}" >/dev/null
}

echo "==> Write pause flag (deploy scripts must refuse to start while present)"
echo "paused at $(date -Is)" | sudo tee "${PAUSE_FLAG}" >/dev/null

echo "==> Disable background monitor and paper auto-processing"
set_kv BACKGROUND_MARKET_MONITOR_ENABLED false
set_kv MARKET_SNAPSHOT_MONITOR_ENABLED false
set_kv BACKGROUND_MONITOR_SCHEDULE_ENABLED false
set_kv PAPER_TRADING false
set_kv ENABLE_LIVE_TRADING false

echo "==> Stop and remove API container (no --restart)"
if sudo docker ps -a --format '{{.Names}}' | grep -qx "${CONTAINER_NAME}"; then
  sudo docker stop "${CONTAINER_NAME}" || true
  sudo docker rm "${CONTAINER_NAME}" || true
  echo "    removed container ${CONTAINER_NAME}"
else
  echo "    container ${CONTAINER_NAME} not running"
fi

echo "==> Stop any other nexusquant containers"
mapfile -t extra < <(sudo docker ps -a --format '{{.Names}}' | grep -i nexusquant || true)
for name in "${extra[@]}"; do
  [[ -z "${name}" ]] && continue
  sudo docker stop "${name}" 2>/dev/null || true
  sudo docker rm "${name}" 2>/dev/null || true
  echo "    removed ${name}"
done

echo "==> Verify local health is down"
if curl -fsS --max-time 3 "http://127.0.0.1:8000/health" >/dev/null 2>&1; then
  echo "WARNING: port 8000 still responding" >&2
else
  echo "    port 8000 not responding (expected)"
fi

echo "==> Pause complete — remove ${PAUSE_FLAG} and redeploy to resume"
date -Is
