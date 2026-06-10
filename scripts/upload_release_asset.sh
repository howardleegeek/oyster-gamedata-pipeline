#!/usr/bin/env bash
# upload_release_asset.sh — Upload OysterRecorder-setup-*.exe to a GitHub Release.
#
# Usage:
#   bash scripts/upload_release_asset.sh <tag> [artifact_dir]
#
# Arguments:
#   tag          — GitHub release tag (e.g. recorder-v1.2.3)
#   artifact_dir — directory containing the built installer (default: dist/)
#
# Environment:
#   GH_TOKEN     — required for gh CLI authentication
#
# Behaviour:
#   1. Validates inputs and prerequisites
#   2. Finds OysterRecorder-setup-*.exe in artifact_dir
#   3. Runs: gh release upload <tag> <file> --clobber
#   4. Generates SHA256SUMS.txt and uploads it too
#
# Exit codes:
#   0 — success
#   1 — validation / gh CLI failure

set -euo pipefail

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log() {
  echo "[upload-release-asset] $*"
}

die() {
  echo "[upload-release-asset] ERROR: $*" >&2
  exit 1
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

TAG="${1:-}"
ARTIFACT_DIR="${2:-dist}"

if [ -z "$TAG" ]; then
  die "Usage: $0 <tag> [artifact_dir]"
fi

# Validate tag format — must look like a semver tag
if ! echo "$TAG" | grep -qE '^v[0-9]+\.[0-9]+\.[0-9]+' && \
   ! echo "$TAG" | grep -qE '^recorder-v[0-9]+\.[0-9]+\.[0-9]+'; then
  die "Tag '${TAG}' does not look like a valid semver tag (expected v* or recorder-v*)"
fi

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

if ! command -v gh &>/dev/null; then
  die "gh CLI is required but not found in PATH"
fi

if [ -z "${GH_TOKEN:-}" ]; then
  die "GH_TOKEN environment variable is not set"
fi

if [ ! -d "$ARTIFACT_DIR" ]; then
  die "Artifact directory '${ARTIFACT_DIR}' does not exist"
fi

# ---------------------------------------------------------------------------
# Find installer executable
# ---------------------------------------------------------------------------

shopt -s nullglob
INSTALLERS=("${ARTIFACT_DIR}"/OysterRecorder-setup-*.exe "${ARTIFACT_DIR}"/OysterRecorder-Setup-*.exe)
shopt -u nullglob

if [ ${#INSTALLERS[@]} -eq 0 ]; then
  die "No OysterRecorder-setup-*.exe found in ${ARTIFACT_DIR}"
fi

log "Found ${#INSTALLERS[@]} installer(s) in ${ARTIFACT_DIR}"

# ---------------------------------------------------------------------------
# Upload each installer to the release
# ---------------------------------------------------------------------------

for installer in "${INSTALLERS[@]}"; do
  filename=$(basename "$installer")
  log "Uploading ${filename} to release ${TAG}..."
  gh release upload "$TAG" "$installer" --clobber
  log "Successfully uploaded ${filename}"
done

# ---------------------------------------------------------------------------
# Generate and upload SHA256SUMS.txt
# ---------------------------------------------------------------------------

SHA_FILE="${ARTIFACT_DIR}/SHA256SUMS.txt"

log "Generating SHA256SUMS.txt..."
(
  cd "$ARTIFACT_DIR"
  sha256sum OysterRecorder-setup-*.exe OysterRecorder-Setup-*.exe 2>/dev/null \
    | sort > SHA256SUMS.txt || true
)

if [ -s "$SHA_FILE" ]; then
  log "SHA256SUMS.txt contents:"
  cat "$SHA_FILE" | while IFS= read -r line; do
    log "  $line"
  done

  log "Uploading SHA256SUMS.txt to release ${TAG}..."
  gh release upload "$TAG" "$SHA_FILE" --clobber
  log "Successfully uploaded SHA256SUMS.txt"
else
  log "SHA256SUMS.txt is empty — skipping upload"
fi

log "Done — all assets uploaded to release ${TAG}"
