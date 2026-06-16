#!/usr/bin/env bash
set -euo pipefail

: "${AWS_REGION:=ap-south-1}"
: "${ECR_REPOSITORY:=nexusquant-api}"
: "${IMAGE_TAG:=latest}"
: "${ACCOUNT_ID:=939198471076}"
: "${ENV_FILE:=/opt/nexusquant/env}"
: "${PERSIST_DIR:=/opt/nexusquant}"
: "${CONTAINER_NAME:=nexusquant-api}"
: "${HOST_PORT:=8000}"

IMAGE_URI="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPOSITORY}:${IMAGE_TAG}"

echo "==> NexusQuant EC2 redeploy"
echo "    Image: ${IMAGE_URI}"
echo "    Env:   ${ENV_FILE}"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "ERROR: env file missing: ${ENV_FILE}" >&2
  exit 1
fi

echo "==> Trading mode guardrails"
if grep -q '^ENABLE_LIVE_TRADING=true' "${ENV_FILE}"; then
  echo "ERROR: ENABLE_LIVE_TRADING=true in ${ENV_FILE}; refusing redeploy." >&2
  exit 1
fi
grep -E '^(PAPER_TRADING|ENABLE_LIVE_TRADING)=' "${ENV_FILE}" || true

echo "==> Persistent volume"
ls -la "${PERSIST_DIR}" || true
for f in upstox_token.json paper_trades.json ai_state.json; do
  if [[ -f "${PERSIST_DIR}/${f}" ]]; then
    echo "    present: ${f}"
  fi
done

echo "==> Docker disk usage"
sudo docker system df || true
df -h / /var/lib/docker 2>/dev/null || df -h /

echo "==> ECR login"
aws ecr get-login-password --region "${AWS_REGION}" | sudo docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

echo "==> Pull image"
sudo docker pull "${IMAGE_URI}"

echo "==> Recreate container"
sudo docker rm -f "${CONTAINER_NAME}" 2>/dev/null || true
sudo docker run -d \
  --name "${CONTAINER_NAME}" \
  --restart unless-stopped \
  --env-file "${ENV_FILE}" \
  -v "${PERSIST_DIR}:${PERSIST_DIR}" \
  -p "${HOST_PORT}:8000" \
  "${IMAGE_URI}"

echo "==> Wait for health"
for i in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${HOST_PORT}/health" >/tmp/nq-health.json 2>/dev/null; then
    echo "health ok:"
    cat /tmp/nq-health.json
    break
  fi
  sleep 2
  if [[ "${i}" -eq 30 ]]; then
    echo "ERROR: health check timed out" >&2
    sudo docker logs --tail 80 "${CONTAINER_NAME}" || true
    exit 1
  fi
done

echo "==> Auto-trader status"
curl -fsS "http://127.0.0.1:${HOST_PORT}/api/auto-trader/status" | head -c 1200 || true
echo

echo "==> Market snapshots probe"
curl -sS -o /dev/null -w "snapshots HTTP %{http_code}\n" "http://127.0.0.1:${HOST_PORT}/api/market/snapshots" || true

echo "==> Recent logs"
sudo docker logs --tail 40 "${CONTAINER_NAME}" || true

echo "==> Redeploy complete"
