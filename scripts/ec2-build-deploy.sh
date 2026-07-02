#!/usr/bin/env bash
# Build the API image on EC2 from uploaded source and recreate the container.
# Used by GitHub Actions — no AWS credentials required in CI (only SSH).
set -euo pipefail

: "${SOURCE_DIR:=/tmp/nexusquant-backend}"
: "${ENV_FILE:=/opt/nexusquant/env}"
: "${PERSIST_DIR:=/opt/nexusquant}"
: "${CONTAINER_NAME:=nexusquant-api}"
: "${HOST_PORT:=8000}"
: "${IMAGE_NAME:=nexusquant-api:local}"
: "${DOCKER_MEMORY:=14g}"
: "${DOCKER_MEMORY_SWAP:=16g}"

echo "==> NexusQuant EC2 build-and-deploy"
echo "    Source: ${SOURCE_DIR}"
echo "    Image:  ${IMAGE_NAME}"
echo "    Env:    ${ENV_FILE}"

if [[ ! -d "${SOURCE_DIR}/app" ]]; then
  echo "ERROR: backend source missing at ${SOURCE_DIR}/app" >&2
  exit 1
fi
if [[ ! -f "${ENV_FILE}" ]]; then
  echo "ERROR: env file missing: ${ENV_FILE}" >&2
  exit 1
fi
if [[ -f "${PERSIST_DIR}/PAUSED" ]]; then
  echo "ERROR: ${PERSIST_DIR}/PAUSED exists — cluster is paused. Remove flag to redeploy." >&2
  exit 1
fi
if grep -q '^ENABLE_LIVE_TRADING=true' "${ENV_FILE}"; then
  echo "ERROR: ENABLE_LIVE_TRADING=true in ${ENV_FILE}; refusing deploy." >&2
  exit 1
fi

echo "==> Docker disk usage"
sudo docker system df || true
df -h / /var/lib/docker 2>/dev/null || df -h /

echo "==> Build image"
sudo docker build -t "${IMAGE_NAME}" "${SOURCE_DIR}"

echo "==> Recreate container"
sudo docker rm -f "${CONTAINER_NAME}" 2>/dev/null || true
sudo docker run -d \
  --name "${CONTAINER_NAME}" \
  --restart unless-stopped \
  --env-file "${ENV_FILE}" \
  -v "${PERSIST_DIR}:${PERSIST_DIR}" \
  -p "${HOST_PORT}:8000" \
  --memory="${DOCKER_MEMORY}" --memory-swap="${DOCKER_MEMORY_SWAP}" \
  "${IMAGE_NAME}"

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

echo "==> Redeploy complete"
