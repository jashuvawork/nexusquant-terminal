#!/usr/bin/env bash
# NexusQuant background monitor IST schedule (Mon–Fri).
# 08:30 IST = 03:00 UTC | 16:00 IST = 10:30 UTC
# Install: sudo cp to /opt/nexusquant/scripts/ and add /etc/cron.d/nexusquant-monitor

set -euo pipefail

ENV_FILE="${ENV_FILE:-/opt/nexusquant/env}"
CONTAINER_NAME="${CONTAINER_NAME:-nexusquant-api}"
ACTION="${1:-}"

set_kv() {
  local key="$1" value="$2"
  sudo sed -i "/^${key}=/d" "$ENV_FILE"
  echo "${key}=${value}" | sudo tee -a "$ENV_FILE" >/dev/null
}

case "$ACTION" in
  enable)
    set_kv BACKGROUND_MARKET_MONITOR_ENABLED true
    set_kv MARKET_SNAPSHOT_MONITOR_ENABLED true
    set_kv BACKGROUND_MONITOR_SCHEDULE_ENABLED true
    set_kv BACKGROUND_MONITOR_START_IST "08:30"
    set_kv BACKGROUND_MONITOR_END_IST "16:00"
    sudo docker restart "$CONTAINER_NAME"
    echo "$(date -Is) enabled background monitor (08:30 IST window)"
    ;;
  disable)
    set_kv BACKGROUND_MARKET_MONITOR_ENABLED false
    set_kv MARKET_SNAPSHOT_MONITOR_ENABLED false
    sudo docker restart "$CONTAINER_NAME"
    echo "$(date -Is) disabled background monitor (16:00 IST cutoff)"
    ;;
  *)
    echo "Usage: $0 {enable|disable}" >&2
    exit 1
    ;;
esac
