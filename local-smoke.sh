#!/bin/sh
# local-smoke.sh — Quick health probe for the local dev environment.
#
# Checks:
#   1. http://localhost:3000/          (tester portal)
#   2. http://localhost:3001/          (buyer portal)
#   3. http://localhost:3001/api/catalog?limit=10  (buyer API, JSON)
#   4. http://127.0.0.1:54321/        (Supabase REST)
#
# Exit 0 if all green, 1 if any probe fails.

set -e

RED='\033[0;31m'; GRN='\033[0;32m'; YEL='\033[0;33m'; RST='\033[0m'

PASS=0
FAIL=0

# probe URL [label] — print colour-coded result, increment counters.
probe() {
  url="$1"
  label="${2:-$1}"
  code=$(curl -sL -o /dev/null -m 5 -w "%{http_code}" "$url" 2>/dev/null) || true
  code="${code:-000}"

  case "$code" in
    2[0-9][0-9]|3[0-9][0-9])
      printf "${GRN}[%s]${RST} %s\n" "$code" "$label"
      PASS=$((PASS + 1))
      ;;
    *)
      printf "${RED}[%s]${RST} %s\n" "$code" "$label"
      FAIL=$((FAIL + 1))
      ;;
  esac
}

# probe_json URL [label] — same as probe but also validates JSON body.
probe_json() {
  url="$1"
  label="${2:-$1}"
  body=$(curl -sL -m 5 "$url" 2>/dev/null) || true
  code=$(curl -sL -o /dev/null -m 5 -w "%{http_code}" "$url" 2>/dev/null) || true
  code="${code:-000}"

  if [ "$code" != "200" ]; then
    printf "${RED}[%s]${RST} %s\n" "$code" "$label"
    FAIL=$((FAIL + 1))
    return
  fi

  # Validate JSON — use python3 if available, else node.
  if command -v python3 >/dev/null 2>&1; then
    valid=$(printf '%s' "$body" | python3 -c "import json,sys; json.load(sys.stdin); print('ok')" 2>/dev/null || echo "bad")
  elif command -v node >/dev/null 2>&1; then
    valid=$(printf '%s' "$body" | node -e "let d='';process.stdin.on('data',c=>d+=c);process.stdin.on('end',()=>{try{JSON.parse(d);console.log('ok')}catch(e){console.log('bad')}})" 2>/dev/null || echo "bad")
  else
    # no validator — trust HTTP 200
    valid="ok"
  fi

  if [ "$valid" = "ok" ]; then
    printf "${GRN}[%s]${RST} %s  (valid JSON)\n" "$code" "$label"
    PASS=$((PASS + 1))
  else
    printf "${YEL}[%s]${RST} %s  (HTTP ok but invalid JSON)\n" "$code" "$label"
    FAIL=$((FAIL + 1))
  fi
}

printf "=== Local smoke probes ===\n\n"

probe      "http://localhost:3000/"                        "tester portal  :3000"
probe      "http://localhost:3001/"                        "buyer portal   :3001"
probe_json "http://localhost:3001/api/catalog?limit=10"    "buyer /api/catalog"
probe      "http://127.0.0.1:54321/"                       "supabase REST  :54321"

printf "\n--- Results: ${GRN}%d passed${RST}, ${RED}%d failed${RST} ---\n" "$PASS" "$FAIL"

if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
exit 0
