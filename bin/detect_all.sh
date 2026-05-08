#!/usr/bin/env bash
# detect_all.sh — unified auto-detection orchestrator.
#
# Howard 2026-05-08: 自动检测系统. Aggregates 6 detection layers into a
# single live dashboard + a JSON status file (/tmp/oyster-detect-status.json)
# + macOS-native notifications on state transitions.
#
# Detection layers:
#   L0 — Mac host  : disk, load
#   L1 — Local stack : Supabase containers, web-tester :3000, web-buyer :3001
#   L2 — Auto-heal daemons : recorder_autoloop.sh, watch.sh
#   L3 — Cluster jobs : in-flight GLM/Codex dispatches
#   L4 — CI lanes : last N gh runs
#   L5 — Auto-spec backlog : specs/auto/*.md awaiting dispatch
#
# Iron-law (data accuracy):
#   - Every status entry is a real probe / real ps / real gh API call.
#   - No "looks healthy" without a real source backing it.
#   - macOS notifications fire only on RED transitions (no spam).
#
# Usage:
#   bin/detect_all.sh                    # one-shot, prints + writes status file
#   INTERVAL=30 bin/detect_all.sh loop   # daemon mode, refresh every 30s
#   bin/detect_all.sh json               # one-shot JSON only (machine-readable)

set -u

REPO_ROOT="${REPO_ROOT:-$HOME/Downloads/oyster-agent-runner}"
STATUS_FILE="${STATUS_FILE:-/tmp/oyster-detect-status.json}"
PREV_FILE="${PREV_FILE:-/tmp/oyster-detect-prev.json}"
INTERVAL="${INTERVAL:-60}"
NOTIFY="${NOTIFY:-true}"   # set to false to suppress macOS notifications

# ── colours ──────────────────────────────────────────────────────────
RED='\033[0;31m'; GRN='\033[0;32m'; YEL='\033[0;33m'; BLU='\033[0;34m'
DIM='\033[2m'; BOLD='\033[1m'; RST='\033[0m'

# Color a status: GREEN ok, YELLOW warn, RED critical, dim unknown.
color() {
  case "$1" in
    GREEN)  printf "${GRN}%-7s${RST}" "$1" ;;
    YELLOW) printf "${YEL}%-7s${RST}" "$1" ;;
    RED)    printf "${RED}%-7s${RST}" "$1" ;;
    *)      printf "${DIM}%-7s${RST}" "$1" ;;
  esac
}

# Add a row to the in-progress detection. Args: layer code label status detail
# We accumulate rows in two temp files so we can both print and JSON-encode.
ROWS_HUMAN=$(mktemp -t oyster-det.XXXXXX)
ROWS_JSON=$(mktemp -t oyster-det-json.XXXXXX)
echo "[" > "$ROWS_JSON"
ROW_FIRST=1

cleanup() { rm -f "$ROWS_HUMAN" "$ROWS_JSON" 2>/dev/null; }
trap cleanup EXIT

row() {
  local layer="$1" code="$2" label="$3" status="$4" detail="$5"
  printf "%s  %-22s  %s  %s\n" "$layer" "$label" "$(color "$status")" "$detail" >> "$ROWS_HUMAN"
  if [ "$ROW_FIRST" = "1" ]; then
    ROW_FIRST=0
  else
    echo "," >> "$ROWS_JSON"
  fi
  python3 -c "
import json, sys
print(json.dumps({
    'layer': '$layer',
    'code': '$code',
    'label': '$label',
    'status': '$status',
    'detail': '''$detail''',
}, ensure_ascii=False))
" >> "$ROWS_JSON"
}

# ── L0 : Mac host ───────────────────────────────────────────────────
detect_l0_host() {
  local disk_pct disk_free
  disk_pct=$(df -h / | awk 'NR==2 {gsub("%",""); print $5}')
  disk_free=$(df -h / | awk 'NR==2 {print $4}')

  local disk_status="GREEN"
  if [ "$disk_pct" -ge 95 ]; then
    disk_status="RED"
  elif [ "$disk_pct" -ge 85 ]; then
    disk_status="YELLOW"
  fi
  row "L0" "host.disk" "host disk" "$disk_status" "${disk_pct}% used, ${disk_free} free"

  local load
  load=$(uptime | awk -F'load averages:' '{print $2}' | awk '{print $1}')
  row "L0" "host.load" "host load (1m)" "GREEN" "$load"
}

# ── L1 : Local stack ────────────────────────────────────────────────
detect_l1_stack() {
  # Supabase containers
  local sb_total sb_healthy
  sb_total=$(docker ps --filter "name=supabase_" -q 2>/dev/null | wc -l | tr -d ' ')
  sb_healthy=$(docker ps --filter "name=supabase_" --filter "health=healthy" -q 2>/dev/null | wc -l | tr -d ' ')
  local sb_status="GREEN"
  [ "$sb_total" = "0" ] && sb_status="RED"
  [ "$sb_total" != "0" ] && [ "$sb_healthy" -lt "$sb_total" ] && sb_status="YELLOW"
  row "L1" "supabase.containers" "supabase containers" "$sb_status" "${sb_healthy}/${sb_total} healthy"

  # Tester portal
  local code
  code=$(curl -sIL -o /dev/null -m 5 -w "%{http_code}" "http://localhost:3000/" 2>/dev/null || echo "000")
  code="${code: -3}"
  case "$code" in
    2*|3*) row "L1" "portal.tester" "tester :3000" "GREEN" "HTTP $code" ;;
    000)   row "L1" "portal.tester" "tester :3000" "RED"   "no response" ;;
    *)     row "L1" "portal.tester" "tester :3000" "YELLOW" "HTTP $code" ;;
  esac

  # Buyer portal
  code=$(curl -sIL -o /dev/null -m 5 -w "%{http_code}" "http://localhost:3001/" 2>/dev/null || echo "000")
  code="${code: -3}"
  case "$code" in
    2*|3*) row "L1" "portal.buyer" "buyer :3001" "GREEN" "HTTP $code" ;;
    000)   row "L1" "portal.buyer" "buyer :3001" "RED"   "no response" ;;
    *)     row "L1" "portal.buyer" "buyer :3001" "YELLOW" "HTTP $code" ;;
  esac

  # Catalog API (proves Supabase + buyer wiring)
  code=$(curl -sIL -o /dev/null -m 5 -w "%{http_code}" "http://localhost:3001/api/catalog?limit=1" 2>/dev/null || echo "000")
  code="${code: -3}"
  case "$code" in
    200) row "L1" "portal.catalog" "/api/catalog" "GREEN" "HTTP 200 (real DB)" ;;
    000) row "L1" "portal.catalog" "/api/catalog" "RED"   "no response" ;;
    *)   row "L1" "portal.catalog" "/api/catalog" "YELLOW" "HTTP $code" ;;
  esac
}

# ── L2 : Auto-heal daemons ──────────────────────────────────────────
detect_l2_daemons() {
  # recorder autoloop
  if pgrep -f "recorder_autoloop.sh" >/dev/null 2>&1; then
    local pid
    pid=$(pgrep -f "recorder_autoloop.sh" | head -1)
    row "L2" "daemon.autoloop" "recorder autoloop" "GREEN" "PID $pid"
  else
    row "L2" "daemon.autoloop" "recorder autoloop" "YELLOW" "not running (start: ./bin/recorder_autoloop.sh &)"
  fi

  # watch.sh (production monitor) — optional, may not be running
  if pgrep -f "watch.sh" >/dev/null 2>&1; then
    local pid
    pid=$(pgrep -f "watch.sh" | head -1)
    row "L2" "daemon.watch" "production watch.sh" "GREEN" "PID $pid"
  else
    row "L2" "daemon.watch" "production watch.sh" "YELLOW" "not running (optional in local dev)"
  fi
}

# ── L3 : Cluster jobs ───────────────────────────────────────────────
detect_l3_cluster() {
  # Count claude/codex processes still running.
  local running
  running=$(pgrep -fc "claude .* dangerously-skip\|codex exec" 2>/dev/null || echo 0)
  if [ "$running" -gt 0 ]; then
    row "L3" "cluster.in_flight" "cluster jobs" "GREEN" "$running running"
  else
    row "L3" "cluster.in_flight" "cluster jobs" "GREEN" "0 running (idle)"
  fi
}

# ── L4 : CI lanes ───────────────────────────────────────────────────
detect_l4_ci() {
  # Latest gh run conclusion summary. gh may be slow / unauthed; cap.
  local out
  out=$(timeout=10 gh run list --limit 5 --json conclusion 2>/dev/null \
        | python3 -c "
import sys, json
try:
    runs = json.load(sys.stdin)
except Exception:
    print('unknown'); raise SystemExit
fail = sum(1 for r in runs if r.get('conclusion') == 'failure')
ok   = sum(1 for r in runs if r.get('conclusion') == 'success')
prog = sum(1 for r in runs if not r.get('conclusion'))
print(f'fail={fail} ok={ok} running={prog}')
" 2>/dev/null) || out="unknown"
  if [ -z "$out" ] || [ "$out" = "unknown" ]; then
    row "L4" "ci.recent" "ci recent (5)" "YELLOW" "gh unavailable"
    return
  fi
  fail=$(echo "$out" | sed -n 's/.*fail=\([0-9]*\).*/\1/p')
  fail="${fail:-0}"
  if [ "$fail" -gt 2 ]; then
    row "L4" "ci.recent" "ci recent (5)" "RED" "$out"
  elif [ "$fail" -gt 0 ]; then
    row "L4" "ci.recent" "ci recent (5)" "YELLOW" "$out"
  else
    row "L4" "ci.recent" "ci recent (5)" "GREEN" "$out"
  fi
}

# ── L5 : Auto-spec backlog ──────────────────────────────────────────
detect_l5_backlog() {
  local count
  count=$(find "$REPO_ROOT/specs/auto" -name "R-AUTO-*.md" 2>/dev/null | wc -l | tr -d ' ')
  if [ "$count" -gt 5 ]; then
    row "L5" "specs.auto" "auto-spec backlog" "YELLOW" "$count specs in specs/auto/ (review!)"
  else
    row "L5" "specs.auto" "auto-spec backlog" "GREEN" "$count specs in specs/auto/"
  fi
}

# ── compose JSON output ─────────────────────────────────────────────
finalize_status() {
  echo "]" >> "$ROWS_JSON"
  local now critical_count yellow_count
  now=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

  python3 -c "
import json, sys
with open('$ROWS_JSON') as f:
    rows = json.load(f)
crit = sum(1 for r in rows if r['status'] == 'RED')
warn = sum(1 for r in rows if r['status'] == 'YELLOW')
ok   = sum(1 for r in rows if r['status'] == 'GREEN')
overall = 'RED' if crit > 0 else ('YELLOW' if warn > 0 else 'GREEN')
out = {
    'ts': '$now',
    'overall': overall,
    'counts': {'red': crit, 'yellow': warn, 'green': ok},
    'rows': rows,
}
with open('$STATUS_FILE', 'w') as f:
    json.dump(out, f, indent=2, ensure_ascii=False)
" 2>/dev/null

  # Print human view
  printf "${BOLD}═══ Oyster Auto-Detection — %s ═══${RST}\n" "$now"
  printf "  layer  detector                status   detail\n"
  printf "  ${DIM}─────  ──────────────────────  ───────  ──────────────────────────${RST}\n"
  cat "$ROWS_HUMAN"
  local overall
  overall=$(python3 -c "import json; print(json.load(open('$STATUS_FILE'))['overall'])" 2>/dev/null)
  printf "  ${BOLD}overall${RST}: %s   (status file: %s)\n" "$(color "$overall")" "$STATUS_FILE"
}

# ── notification on transitions ─────────────────────────────────────
maybe_notify() {
  [ "$NOTIFY" != "true" ] && return
  command -v osascript >/dev/null 2>&1 || return
  [ ! -f "$STATUS_FILE" ] && return
  [ ! -f "$PREV_FILE" ] && cp "$STATUS_FILE" "$PREV_FILE" && return

  python3 -c "
import json
cur = json.load(open('$STATUS_FILE'))
prev = json.load(open('$PREV_FILE'))
cur_red = {r['code'] for r in cur['rows'] if r['status'] == 'RED'}
prev_red = {r['code'] for r in prev['rows'] if r['status'] == 'RED'}
new_reds = cur_red - prev_red
if new_reds:
    print('|'.join(new_reds))
" 2>/dev/null | while IFS= read -r new_reds; do
    [ -z "$new_reds" ] && continue
    osascript -e "display notification \"new RED: $new_reds\" with title \"Oyster Auto-Detect\" sound name \"Glass\"" 2>/dev/null
  done
  cp "$STATUS_FILE" "$PREV_FILE"
}

# ── main ────────────────────────────────────────────────────────────
run_once() {
  detect_l0_host
  detect_l1_stack
  detect_l2_daemons
  detect_l3_cluster
  detect_l4_ci
  detect_l5_backlog
  finalize_status
  maybe_notify
}

case "${1:-once}" in
  once)
    run_once
    ;;
  json)
    run_once >/dev/null
    cat "$STATUS_FILE"
    ;;
  loop)
    while true; do
      # Reset row buffers each cycle
      : > "$ROWS_HUMAN"
      echo "[" > "$ROWS_JSON"
      ROW_FIRST=1
      clear
      run_once
      sleep "$INTERVAL"
    done
    ;;
  *)
    echo "usage: $0 {once|json|loop}"
    exit 1
    ;;
esac
