#!/usr/bin/env bash
# Sync production trading env on every deploy — EC2 /opt/nexusquant/env often drifts stricter than code defaults.
set -euo pipefail

ENV_FILE="${1:-/opt/nexusquant/env}"

upsert() {
  local k="$1" v="$2"
  if sudo grep -q "^${k}=" "${ENV_FILE}" 2>/dev/null; then
    sudo sed -i "s|^${k}=.*|${k}=${v}|" "${ENV_FILE}"
  else
    echo "${k}=${v}" | sudo tee -a "${ENV_FILE}" >/dev/null
  fi
}

echo "==> Sync trading env: ${ENV_FILE}"

upsert NEXUSQUANT_BACKEND_COMMIT "${NEXUSQUANT_BACKEND_COMMIT:-unknown}"
upsert PAPER_MIN_ENTRY_TQS 70
upsert PAPER_HIGH_CONFIDENCE_MIN_TQS 72
upsert PAPER_HIGH_CONFIDENCE_MIN_RUNNER_SCORE 85
upsert PAPER_MOMENTUM_MIN_ENTRY_TQS 55
upsert PAPER_MOMENTUM_MIN_PREMIUM_LTP 25
upsert PAPER_MOMENTUM_MAX_ENTRY_PREMIUM 140
upsert PAPER_MOMENTUM_CHASE_PREMIUM_FLOOR 125
upsert PAPER_MOMENTUM_CHASE_MAX_VELOCITY_PCT 2.5
upsert PAPER_MOMENTUM_EXPLOSION_VELOCITY_PCT 3
upsert PAPER_MOMENTUM_EXPLOSION_VOLUME_ACCEL 30
upsert PAPER_MOMENTUM_OVERRIDE_MIN_VELOCITY_PCT 3
upsert PAPER_RUNNER_BYPASS_QUALITY_GATES false
upsert EXPLOSIVE_RUNNER_SCAN_STRIKES 24
upsert EXPLOSIVE_RUNNER_MOMENTUM_PREMIUM_VELOCITY_PCT 3
upsert EXPLOSIVE_RUNNER_MOMENTUM_MIN_SCORE 65
upsert SNAPSHOT_CACHE_SECONDS 2
upsert BACKGROUND_MARKET_MONITOR_ENABLED true
upsert BACKGROUND_MONITOR_SCHEDULE_ENABLED false
upsert MARKET_POLL_SECONDS 3

echo "==> Key trading settings:"
sudo grep -E "^(PAPER_MIN_ENTRY|PAPER_HIGH_CONFIDENCE|EXPLOSIVE_RUNNER_SCAN|PAPER_MOMENTUM|SNAPSHOT_CACHE|BACKGROUND_MARKET)" "${ENV_FILE}" | sort -u || true
