#!/usr/bin/env bash
# deploy_backend.sh – Deploy backend_stub to Fly.io
# Usage: ./scripts/deploy_backend.sh
#
# Requires: Fly.io CLI (`flyctl` or `fly`) installed and authenticated.
# Does NOT store any tokens in the repo.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKEND_DIR="${PROJECT_ROOT}/backend_stub"

find_fly_cli() {
  if command -v flyctl &>/dev/null; then
    command -v flyctl
    return 0
  fi

  if command -v fly &>/dev/null; then
    command -v fly
    return 0
  fi

  return 1
}

FLY_CLI="$(find_fly_cli || true)"
if [ -z "$FLY_CLI" ]; then
  echo "Error: Fly.io CLI is not installed or not in PATH." >&2
  exit 1
fi

if ! "$FLY_CLI" auth whoami &>/dev/null; then
  echo "Error: Not authenticated with Fly.io CLI. Run 'fly auth login' first." >&2
  exit 1
fi

echo "Deploying backend_stub to Fly.io with ${FLY_CLI}..."
"$FLY_CLI" deploy "${BACKEND_DIR}" --config "${BACKEND_DIR}/fly.toml"

echo "Deployment complete."
