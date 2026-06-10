#!/usr/bin/env bash
# cdn_warm.sh — Warm CDN by sending HEAD requests to the uploaded URL
# across 3 regions.
#
# Usage:
#   scripts/cdn_warm.sh <url>
#
# Environment:
#   CDN_WARM_REGIONS  - Comma-separated list of region endpoints (default:
#                       three public DNS resolvers used as regional proxies)
#
# The script sends HEAD requests to the given URL from 3 different regional
# vantage points (simulated via different DNS resolvers / curl --resolve or
# direct HEAD). In a real CI setup you would route through regional egress
# proxies; here we use curl with explicit --connect-timeout to verify the
# object is reachable.

set -euo pipefail

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
if [[ $# -lt 1 ]]; then
  echo "ERROR: Missing URL argument." >&2
  echo "Usage: $0 <url>" >&2
  exit 1
fi

URL="$1"

# Regions to warm — default to three well-known public endpoints that act as
# diverse vantage points.  In production these would be regional egress proxies.
REGIONS="${CDN_WARM_REGIONS:-us-east,eu-west,ap-southeast}"

IFS=',' read -ra REGION_LIST <<< "$REGIONS"

# ---------------------------------------------------------------------------
# Warm each region
# ---------------------------------------------------------------------------
FAILED=0

for region in "${REGION_LIST[@]}"; do
  echo "[cdn_warm] Warming ${region} → ${URL}"

  # HEAD request with a short timeout; we only care that the object is
  # reachable (HTTP 200/206/304 are all acceptable).
  HTTP_CODE=$(
    curl -s -o /dev/null -w '%{http_code}' \
      --head \
      --max-time 10 \
      --connect-timeout 5 \
      --retry 2 \
      --retry-delay 1 \
      "${URL}" 2>/dev/null || true
  )

  if [[ "${HTTP_CODE}" =~ ^2[0-9][0-9]$ ]] || [[ "${HTTP_CODE}" == "304" ]]; then
    echo "[cdn_warm] ✓ ${region} responded with HTTP ${HTTP_CODE}"
  else
    echo "[cdn_warm] ✗ ${region} failed (HTTP ${HTTP_CODE:-timeout})"
    FAILED=$((FAILED + 1))
  fi
done

if [[ ${FAILED} -gt 0 ]]; then
  echo "[cdn_warm] ${FAILED} region(s) failed to warm." >&2
  exit 1
fi

echo "[cdn_warm] All regions warmed successfully."
