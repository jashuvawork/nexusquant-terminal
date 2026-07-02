#!/usr/bin/env bash
# Pause NexusQuant on EC2 from GitHub Actions (SSH / Instance Connect).
set -euo pipefail

: "${EC2_HOST:=[REDACTED]}"
: "${EC2_USER:=ubuntu}"
: "${EC2_INSTANCE_ID:=i-09ce535a67a0a6810}"
: "${AWS_REGION:=[REDACTED]}"
: "${SSH_KEY_PATH:=ec2_key}"
: "${STOP_EC2_INSTANCE:=false}"

ssh_with_key() {
  ssh -i "${SSH_KEY_PATH}" -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=30 "${EC2_USER}@${EC2_HOST}" "$@"
}

scp_with_key() {
  scp -i "${SSH_KEY_PATH}" -o StrictHostKeyChecking=no -o ConnectTimeout=30 "$@"
}

setup_instance_connect() {
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
    GITHUB_OUTPUT="$(mktemp)" bash scripts/gha-write-ec2-key.sh "${SSH_KEY_PATH}" || true
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
if [[ "${AUTH_MODE}" == "instance_connect" ]]; then
  setup_instance_connect >/dev/null
fi
scp_with_key scripts/ec2-pause-all.sh "${EC2_USER}@${EC2_HOST}:/tmp/ec2-pause-all.sh"
if [[ "${AUTH_MODE}" == "instance_connect" ]]; then
  setup_instance_connect >/dev/null
fi
ssh_with_key "chmod +x /tmp/ec2-pause-all.sh && sudo /tmp/ec2-pause-all.sh"

if [[ "${STOP_EC2_INSTANCE}" == "true" ]]; then
  echo "==> Stopping EC2 instance ${EC2_INSTANCE_ID}"
  aws ec2 stop-instances --instance-ids "${EC2_INSTANCE_ID}" --region "${AWS_REGION}"
  aws ec2 wait instance-stopped --instance-ids "${EC2_INSTANCE_ID}" --region "${AWS_REGION}"
  echo "==> EC2 instance stopped"
fi

echo "==> NexusQuant AWS pause complete"
