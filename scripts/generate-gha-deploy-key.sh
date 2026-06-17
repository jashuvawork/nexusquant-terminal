#!/usr/bin/env bash
# Generate an SSH key for GitHub Actions → EC2 deploy.
# Run locally, then paste the printed base64 into GitHub secret NEXUSQUANT_EC2_SSH_KEY_B64.
set -euo pipefail

KEY_PATH="${1:-./nexusquant-gha-deploy}"
EC2_HOST="${EC2_HOST:-13.206.45.57}"
EC2_USER="${EC2_USER:-ubuntu}"

ssh-keygen -t ed25519 -f "${KEY_PATH}" -N "" -C "github-actions-nexusquant-deploy"
echo ""
echo "==> Add public key to EC2 (one-time):"
echo "ssh-copy-id -i ${KEY_PATH}.pub ${EC2_USER}@${EC2_HOST}"
echo ""
echo "==> GitHub secret NEXUSQUANT_EC2_SSH_KEY_B64 (delete old value first):"
if base64 --help 2>&1 | grep -q '\-w'; then
  base64 -w0 "${KEY_PATH}"
else
  base64 < "${KEY_PATH}" | tr -d '\n'
fi
echo ""
echo ""
echo "Done. Private key kept at ${KEY_PATH} — do not commit it."
