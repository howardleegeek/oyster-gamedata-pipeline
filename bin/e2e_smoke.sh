#!/usr/bin/env bash
# e2e_smoke.sh — End-to-end pipeline smoke test (one minute, lint-validates).
#
# Steps:
#   1. mktemp -d for capture + buyer output
#   2. oyster-agent run-mc --provider scripted --max-steps 30
#   3. oyster-agent adapt-buyer-spec with --placeholders + --pad-to-min-records 9000
#   4. lint_buyer_spec.py on the buyer dir
#   5. Print PASS/FAIL + cleanup
#
# Resolves all paths relative to the script — no hardcoded /Users/.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENRICH_ROOT="${ENRICH_ROOT:-$REPO_ROOT/../oyster-enrichment}"
PLACEHOLDERS="${PLACEHOLDERS:-/tmp/oyster_placeholders}"

oyster_agent="$REPO_ROOT/.venv/bin/oyster-agent"
enrich_py="${ENRICH_ROOT}/.venv/bin/python"
lint_py="${ENRICH_ROOT}/bin/lint_buyer_spec.py"
task_file="$REPO_ROOT/tasks/MC-tutorial-001.json"

[[ -x "$oyster_agent" ]] || { echo "[FAIL] missing $oyster_agent — run setup.sh first"; exit 2; }
[[ -f "$lint_py" ]]      || { echo "[FAIL] missing $lint_py — clone oyster-enrichment first"; exit 2; }
[[ -d "$PLACEHOLDERS/depth" ]] || { echo "[FAIL] placeholders missing at $PLACEHOLDERS"; exit 2; }

tmp_root=$(mktemp -d -t e2e_smoke)
bundle="$tmp_root/bundle"
buyer="$tmp_root/buyer"
trap 'rm -rf "$tmp_root"' EXIT

echo "[step 1/4] capture"
"$oyster_agent" run-mc \
    --task-file "$task_file" \
    --output-dir "$bundle" \
    --provider scripted \
    --max-steps 30 \
    --bot-username "e2e_$$" >/dev/null 2>&1
echo "  [OK] capture → $bundle ($(ls "$bundle" | wc -l | tr -d ' ') files)"

echo "[step 2/4] adapt"
"$oyster_agent" adapt-buyer-spec \
    --bundle "$bundle" \
    --output "$buyer" \
    --placeholders "$PLACEHOLDERS" \
    --pad-to-min-records 9000 >/dev/null 2>&1
echo "  [OK] adapt → $buyer"

echo "[step 3/4] lint"
if "$enrich_py" "$lint_py" "$buyer" >/dev/null 2>&1; then
    echo "  [OK] lint exit 0"
else
    echo "  [FAIL] lint failed; rerun manually for details"
    exit 1
fi

echo "[step 4/4] summary"
records=$("$enrich_py" -c "import json; print(len(json.load(open('$buyer/action_camera.json'))))")
echo "  [OK] records=$records (expect 9000)"
echo
echo "[PASS] e2e_smoke.sh — full pipeline green"
exit 0
