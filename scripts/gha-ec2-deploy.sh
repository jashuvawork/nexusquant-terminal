#!/usr/bin/env bash
# Deploy backend to EC2 from GitHub Actions.
# Uses SSH key secret if present; otherwise AWS EC2 Instance Connect + ephemeral key.
set -euo pipefail

: "${EC2_HOST:=13.206.45.57}"
: "${EC2_USER:=ubuntu}"
: "${EC2_INSTANCE_ID:=i-09ce535a67a0a6810}"
: "${AWS_REGION:=ap-south-1}"
: "${SSH_KEY_PATH:=ec2_key}"
: "${BACKEND_TARBALL:=backend.tar.gz}"

COMMIT_SHA="${GITHUB_SHA:-$(git rev-parse --short HEAD 2>/dev/null || echo unknown)}"

ssh_with_key() {
  ssh -i "${SSH_KEY_PATH}" -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=30 "${EC2_USER}@${EC2_HOST}" "$@"
}

scp_with_key() {
  scp -i "${SSH_KEY_PATH}" -o StrictHostKeyChecking=no -o ConnectTimeout=30 "$@"
}

setup_instance_connect() {
  echo "==> Using AWS EC2 Instance Connect"
  command -v aws >/dev/null || { echo "aws CLI required" >&2; exit 1; }
  rm -f "${SSH_KEY_PATH}" "${SSH_KEY_PATH}.pub"
  ssh-keygen -t rsa -f "${SSH_KEY_PATH}" -N "" -q
  local az
  az=$(aws ec2 describe-instances --instance-ids "${EC2_INSTANCE_ID}" --region "${AWS_REGION}" \
    --query 'Reservations[0].Instances[0].Placement.AvailabilityZone' --output text)
  aws ec2-instance-connect send-ssh-public-key \
    --instance-id "${EC2_INSTANCE_ID}" \
    --instance-os-user "${EC2_USER}" \
    --ssh-public-key "file://${SSH_KEY_PATH}.pub" \
    --availability-zone "${az}" \
    --region "${AWS_REGION}"
}

try_ssh_key_secret() {
  if [[ -n "${SSH_KEY_B64:-}" || -n "${SSH_KEY:-}" ]]; then
    local out
    out=$(mktemp)
    GITHUB_OUTPUT="${out}" bash scripts/gha-write-ec2-key.sh "${SSH_KEY_PATH}" || true
    if grep -q 'deploy_skip=true' "${out}" 2>/dev/null; then
      rm -f "${out}"
      return 1
    fi
    rm -f "${out}"
    if ssh-keygen -y -f "${SSH_KEY_PATH}" >/dev/null 2>&1 && ssh_with_key 'echo ok' >/dev/null 2>&1; then
      echo "ssh_secret"
      return 0
    fi
  fi
  return 1
}

AUTH_MODE=""
if try_ssh_key_secret; then
  AUTH_MODE="ssh_secret"
else
  setup_instance_connect
  AUTH_MODE="instance_connect"
fi

echo "==> Auth mode: ${AUTH_MODE}"
echo "==> Upload backend to EC2"
# Instance Connect keys expire quickly — refresh before each transfer
if [[ "${AUTH_MODE}" == "instance_connect" ]]; then
  setup_instance_connect >/dev/null
fi
scp_with_key "${BACKEND_TARBALL}" scripts/ec2-build-deploy.sh scripts/ec2-sync-trading-env.sh "${EC2_USER}@${EC2_HOST}:/tmp/"

if [[ "${AUTH_MODE}" == "instance_connect" ]]; then
  setup_instance_connect >/dev/null
fi

ssh_with_key "set -euo pipefail
  export NEXUSQUANT_BACKEND_COMMIT='${COMMIT_SHA}'
  chmod +x /tmp/ec2-build-deploy.sh /tmp/ec2-sync-trading-env.sh
  sudo NEXUSQUANT_BACKEND_COMMIT='${COMMIT_SHA}' /tmp/ec2-sync-trading-env.sh /opt/nexusquant/env
  rm -rf /tmp/nexusquant-backend
  mkdir -p /tmp/nexusquant-backend
  tar -xzf /tmp/${BACKEND_TARBALL} -C /tmp/nexusquant-backend --strip-components=1
  sudo SOURCE_DIR=/tmp/nexusquant-backend /tmp/ec2-build-deploy.sh"

echo "==> Deploy complete (${COMMIT_SHA})"
