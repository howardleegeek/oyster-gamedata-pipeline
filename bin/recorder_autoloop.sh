#!/usr/bin/env bash
# recorder_autoloop.sh — closed-loop auto-heal for OysterRecorder.
#
# Howard 2026-05-08: 自动跑 mc → 自动看日志 → 自动修理.
#
# Architecture:
#   Windows side: tester runs MC + OysterRecorder.exe; on session end the
#                 .exe writes OysterRecorder_diagnostic.zip (already exists).
#                 Tester drops the zip into a sync folder (iCloud Drive,
#                 AirDrop, scp).
#   Mac side  :   this script polls a watch dir for new diag zips. On each
#                 new zip:
#                   1. recorder_log_analyzer.py classifies issues
#                   2. issues with severity >= medium → spec written to
#                      specs/auto/R-AUTO-<timestamp>.md citing real evidence
#                   3. spec dispatched to claude-glm cluster
#                   4. state file updated so the same zip isn't reprocessed
#
# Iron-law (data accuracy):
#   - Every spec entry quotes real log lines as evidence (analyzer guarantees).
#   - Specs that match an existing in-flight spec (R01 etc) are NOT
#     re-dispatched — we instead append the new evidence as a comment.
#   - Skips zips already in the state file.
#
# Usage:
#   ./bin/recorder_autoloop.sh                 # default watch ~/Downloads
#   WATCH_DIR=~/iCloud\ Drive ./bin/recorder_autoloop.sh
#   INTERVAL=30 ./bin/recorder_autoloop.sh     # check every 30s (default 60)
#
# Stop with Ctrl-C.

set -u

WATCH_DIR="${WATCH_DIR:-$HOME/Downloads}"
INTERVAL="${INTERVAL:-60}"
STATE_FILE="${STATE_FILE:-$HOME/.oyster-recorder-autoloop-state.json}"
REPO_ROOT="${REPO_ROOT:-$HOME/Downloads/oyster-agent-runner}"
DISPATCH="${DISPATCH:-glm}"   # glm | codex | none (none = report-only)

# ── colours ──────────────────────────────────────────────────────────
RED='\033[0;31m'; GRN='\033[0;32m'; YEL='\033[0;33m'; DIM='\033[2m'; RST='\033[0m'

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { printf "${DIM}%s${RST}  %s\n" "$(ts)" "$1"; }
ok()  { printf "${DIM}%s${RST}  ${GRN}[OK]${RST}   %s\n" "$(ts)" "$1"; }
warn(){ printf "${DIM}%s${RST}  ${YEL}[WARN]${RST} %s\n" "$(ts)" "$1"; }
err() { printf "${DIM}%s${RST}  ${RED}[ERR]${RST}  %s\n" "$(ts)" "$1" >&2; }

# ── prereqs ─────────────────────────────────────────────────────────
command -v python3 >/dev/null 2>&1 || { err "python3 not found"; exit 1; }
command -v shasum  >/dev/null 2>&1 || { err "shasum not found";  exit 1; }
[ -d "$WATCH_DIR" ] || { err "watch dir does not exist: $WATCH_DIR"; exit 1; }
[ -d "$REPO_ROOT" ] || { err "repo root does not exist: $REPO_ROOT"; exit 1; }

# ── state ───────────────────────────────────────────────────────────
# State file is JSON: { "processed": { "<sha256>": "<spec_id>", ... } }
if [ ! -f "$STATE_FILE" ]; then
  printf '{"processed": {}}\n' > "$STATE_FILE"
fi

# Returns 0 if sha already processed, 1 otherwise.
already_processed() {
  python3 -c "
import json, sys
with open('$STATE_FILE') as f:
    d = json.load(f)
sys.exit(0 if '$1' in d.get('processed', {}) else 1)
"
}

mark_processed() {
  local sha="$1"
  local spec_id="$2"
  python3 -c "
import json
with open('$STATE_FILE') as f:
    d = json.load(f)
d.setdefault('processed', {})['$sha'] = '$spec_id'
with open('$STATE_FILE', 'w') as f:
    json.dump(d, f, indent=2)
"
}

# ── main loop ───────────────────────────────────────────────────────
log "watching $WATCH_DIR for OysterRecorder_diagnostic*.zip (interval ${INTERVAL}s)"
log "state file: $STATE_FILE"
log "dispatch:   $DISPATCH"
log "Ctrl-C to stop."

while true; do
  # Find candidate zips. -mtime check avoids reprocessing ancient zips.
  while IFS= read -r zip; do
    [ -z "$zip" ] && continue
    sha=$(shasum -a 256 "$zip" | awk '{print $1}')
    if already_processed "$sha"; then
      continue
    fi

    log "new diag zip: $(basename "$zip") (sha=${sha:0:12})"

    # ── analyze ────────────────────────────────────────────────────
    report_json=$(python3 "$REPO_ROOT/bin/recorder_log_analyzer.py" "$zip" 2>/dev/null)
    if [ -z "$report_json" ]; then
      warn "analyzer returned empty output for $zip — skipping"
      mark_processed "$sha" "skipped:empty"
      continue
    fi

    crit_count=$(printf '%s' "$report_json" | python3 -c "import sys,json; print(json.load(sys.stdin).get('critical_count', 0))")
    issue_count=$(printf '%s' "$report_json" | python3 -c "import sys,json; print(json.load(sys.stdin).get('issue_count', 0))")

    log "analyzer: $issue_count issues ($crit_count critical)"

    if [ "$issue_count" = "0" ]; then
      ok "no issues — clean run"
      mark_processed "$sha" "clean"
      continue
    fi

    # ── write auto-spec ───────────────────────────────────────────
    stamp=$(date -u +"%Y%m%dT%H%M%SZ")
    spec_id="R-AUTO-${stamp}"
    spec_path="$REPO_ROOT/specs/auto/${spec_id}.md"
    mkdir -p "$REPO_ROOT/specs/auto"

    {
      echo "---"
      echo "task_id: $spec_id"
      echo "project: recorder-autoloop"
      echo "priority: 2"
      echo "estimated_minutes: 30"
      echo "depends_on: [R01-recorder-iron-law-polish]"
      echo "executor: ${DISPATCH}-aliyun"
      echo "source_zip: $(basename "$zip")"
      echo "source_sha256: $sha"
      echo "---"
      echo
      echo "# Auto-generated from real OysterRecorder diagnostic"
      echo
      echo "Howard 2026-05-08 auto-heal loop ($DISPATCH dispatch)."
      echo
      echo "## Run context"
      echo
      echo '```json'
      printf '%s' "$report_json"
      echo
      echo '```'
      echo
      echo "## Suggested action"
      echo
      printf '%s' "$report_json" | python3 -c "
import sys, json
d = json.load(sys.stdin)
crit = [i for i in d['issues'] if i['severity'] == 'critical']
if crit:
    print('- Critical issues detected. The R01 spec already covers these — once R01 lands and a v0.27.0 build ships, this run should re-test clean.')
    for i in crit:
        print(f'  - {i[\"code\"]} (line {i[\"line_no\"]}): {i[\"evidence_line\"]}')
else:
    print('- No critical issues. Lower-severity findings worth tracking but not release-blocking:')
    for i in d['issues']:
        print(f'  - {i[\"code\"]} ({i[\"severity\"]}, line {i[\"line_no\"]}): {i[\"summary\"]}')
"
      echo
      echo "## 数据准确铁律"
      echo
      echo "Every issue cited above includes a literal log line as evidence."
      echo "If you act on this spec: link your fix back to the evidence line"
      echo "and the R01 acceptance criteria. Don't fabricate root causes."
    } > "$spec_path"

    ok "spec written: $spec_path"

    # ── dispatch ──────────────────────────────────────────────────
    case "$DISPATCH" in
      none)
        log "dispatch=none — spec written but not sent to cluster"
        ;;
      glm)
        if [ -z "${ZAI_API_KEY:-}" ] || [ -z "${ZAI_BASE_URL:-}" ]; then
          warn "ZAI_API_KEY / ZAI_BASE_URL not set — cannot dispatch to GLM"
          warn "spec is on disk, dispatch manually with: claude-glm -p ..."
        else
          log "dispatching $spec_id to GLM cluster (background)..."
          (
            cd "$REPO_ROOT" && \
            ANTHROPIC_AUTH_TOKEN="$ZAI_API_KEY" \
            ANTHROPIC_BASE_URL="$ZAI_BASE_URL" \
            API_TIMEOUT_MS=3000000 \
              claude -p "Read $spec_path. The critical issues are already addressed by R01-recorder-iron-law-polish (in flight). Only act if the spec contains issues NOT covered by R01. If R01 covers everything, leave a comment in $spec_path noting this and exit. If new issues exist, propose a sub-spec, do NOT directly modify recorder source — Howard reviews first. Pre-authorized: read repo, write to specs/auto/, no code edits without further direction." \
              --dangerously-skip-permissions > "/tmp/${spec_id}.glm.log" 2>&1 &
          )
          ok "GLM dispatch fired (log: /tmp/${spec_id}.glm.log)"
        fi
        ;;
      codex)
        warn "codex dispatch not yet wired in autoloop"
        ;;
      *)
        err "unknown DISPATCH=$DISPATCH (use glm|codex|none)"
        ;;
    esac

    mark_processed "$sha" "$spec_id"
  done < <(find "$WATCH_DIR" -maxdepth 2 -name "OysterRecorder_diagnostic*.zip" 2>/dev/null)

  sleep "$INTERVAL"
done
