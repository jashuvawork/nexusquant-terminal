#!/usr/bin/env bash
# Toggle NexusQuant background monitor on EC2.
# Default production mode: enable = 24/7 monitor (schedule off).
# Legacy scheduled window: use enable-schedule / disable-schedule.

set -euo pipefail

ENV_FILE="${ENV_FILE:-/opt/nexusquant/env}"
CONTAINER_NAME="${CONTAINER_NAME:-nexusquant-api}"
ACTION="${1:-}"

set_kv() {
  local key="$1" value="$2"
  sudo sed -i "/^${key}=/d" "$ENV_FILE"
  echo "${key}=${value}" | sudo tee -a "$ENV_FILE" >/dev/null
}

restart_api() {
  sudo docker restart "$CONTAINER_NAME"
}

case "$ACTION" in
  enable)
    set_kv BACKGROUND_MARKET_MONITOR_ENABLED true
    set_kv MARKET_SNAPSHOT_MONITOR_ENABLED true
    set_kv BACKGROUND_MONITOR_SCHEDULE_ENABLED false
    restart_api
    echo "$(date -Is) enabled background monitor (24/7; trades still LIVE_MARKET 09:15-15:30 IST)"
    ;;
  disable)
    set_kv BACKGROUND_MARKET_MONITOR_ENABLED false
    set_kv MARKET_SNAPSHOT_MONITOR_ENABLED false
    restart_api
    echo "$(date -Is) disabled background monitor"
    ;;
  enable-schedule)
    set_kv BACKGROUND_MARKET_MONITOR_ENABLED true
    set_kv MARKET_SNAPSHOT_MONITOR_ENABLED true
    set_kv BACKGROUND_MONITOR_SCHEDULE_ENABLED true
    set_kv BACKGROUND_MONITOR_START_IST "08:30"
    set_kv BACKGROUND_MONITOR_END_IST "16:00"
    restart_api
    echo "$(date -Is) enabled scheduled monitor (08:30-16:00 IST weekdays)"
    ;;
  disable-schedule)
    set_kv BACKGROUND_MONITOR_SCHEDULE_ENABLED false
    restart_api
    echo "$(date -Is) disabled IST schedule window (monitor stays 24/7 if enabled)"
    ;;
  *)
    echo "Usage: $0 {enable|disable|enable-schedule|disable-schedule}" >&2
    exit 1
    ;;
esac
