#!/usr/bin/env bash
# =============================================================================
# install_clean.sh — clean-room SOP validator
#
# Clones oyster-agent-runner fresh, runs SOP.sh, validates all 8 steps pass.
# =============================================================================
set -euo pipefail

# --- Configuration -----------------------------------------------------------
REPO_URL="https://github.com/oyster-agent-runner/oyster-agent-runner.git"
BRANCH="feat/buyer-spec-production-pipeline"
TOTAL_SOP_STEPS=8

# --- STEP 1: Clone repository ------------------------------------------------
echo "[validator] [STEP 1/4] Cloning repository (branch: ${BRANCH})..."

TMPDIR="$(mktemp -d)"
trap 'rm -rf "${TMPDIR}"' EXIT

git clone \
  --branch "${BRANCH}" \
  --single-branch \
  --depth 1 \
  "${REPO_URL}" \
  "${TMPDIR}/repo"

echo "[validator] [STEP 1/4] Clone complete → ${TMPDIR}/repo"

# --- STEP 2: Run SOP.sh ------------------------------------------------------
echo "[validator] [STEP 2/4] Running SOP.sh inside temp dir..."

cd "${TMPDIR}/repo"
bash SOP.sh > install.log 2>&1 || true

echo "[validator] [STEP 2/4] SOP.sh finished, output captured to install.log"

# --- STEP 3: Parse SOP.log for completed steps --------------------------------
echo "[validator] [STEP 3/4] Parsing SOP.log for completed steps..."

# SOP.sh may write its own SOP.log; fall back to install.log if absent
LOG_FILE="SOP.log"
if [ ! -f "${LOG_FILE}" ]; then
  LOG_FILE="install.log"
fi

COMPLETED=0
for i in $(seq 1 ${TOTAL_SOP_STEPS}); do
  if grep -q "\[STEP ${i}/${TOTAL_SOP_STEPS}\].*\[DONE\]" "${LOG_FILE}" 2>/dev/null; then
    COMPLETED=$((COMPLETED + 1))
  fi
done

echo "[validator] [STEP 3/4] Found ${COMPLETED}/${TOTAL_SOP_STEPS} SOP steps marked [DONE]"

# --- STEP 4: Report PASS / FAIL ----------------------------------------------
echo "[validator] [STEP 4/4] Reporting result..."

if [ "${COMPLETED}" -eq ${TOTAL_SOP_STEPS} ]; then
  echo "[install-validator] PASS — all ${TOTAL_SOP_STEPS} SOP steps completed"
else
  echo "[install-validator] FAIL — only ${COMPLETED}/${TOTAL_SOP_STEPS} steps completed"
  echo "--- last 20 lines of install.log ---"
  tail -n 20 install.log
  exit 1
fi
