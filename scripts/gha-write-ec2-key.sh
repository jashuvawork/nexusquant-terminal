#!/usr/bin/env bash
# Write EC2 SSH private key for GitHub Actions deploy steps.
# Sets GITHUB_OUTPUT deploy_skip=true when the key cannot be loaded.
set -euo pipefail

out="${GITHUB_OUTPUT:-/dev/stdout}"
key_path="${1:-ec2_key}"

write_fail() {
  echo "::warning::$1"
  echo "deploy_skip=true" >> "${out}"
  exit 0
}

rm -f "${key_path}"
if [[ -n "${SSH_KEY_B64:-}" ]]; then
  cleaned=$(printf '%s' "${SSH_KEY_B64}" | tr -d '\n\r\t ')
  pad=$(( (4 - ${#cleaned} % 4) % 4 ))
  for ((i = 0; i < pad; i++)); do cleaned+="="; done
  if ! printf '%s' "${cleaned}" | base64 -d > "${key_path}" 2>/dev/null; then
    write_fail "NEXUSQUANT_EC2_SSH_KEY_B64 is invalid base64. Delete it and re-add a fresh key (see workflow logs / repo docs)."
  fi
elif [[ -n "${SSH_KEY:-}" ]]; then
  printf '%s\n' "${SSH_KEY}" > "${key_path}"
else
  write_fail "Missing NEXUSQUANT_EC2_SSH_KEY or NEXUSQUANT_EC2_SSH_KEY_B64."
fi

chmod 600 "${key_path}"
if ! ssh-keygen -y -f "${key_path}" >/dev/null 2>&1; then
  rm -f "${key_path}"
  write_fail "EC2 SSH key secret is corrupt or truncated. Replace NEXUSQUANT_EC2_SSH_KEY_B64 in GitHub repo secrets."
fi

echo "deploy_skip=false" >> "${out}"
echo "EC2 SSH key loaded ($(wc -c < "${key_path}") bytes)"
