#!/usr/bin/env bash
#
# smoke_phase1.sh — Automated Phase 1 §6 smoke test.
#
# Replaces the manual "install Paper, npm install, run-mc" flow documented in
# docs/PHASE1_RUNBOOK.md §6. This script:
#
#   1. Detects java + node + npm. If any are missing, prints a clear "skipped"
#      message and exits 0 (informational — Phase 1 smoke is environmental,
#      not a hard fail).
#   2. Downloads a pinned Paper jar to bin/.cache/paper-1.20.4.jar if absent
#      (skipped under --no-download).
#   3. Runs `npm install` inside mineflayer/ if node_modules/ is missing.
#   4. Launches Paper in the background, waits up to 60s for the "Done" log
#      line, then runs the Python coordinator with --max-steps 5 against
#      tasks/MC-tutorial-001.json.
#   5. Validates the four output files and prints PASSED on success.
#   6. Always cleans up the Paper PID via trap.
#
# Pinned Paper version: 1.20.4 build 499 (chosen because it remains compatible
# with Java 17, which is the stable openjdk on most macOS dev boxes; the
# runbook §1.4 documents 1.20.6 for newer Java 21 setups). The download URL
# is from PaperMC's stable v2 API.
#
# Modes:
#   --dry-run      Skip the actual Paper launch + run-mc invocation. Useful
#                  for CI smoke-testing this script's flow.
#   --no-download  Use whatever already exists at bin/.cache/paper-1.20.4.jar.
#                  Errors if the file is absent.
#   --help         Print usage and exit 0.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CACHE_DIR="${SCRIPT_DIR}/.cache"
PAPER_VERSION="1.20.4"
PAPER_BUILD="499"
PAPER_JAR="${CACHE_DIR}/paper-${PAPER_VERSION}.jar"
PAPER_URL="https://api.papermc.io/v2/projects/paper/versions/${PAPER_VERSION}/builds/${PAPER_BUILD}/downloads/paper-${PAPER_VERSION}-${PAPER_BUILD}.jar"
TASK_FILE="${REPO_ROOT}/tasks/MC-tutorial-001.json"
MINEFLAYER_DIR="${REPO_ROOT}/mineflayer"
PAPER_RUN_DIR="${CACHE_DIR}/paper-runtime"
PAPER_LOG="${PAPER_RUN_DIR}/paper.log"
PAPER_BOOT_TIMEOUT_SEC=60

DRY_RUN=0
NO_DOWNLOAD=0
PAPER_PID=""
TMP_OUTPUT_DIR=""

# ---- argv -------------------------------------------------------------------

usage() {
  cat <<EOF
Usage: bin/smoke_phase1.sh [--dry-run] [--no-download] [--help]

Automated Phase 1 §6 smoke test (replaces the manual flow in
docs/PHASE1_RUNBOOK.md §6).

Options:
  --dry-run      Simulate the flow without launching Paper or run-mc.
                 Exits 0 if the script's plumbing is intact.
  --no-download  Skip the Paper jar download step. Errors if the cached
                 jar at bin/.cache/paper-${PAPER_VERSION}.jar is missing.
  --help, -h     Show this message.

Exits 0 (skip) when java/node/npm are unavailable — Phase 1 smoke is
environmental, not a hard CI gate. Exits 1 only on real failure
(timeout waiting for Paper boot, run-mc bootstrap error, missing output
files). Exits 0 with "PHASE 1 SMOKE PASSED" on success.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --no-download) NO_DOWNLOAD=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "unknown flag: $1" >&2; usage >&2; exit 2 ;;
  esac
done

# ---- cleanup trap -----------------------------------------------------------

cleanup() {
  local rc=$?
  if [[ -n "${PAPER_PID}" ]] && kill -0 "${PAPER_PID}" 2>/dev/null; then
    echo "[smoke] cleaning up Paper pid=${PAPER_PID}" >&2
    kill -TERM "${PAPER_PID}" 2>/dev/null || true
    # Give Paper a couple of seconds to flush, then escalate.
    for _ in 1 2 3 4 5; do
      kill -0 "${PAPER_PID}" 2>/dev/null || break
      sleep 1
    done
    kill -KILL "${PAPER_PID}" 2>/dev/null || true
  fi
  if [[ ${rc} -ne 0 ]] && [[ -f "${PAPER_LOG}" ]]; then
    echo "[smoke] last 20 lines of Paper log:" >&2
    tail -20 "${PAPER_LOG}" >&2 || true
  fi
  return ${rc}
}
trap cleanup EXIT

# ---- step 1: tool detection -------------------------------------------------

missing=()
command -v java >/dev/null 2>&1 || missing+=("java")
command -v node >/dev/null 2>&1 || missing+=("node")
command -v npm >/dev/null 2>&1 || missing+=("npm")

if [[ ${#missing[@]} -gt 0 ]]; then
  echo "Phase 1 smoke skipped (need Java + Node + npm; missing: ${missing[*]})"
  exit 0
fi

# ---- step 2: ensure Paper jar -----------------------------------------------

mkdir -p "${CACHE_DIR}"

if [[ ! -f "${PAPER_JAR}" ]]; then
  if [[ ${NO_DOWNLOAD} -eq 1 ]]; then
    echo "[smoke] --no-download set but ${PAPER_JAR} not present" >&2
    exit 1
  fi
  if [[ ${DRY_RUN} -eq 1 ]]; then
    echo "[smoke] (dry-run) would download Paper ${PAPER_VERSION} build ${PAPER_BUILD} to ${PAPER_JAR}"
  else
    echo "[smoke] downloading Paper ${PAPER_VERSION} build ${PAPER_BUILD}..."
    if ! curl -fsSL -o "${PAPER_JAR}.part" "${PAPER_URL}"; then
      echo "[smoke] failed to download Paper from ${PAPER_URL}" >&2
      rm -f "${PAPER_JAR}.part"
      exit 1
    fi
    mv "${PAPER_JAR}.part" "${PAPER_JAR}"
  fi
else
  echo "[smoke] using cached Paper jar: ${PAPER_JAR}"
fi

# ---- step 3: npm install ----------------------------------------------------

if [[ ! -d "${MINEFLAYER_DIR}/node_modules" ]]; then
  if [[ ${DRY_RUN} -eq 1 ]]; then
    echo "[smoke] (dry-run) would run: npm install in ${MINEFLAYER_DIR}"
  else
    echo "[smoke] running npm install in ${MINEFLAYER_DIR}..."
    (cd "${MINEFLAYER_DIR}" && npm install --silent)
  fi
else
  echo "[smoke] mineflayer/node_modules/ already present, skipping npm install"
fi

# ---- dry-run shortcut -------------------------------------------------------

if [[ ${DRY_RUN} -eq 1 ]]; then
  echo "[smoke] (dry-run) would launch Paper from ${PAPER_JAR}"
  echo "[smoke] (dry-run) would run: python -m oyster_agent_runner.cli run-mc \\"
  echo "          --task-file ${TASK_FILE} \\"
  echo "          --output-dir <tmp> \\"
  echo "          --provider claude-thinking \\"
  echo "          --max-steps 5"
  echo "PHASE 1 SMOKE DRY-RUN OK"
  exit 0
fi

# ---- step 4: launch Paper ---------------------------------------------------

mkdir -p "${PAPER_RUN_DIR}"
echo "eula=true" > "${PAPER_RUN_DIR}/eula.txt"

# Minimal server.properties pinned to the runbook spec — online-mode=false,
# spawn-protection=0 so the bot can dig at spawn, level-seed=42 for repro.
cat > "${PAPER_RUN_DIR}/server.properties" <<'PROPS'
online-mode=false
gamemode=survival
difficulty=easy
spawn-protection=0
view-distance=8
simulation-distance=6
level-seed=42
motd=Oyster L4 trajectory smoke
server-port=25565
PROPS

echo "[smoke] launching Paper (logs: ${PAPER_LOG})..."
(
  cd "${PAPER_RUN_DIR}"
  java -Xms2G -Xmx2G -jar "${PAPER_JAR}" nogui
) >"${PAPER_LOG}" 2>&1 &
PAPER_PID=$!

# Poll the log for the canonical "Done" line. Paper's startup line is
# 'Done (NN.NNNs)! For help, type "help"'.
boot_ok=0
for _ in $(seq 1 ${PAPER_BOOT_TIMEOUT_SEC}); do
  if ! kill -0 "${PAPER_PID}" 2>/dev/null; then
    echo "[smoke] Paper exited during boot" >&2
    exit 1
  fi
  if grep -q 'Done (' "${PAPER_LOG}" 2>/dev/null; then
    boot_ok=1
    break
  fi
  sleep 1
done

if [[ ${boot_ok} -ne 1 ]]; then
  echo "[smoke] timeout (${PAPER_BOOT_TIMEOUT_SEC}s) waiting for Paper Done line" >&2
  exit 1
fi
echo "[smoke] Paper booted; running run-mc smoke..."

# ---- step 5: run-mc ---------------------------------------------------------

TMP_OUTPUT_DIR="$(mktemp -d -t smoke-phase1-XXXXXX)"

PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="$(command -v python3 || command -v python)"
fi

set +e
"${PYTHON_BIN}" -m oyster_agent_runner.cli run-mc \
  --task-file "${TASK_FILE}" \
  --output-dir "${TMP_OUTPUT_DIR}" \
  --provider claude-thinking \
  --max-steps 5
rc=$?
set -e

if [[ ${rc} -ne 0 ]]; then
  echo "[smoke] run-mc exited rc=${rc}" >&2
  exit 1
fi

# ---- step 6: validate output ------------------------------------------------

required=(
  "manifest.json"
  "cot.jsonl"
  "metadata.jsonl"
  "inputs.jsonl"
  "trajectory.jsonl"
)
missing_files=()
for f in "${required[@]}"; do
  if [[ ! -s "${TMP_OUTPUT_DIR}/${f}" ]]; then
    missing_files+=("${f}")
  fi
done

if [[ ${#missing_files[@]} -gt 0 ]]; then
  echo "[smoke] missing or empty output files: ${missing_files[*]}" >&2
  exit 1
fi

echo "[smoke] outputs OK in ${TMP_OUTPUT_DIR}"
echo "PHASE 1 SMOKE PASSED"
exit 0
