#!/usr/bin/env bash
# deploy_backend.sh – Deploy backend_stub to Fly.io
# Usage: ./scripts/deploy_backend.sh
#
# Requires: flyctl installed and authenticated.
# Does NOT store any tokens in the repo.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKEND_DIR="${PROJECT_ROOT}/backend_stub"

if ! command -v flyctl &>/dev/null; then
  echo "Error: flyctl is not installed or not in PATH." >&2
  exit 1
fi

if ! flyctl auth whoami &>/dev/null; then
  echo "Error: Not authenticated with flyctl. Run 'flyctl auth login' first." >&2
  exit 1
fi

echo "Deploying backend_stub to Fly.io..."
flyctl deploy "${BACKEND_DIR}" --config "${BACKEND_DIR}/fly.toml"

echo "Deployment complete."
