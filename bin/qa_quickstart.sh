#!/usr/bin/env bash
# qa_quickstart.sh — one-command QA flow for the testing team.
#
# What it does:
#   1. Creates a local .venv if one doesn't already exist (no system pip pollution)
#   2. Installs all required deps (OpenEXR, numpy, openpyxl, pyyaml, etc.)
#   3. Builds a sample tarball using the production synthesizers
#   4. Lints the sample with the PRD-grounded v3 lint
#   5. Prints a single GREEN/RED verdict
#
# Usage:
#   bash bin/qa_quickstart.sh                 # full pipeline
#   bash bin/qa_quickstart.sh --quick         # skip video/depth (fast smoke)
#   bash bin/qa_quickstart.sh --keep          # keep the work dir afterwards
#
# Requires: Python 3.10+, ffmpeg (only for full mode). Nothing else.

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
WORK="${REPO}/qa_workdir"
QUICK=0
KEEP=0
for arg in "$@"; do
  case "$arg" in
    --quick) QUICK=1 ;;
    --keep)  KEEP=1 ;;
    -h|--help)
      grep '^#' "$0" | head -20 | sed 's/^# \{0,1\}//'
      exit 0 ;;
  esac
done

red() { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
section() { printf '\n\033[1;36m═══ %s ═══\033[0m\n' "$*"; }

# ─── Step 1: venv ─────────────────────────────────────────────────────────
section "1/5 venv setup"
cd "$REPO"
if [ ! -d .venv ]; then
  echo "Creating .venv (one-time setup, ~10s)…"
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
PY="$(command -v python3)"
echo "  python: $PY"
echo "  version: $($PY --version)"

# ─── Step 2: install ──────────────────────────────────────────────────────
section "2/5 dependencies"
$PY -m pip install --quiet --upgrade pip
# Editable install with the lint extras pulls in OpenEXR, numpy, openpyxl
$PY -m pip install --quiet -e ".[exr,xlsx,test]" 2>&1 | tail -3 || {
  red "  pip install failed — see above"
  exit 1
}
green "  ✓ deps installed"

# ─── Step 3: build sample ─────────────────────────────────────────────────
section "3/5 build sample tarball"
rm -rf "$WORK"
mkdir -p "$WORK"
SAMPLE="$WORK/sample.tar.gz"

if [ "$QUICK" -eq 1 ]; then
  yellow "  --quick mode: skipping video + depth (fast schema-only check)"
  $PY "$REPO/dist/oyster-sample-tarball.pyz" --output "$SAMPLE" --skip-video --skip-depth 2>&1 | tail -3
else
  if ! command -v ffmpeg >/dev/null 2>&1; then
    yellow "  ffmpeg not found — falling back to --quick"
    $PY "$REPO/dist/oyster-sample-tarball.pyz" --output "$SAMPLE" --skip-video --skip-depth 2>&1 | tail -3
  else
    $PY "$REPO/dist/oyster-sample-tarball.pyz" --output "$SAMPLE" 2>&1 | tail -3
  fi
fi
[ -f "$SAMPLE" ] || { red "  sample tarball not produced"; exit 1; }
SIZE=$(du -h "$SAMPLE" | cut -f1)
green "  ✓ sample.tar.gz built ($SIZE)"

# ─── Step 4: extract for linting ──────────────────────────────────────────
section "4/5 extract"
EXTRACT="$WORK/extracted"
mkdir -p "$EXTRACT"
tar -xzf "$SAMPLE" -C "$EXTRACT"
echo "  layout:"
ls -la "$EXTRACT" | grep -v '^total\|^d.*\.$' | awk '{print "    " $NF " (" $5 " bytes)"}' | head -10

# ─── Step 5: lint ─────────────────────────────────────────────────────────
section "5/5 lint v3 PRD-grounded"
LINT_REPORT="$WORK/lint_report.json"
$PY "$REPO/bin/lint_v3_prd_grounded.py" "$EXTRACT" --output "$LINT_REPORT" 2>&1 | tail -5 || true

# Parse pass/fail count
if [ -f "$LINT_REPORT" ]; then
  PASS=$($PY -c "import json; r=json.load(open('$LINT_REPORT')); print(sum(1 for c in r.get('checks',r.get('results',[])) if c.get('passed')))")
  TOTAL=$($PY -c "import json; r=json.load(open('$LINT_REPORT')); print(len(r.get('checks',r.get('results',[]))))")
  echo ""
  if [ "$PASS" -eq "$TOTAL" ]; then
    green "✓ ALL $TOTAL CHECKS PASSED — buyer-spec compliant"
    EXIT=0
  else
    yellow "$PASS / $TOTAL checks passed — see $LINT_REPORT for details"
    EXIT=1
  fi
else
  red "  lint did not produce a report — manual review needed"
  EXIT=2
fi

if [ "$KEEP" -eq 0 ]; then
  rm -rf "$WORK"
  echo "(work dir cleaned; use --keep to retain it)"
else
  yellow "work dir retained at: $WORK"
fi

exit "$EXIT"
