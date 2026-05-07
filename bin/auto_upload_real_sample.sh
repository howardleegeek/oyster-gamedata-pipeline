#!/usr/bin/env bash
# =============================================================================
# auto_upload_real_sample.sh — push every D5-validated REAL=6 tarball to the
# `real-data-sample-v1-20260507-0742` GitHub release as a rotating buffer.
#
# Howard 2026-05-07 iron-law: testers must always be able to download the
# freshest real sample without us being on-call to push it manually. This
# hook is wired into swarm_controller.sh after D5 returns "Verdict: REAL".
#
# USAGE:
#   bin/auto_upload_real_sample.sh <tarball_path>
#
# Exit codes:
#   0  uploaded (or skipped because already-uploaded)
#   1  tarball missing or oversized (>2 GiB GitHub asset limit)
#   2  D5 verdict not REAL=6
#   3  gh CLI call failed
#
# RELEASE TAG (hardcoded — change here, not in caller):
#   real-data-sample-v1-20260507-0742
#
# ROTATING BUFFER POLICY:
#   - keep newest 3 oyster_REAL6_*.tar.gz assets
#   - delete older ones (the smaller oyster_REAL6_test.tar.gz "seed" asset
#     is preserved indefinitely as a stable known-good 18 MB reference)
# =============================================================================

set -uo pipefail

REPO="${REPO_OVERRIDE:-howardleegeek/oyster-gamedata-pipeline}"
TAG="real-data-sample-v1-20260507-0742"
GITHUB_ASSET_LIMIT=2147483648   # 2 GiB exact (GitHub hard cap)
KEEP_NEWEST=3
PROTECTED_ASSET="oyster_REAL6_test.tar.gz"  # stable seed sample
LOG="${UPLOAD_LOG:-/tmp/auto_upload_real_sample.log}"

log() { echo "[$(date +%H:%M:%S)] auto_upload: $*" | tee -a "$LOG" >&2; }

INPUT_TAR="${1:-}"
if [[ -z "$INPUT_TAR" ]]; then
    log "usage: $0 <tarball>"
    exit 1
fi

if [[ ! -f "$INPUT_TAR" ]]; then
    log "tarball missing: $INPUT_TAR"
    exit 1
fi

# 1. size check (GitHub silently 422s tarballs > 2 GiB)
SIZE=$(stat -f %z "$INPUT_TAR" 2>/dev/null || stat -c %s "$INPUT_TAR" 2>/dev/null)
if [[ "$SIZE" -gt "$GITHUB_ASSET_LIMIT" ]]; then
    log "skipping $INPUT_TAR — $SIZE bytes exceeds GitHub 2 GiB limit"
    exit 1
fi

# 2. re-validate D5 (defense in depth — caller may have a different
#    tarball than what was just validated). This is cheap (~5s) compared
#    to a tester downloading 2 GB of bad data.
REPO_DIR="${REPO_DIR_OVERRIDE:-/Users/howardli/Downloads/oyster-agent-runner}"
PY_BIN="${REPO_DIR}/.venv/bin/python"
D5_OUT=$("$PY_BIN" "${REPO_DIR}/bin/tarball_authenticity_check.py" "$INPUT_TAR" 2>&1 | tail -10)
if ! echo "$D5_OUT" | grep -q "REAL=6  PLACEHOLDER=0  UNKNOWN=0"; then
    log "skipping $INPUT_TAR — D5 verdict not REAL=6"
    log "D5 output:\n$D5_OUT"
    exit 2
fi

# 3. compute uploaded asset name from input filename
BASE=$(basename "$INPUT_TAR" .tar.gz)
ASSET_NAME="oyster_REAL6_${BASE#swarm_real_}.tar.gz"  # strip swarm_real_ prefix

# 4. check if already uploaded (idempotent — re-runs are no-ops)
EXISTING=$(gh release view "$TAG" --repo "$REPO" --json assets 2>/dev/null \
    | python3 -c "import json,sys; print(' '.join(a['name'] for a in json.load(sys.stdin).get('assets', [])))")
if [[ " $EXISTING " == *" $ASSET_NAME "* ]]; then
    log "asset $ASSET_NAME already uploaded — skip"
    exit 0
fi

# 5. upload with rename
log "uploading $INPUT_TAR as $ASSET_NAME ($((SIZE / 1024 / 1024)) MB)"
TMP_LINK=$(mktemp -d)/"$ASSET_NAME"
ln -sf "$INPUT_TAR" "$TMP_LINK"
if ! gh release upload "$TAG" "$TMP_LINK" --repo "$REPO" --clobber 2>>"$LOG"; then
    log "gh upload failed for $ASSET_NAME"
    rm -rf "$(dirname "$TMP_LINK")"
    exit 3
fi
rm -rf "$(dirname "$TMP_LINK")"

# 6. rotating buffer — keep newest KEEP_NEWEST oyster_REAL6_*.tar.gz assets
#    that are NOT the protected seed. Older ones removed.
log "pruning old assets (keep newest $KEEP_NEWEST + $PROTECTED_ASSET)"
gh release view "$TAG" --repo "$REPO" --json assets 2>/dev/null \
    | python3 -c "
import json, sys
assets = json.load(sys.stdin).get('assets', [])
# keep oyster_REAL6_*.tar.gz only; sort by createdAt desc; skip protected
oysters = [a for a in assets if a['name'].startswith('oyster_REAL6_') and a['name'] != '$PROTECTED_ASSET']
oysters.sort(key=lambda a: a.get('createdAt', ''), reverse=True)
to_delete = oysters[$KEEP_NEWEST:]
for a in to_delete:
    print(a['name'])
" | while read -r OLD; do
    [[ -z "$OLD" ]] && continue
    log "deleting old asset: $OLD"
    gh release delete-asset "$TAG" "$OLD" --repo "$REPO" --yes 2>>"$LOG" || log "  delete-asset failed: $OLD"
done

log "DONE — $ASSET_NAME uploaded, buffer trimmed to newest $KEEP_NEWEST"
exit 0
