#!/usr/bin/env bash
# watch.sh — Production health monitor for the GameData pipeline.
#
# Single-pane real-time view of the production surfaces:
#   - tester portal (homepage + /docs + /download)
#   - buyer  portal (homepage + /browse + /api/catalog row count)
#   - GitHub Release .exe download URL (the recorder testers download)
#   - local disk pressure (the box running this script)
#
# Howard 2026-05-08 IRON-LAW: only honest signals — HTTP codes from the
# real production deployments + the real catalog row count. No fabricated
# metrics. Designed to run 24/7, not just during a launch window.
#
# Usage:
#   TESTER_URL=https://tester.example.com \
#   BUYER_URL=https://buyer.example.com \
#   ./watch.sh
#
# Defaults to localhost:3000 / 3001 (matches `next dev` for both portals).
# Press Ctrl-C to exit.

set -u

TESTER_URL="${TESTER_URL:-http://localhost:3000}"
BUYER_URL="${BUYER_URL:-http://localhost:3001}"
EXE_URL="${EXE_URL:-https://github.com/howardleegeek/oyster-gamedata-pipeline/releases/download/recorder-v0.26.0-real-game-state/OysterRecorder.exe}"
INTERVAL="${INTERVAL:-10}"

# ANSI colors
RED='\033[0;31m'; YEL='\033[0;33m'; GRN='\033[0;32m'; DIM='\033[2m'; RST='\033[0m'

# Color a HTTP code: 2xx/3xx green, 4xx yellow, 5xx/000 red.
color_code() {
  local code="$1"
  case "$code" in
    2??|3??) printf "${GRN}%s${RST}" "$code" ;;
    4??)     printf "${YEL}%s${RST}" "$code" ;;
    *)       printf "${RED}%s${RST}" "$code" ;;
  esac
}

# HEAD a URL, return the HTTP code (000 on timeout / DNS fail). When the
# request follows redirects, curl emits one HTTP code per hop — take the
# LAST 3 chars so we always show a single 3-digit status.
http_head() {
  local raw
  raw="$(curl -sIL -o /dev/null -m 8 -w "%{http_code}" "$1" 2>/dev/null)"
  if [ -z "$raw" ]; then echo "000"; return; fi
  # Tail-3 — handles "000000" or "302200" (redirect-then-200) cleanly.
  echo "${raw: -3}"
}

# GET /api/catalog and extract row count from JSON. Empty / 5xx -> "?".
catalog_count() {
  local body
  body="$(curl -sL -m 8 "$BUYER_URL/api/catalog?limit=200" 2>/dev/null || true)"
  if [ -z "$body" ]; then echo "?"; return; fi
  # Try common response shapes: {rows:[...]}, {data:{rows:[...]}}, {total:N}, [...]
  python3 - "$body" <<'PY' 2>/dev/null || echo "?"
import json, sys
try:
    raw = sys.argv[1]
    j = json.loads(raw)
except Exception:
    print("?"); raise SystemExit
def count(obj):
    if isinstance(obj, list): return len(obj)
    if isinstance(obj, dict):
        for k in ("rows", "items", "tarballs", "data"):
            v = obj.get(k)
            if isinstance(v, list): return len(v)
            if isinstance(v, dict):
                inner = count(v)
                if inner != "?": return inner
        if "total" in obj: return obj["total"]
        if "count" in obj: return obj["count"]
    return "?"
print(count(j))
PY
}

clear_screen() { printf '\033[H\033[2J'; }

clear_screen
echo "🛟  watch.sh — production health monitor | refresh every ${INTERVAL}s | Ctrl-C to exit"
echo "    tester: $TESTER_URL"
echo "    buyer:  $BUYER_URL"
echo "    exe:    $EXE_URL"
echo

while true; do
  TS="$(date '+%Y-%m-%d %H:%M:%S')"
  T_HOME="$(http_head "$TESTER_URL/")"
  T_DOCS="$(http_head "$TESTER_URL/docs")"
  T_DL="$(http_head   "$TESTER_URL/download")"
  B_HOME="$(http_head "$BUYER_URL/")"
  B_BROWSE="$(http_head "$BUYER_URL/browse")"
  EXE="$(http_head "$EXE_URL")"
  CNT="$(catalog_count)"
  DISK="$(df -h / 2>/dev/null | awk 'NR==2 {print $4 " free (" $5 " used)"}')"

  printf "${DIM}%s${RST}  tester:[/=%b /docs=%b /download=%b]  buyer:[/=%b /browse=%b]  catalog=${GRN}%s${RST}  exe=%b  disk=%s\n" \
    "$TS" \
    "$(color_code "$T_HOME")" \
    "$(color_code "$T_DOCS")" \
    "$(color_code "$T_DL")" \
    "$(color_code "$B_HOME")" \
    "$(color_code "$B_BROWSE")" \
    "$CNT" \
    "$(color_code "$EXE")" \
    "$DISK"

  sleep "$INTERVAL"
done
