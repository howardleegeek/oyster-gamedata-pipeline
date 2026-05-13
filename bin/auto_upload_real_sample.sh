#!/usr/bin/env bash
# =============================================================================
# auto_upload_real_sample.sh — push every D5-validated REAL=6 tarball to a
# pluggable storage backend (default: GitHub release for legacy compat).
#
# Howard 2026-05-07: GitHub release has 2 GiB hard cap per asset and 50 asset
# limit per release. To scale beyond ~100 testers/day we now route through
# bin/upload_tarball.py which selects backend via $STORAGE_BACKEND
# (github | s3 | local). Default = github so existing testers see no change.
#
# USAGE:
#   bin/auto_upload_real_sample.sh <tarball_path>
#
# Exit codes:
#   0  uploaded (or skipped because already-uploaded)
#   1  tarball missing or oversized for the chosen backend
#   2  D5 verdict not REAL=6
#   3  backend upload failed
#
# ENV VARS:
#   STORAGE_BACKEND        github (default) | s3 | local
#   STORAGE_GITHUB_REPO    (default howardleegeek/oyster-gamedata-pipeline)
#   STORAGE_GITHUB_TAG     (default real-data-sample-v1-20260507-0742)
#   STORAGE_S3_BUCKET      (required for s3)
#   STORAGE_S3_REGION      (default us-east-1)
#   STORAGE_S3_ENDPOINT_URL (optional — for R2/B2/MinIO)
#   AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (required for s3)
# =============================================================================

set -uo pipefail

REPO_DIR="${REPO_DIR_OVERRIDE:-/Users/howardli/Downloads/oyster-agent-runner}"
PY_BIN="${PY_BIN_OVERRIDE:-${REPO_DIR}/.venv/bin/python}"
LOG="${UPLOAD_LOG:-/tmp/auto_upload_real_sample.log}"
BACKEND="${STORAGE_BACKEND:-github}"

# Legacy env-var defaults (preserved so existing callers see no change).
export STORAGE_GITHUB_REPO="${STORAGE_GITHUB_REPO:-${REPO_OVERRIDE:-howardleegeek/oyster-gamedata-pipeline}}"
export STORAGE_GITHUB_TAG="${STORAGE_GITHUB_TAG:-real-data-sample-v1-20260507-0742}"
export STORAGE_GITHUB_KEEP_NEWEST="${STORAGE_GITHUB_KEEP_NEWEST:-3}"

log() { echo "[$(date +%H:%M:%S)] auto_upload[$BACKEND]: $*" | tee -a "$LOG" >&2; }

INPUT_TAR="${1:-}"
if [[ -z "$INPUT_TAR" ]]; then
    log "usage: $0 <tarball>"
    exit 1
fi

if [[ ! -f "$INPUT_TAR" ]]; then
    log "tarball missing: $INPUT_TAR"
    exit 1
fi

# 1. Per-backend size guard. GitHub is the only one with a hard cap we enforce
#    client-side; S3 and local accept anything (S3 multipart handles >2 GiB).
SIZE=$(stat -f %z "$INPUT_TAR" 2>/dev/null || stat -c %s "$INPUT_TAR" 2>/dev/null)
GITHUB_ASSET_LIMIT=2147483648   # 2 GiB exact (GitHub hard cap)
if [[ "$BACKEND" == "github" && "$SIZE" -gt "$GITHUB_ASSET_LIMIT" ]]; then
    log "skipping $INPUT_TAR — $SIZE bytes exceeds GitHub 2 GiB limit (use STORAGE_BACKEND=s3 instead)"
    exit 1
fi

# 2. Re-validate D5 (defense in depth — caller may have a different
#    tarball than what was just validated). Cheap (~5s) compared to wasted upload.
D5_OUT=$("$PY_BIN" "${REPO_DIR}/bin/tarball_authenticity_check.py" "$INPUT_TAR" 2>&1 | tail -10)
if ! echo "$D5_OUT" | grep -q "REAL=6  PLACEHOLDER=0  UNKNOWN=0"; then
    log "skipping $INPUT_TAR — D5 verdict not REAL=6"
    log "D5 output:"
    echo "$D5_OUT" | tee -a "$LOG" >&2
    exit 2
fi

# 3. Tester ID — deterministic from filename (stable across re-runs).
BASE=$(basename "$INPUT_TAR" .tar.gz)
TESTER_ID="${TESTER_ID_OVERRIDE:-tester-${BASE#swarm_real_}}"
TESTER_ID="${TESTER_ID:0:64}"  # trim to a reasonable upper bound

# 4. Hand off to the python CLI. It owns idempotency, signed-URL minting,
#    and per-backend specifics.
#
# Howard 2026-05-13 (Gap #8): when BACKEND=vercel-signed (or UPLOAD_BASE_URL is
# set), route through the direct-to-Supabase signed-URL client instead of the
# legacy backend dispatcher. The legacy `/api/upload-tarball` route now returns
# 410 Gone; any pipeline still calling upload_tarball.py against an HTTP backend
# will fail loudly. The signed-URL path requires --duration-seconds, which we
# don't get from the tarball, so callers must export UPLOAD_DURATION_SECONDS.
if [[ "$BACKEND" == "vercel-signed" || -n "${UPLOAD_BASE_URL:-}" ]]; then
    if [[ -z "${UPLOAD_DURATION_SECONDS:-}" ]]; then
        log "ERROR: vercel-signed backend requires UPLOAD_DURATION_SECONDS in env"
        exit 1
    fi
    log "uploading $INPUT_TAR via vercel-signed (size=$((SIZE / 1024 / 1024)) MB, tester=$TESTER_ID)"
    RESULT=$("$PY_BIN" "${REPO_DIR}/bin/upload_tarball_signed.py" "$INPUT_TAR" \
        --tester-id "$TESTER_ID" \
        --duration-seconds "$UPLOAD_DURATION_SECONDS" \
        --base-url "$UPLOAD_BASE_URL" 2>>"$LOG")
    RC=$?
else
    log "uploading $INPUT_TAR via backend=$BACKEND (size=$((SIZE / 1024 / 1024)) MB, tester=$TESTER_ID)"
    RESULT=$("$PY_BIN" "${REPO_DIR}/bin/upload_tarball.py" "$INPUT_TAR" \
        --tester-id "$TESTER_ID" \
        --d5-verdict "REAL" \
        --backend "$BACKEND" 2>>"$LOG")
    RC=$?
fi

if [[ $RC -ne 0 ]]; then
    log "upload step failed (rc=$RC, backend=$BACKEND)"
    exit 3
fi

# Surface the JSON result to the log for downstream debugging.
log "DONE: $RESULT"
exit 0
