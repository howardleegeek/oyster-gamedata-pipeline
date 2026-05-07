#!/usr/bin/env bash
# =============================================================================
# auto_upload_watcher.sh — side-car daemon that watches /tmp for new
# swarm_real_*.tar.gz files produced by the cluster, validates them with D5,
# and auto-uploads REAL=6 ones to the GitHub real-data-sample-v1 release.
#
# Howard 2026-05-07 iron-law: testers must always be able to download the
# freshest real sample without manual ops intervention.
#
# Decoupled from swarm_controller.sh — runs independently, idempotent so
# re-runs are no-ops if the same tarball is seen twice.
#
# RUN:
#   nohup bash bin/auto_upload_watcher.sh > /tmp/auto_upload_watcher.log 2>&1 &
#
# STOP:
#   pkill -f auto_upload_watcher.sh
# =============================================================================

set -uo pipefail

REPO_DIR="${REPO_DIR_OVERRIDE:-/Users/howardli/Downloads/oyster-agent-runner}"
WATCH_PATTERN="/tmp/swarm_real_*.tar.gz"
SEEN_LIST="/tmp/auto_upload_seen.txt"
LOG="${WATCHER_LOG:-/tmp/auto_upload_watcher.log}"
INTERVAL=60   # poll every 60s

touch "$SEEN_LIST"

log() { echo "[$(date +%H:%M:%S)] watcher: $*" | tee -a "$LOG" >&2; }

log "=== auto_upload_watcher START (pattern: $WATCH_PATTERN) ==="

while true; do
    for TAR in $WATCH_PATTERN; do
        [ -f "$TAR" ] || continue
        # skip if already seen (idempotency — re-runs no-op)
        if grep -qFx "$TAR" "$SEEN_LIST"; then
            continue
        fi
        # skip if file is still being written (mtime < 60s old)
        AGE=$(( $(date +%s) - $(stat -f %m "$TAR" 2>/dev/null || stat -c %Y "$TAR") ))
        if [ "$AGE" -lt 60 ]; then
            log "skip $TAR — too fresh (${AGE}s old, may still be writing)"
            continue
        fi
        log "new tarball: $TAR — invoking auto_upload_real_sample.sh"
        bash "${REPO_DIR}/bin/auto_upload_real_sample.sh" "$TAR" >> "$LOG" 2>&1
        RC=$?
        echo "$TAR" >> "$SEEN_LIST"
        log "auto_upload exit=$RC for $TAR"
    done
    sleep $INTERVAL
done
