#!/usr/bin/env bash
# Brute-force iteration runner: capture → adapt → lint, repeat N times.
#
# Usage: iterate_buyer_spec.sh <iter_count> [<max_steps_per_run>]
#
# Each iteration logs to /tmp/oyster_iter_log/{iter_NNNN}.json with:
#   - capture_seconds
#   - records_emitted
#   - lint_exit
#   - lint_errors
#   - lint_warnings
#
# Aggregate metrics flushed to /tmp/oyster_iter_log/summary.json after the run.

set -uo pipefail

ITER_COUNT="${1:-100}"
MAX_STEPS="${2:-50}"

REPO="/Users/howardli/Downloads/oyster-agent-runner"
ENRICH_VENV="/Users/howardli/Downloads/oyster-enrichment/.venv/bin/python"
TASK_FILE="$REPO/tasks/MC-tutorial-001.json"
PLACEHOLDERS="/tmp/oyster_placeholders"
LOG_DIR="/tmp/oyster_iter_log"
mkdir -p "$LOG_DIR"

echo "[iterate] starting $ITER_COUNT iterations, max_steps=$MAX_STEPS"

PASS_COUNT=0
FAIL_COUNT=0

for i in $(seq 1 "$ITER_COUNT"); do
    iter_id=$(printf "iter_%04d" "$i")
    bundle_dir="/tmp/oyster_iter_runs/$iter_id"
    buyer_dir="/tmp/oyster_iter_runs/${iter_id}_buyer"
    log_file="$LOG_DIR/$iter_id.json"

    echo "[$iter_id] capture → adapt → lint"
    t0=$(date +%s)

    # 1. CAPTURE — uniquify bot username so each run gets its own offline-mode session
    "$REPO/.venv/bin/oyster-agent" run-mc \
        --task-file "$TASK_FILE" \
        --output-dir "$bundle_dir" \
        --provider mock \
        --max-steps "$MAX_STEPS" \
        --bot-username "oyster_loop_${i}" > "$LOG_DIR/${iter_id}_capture.log" 2>&1
    capture_exit=$?
    t_capture=$(date +%s)

    # 2. ADAPT (pad to 9000 records so 5-min lint floor is satisfied even
    # when capture step rate is below 30/sec)
    "$ENRICH_VENV" -c "
import sys
sys.path.insert(0, '$REPO/src')
from pathlib import Path
from oyster_agent_runner.buyer_spec_adapter import adapt_phase1_to_buyer_spec
adapt_phase1_to_buyer_spec(
    Path('$bundle_dir'),
    Path('$buyer_dir'),
    placeholders_dir=Path('$PLACEHOLDERS'),
    pad_to_min_records=9000,
)
" > "$LOG_DIR/${iter_id}_adapt.log" 2>&1
    adapt_exit=$?
    t_adapt=$(date +%s)

    # 3. LINT
    "$ENRICH_VENV" /Users/howardli/Downloads/oyster-enrichment/bin/lint_buyer_spec.py "$buyer_dir" > "$LOG_DIR/${iter_id}_lint.log" 2>&1
    lint_exit=$?
    t_lint=$(date +%s)

    # 4. Extract metrics
    records=$(/Users/howardli/Downloads/oyster-enrichment/.venv/bin/python -c "
import json
try:
    print(len(json.load(open('$buyer_dir/action_camera.json'))))
except Exception:
    print(0)
" 2>/dev/null)
    # grep -c exits 1 when no matches; pipe-to-cat to neutralize that and
    # always read a single integer line. Empty-file fallback to 0.
    errors=$(grep -c "error  " "$LOG_DIR/${iter_id}_lint.log" 2>/dev/null | head -1)
    errors="${errors:-0}"
    warnings=$(grep -c "warning  " "$LOG_DIR/${iter_id}_lint.log" 2>/dev/null | head -1)
    warnings="${warnings:-0}"

    /Users/howardli/Downloads/oyster-enrichment/.venv/bin/python -c "
import json
json.dump({
    'iter': $i,
    'capture_exit': $capture_exit,
    'adapt_exit': $adapt_exit,
    'lint_exit': $lint_exit,
    'capture_seconds': $((t_capture - t0)),
    'adapt_seconds': $((t_adapt - t_capture)),
    'lint_seconds': $((t_lint - t_adapt)),
    'total_seconds': $((t_lint - t0)),
    'records': $records,
    'lint_errors': $errors,
    'lint_warnings': $warnings,
}, open('$log_file', 'w'), indent=2)
"

    if [[ $lint_exit -eq 0 && $errors -eq 0 ]]; then
        PASS_COUNT=$((PASS_COUNT + 1))
        echo "[$iter_id] ✅ PASS  records=$records  total=$((t_lint - t0))s"
    else
        FAIL_COUNT=$((FAIL_COUNT + 1))
        echo "[$iter_id] ❌ FAIL  records=$records  errors=$errors  total=$((t_lint - t0))s"
    fi

    # 5. Cleanup: drop the intermediate trajectory bundle and the buyer
    # dir to save disk during long sprints. Sample every 10th buyer dir
    # is preserved so we have evidence trail.
    rm -rf "$bundle_dir" 2>/dev/null
    if [[ $((i % 10)) -ne 0 ]]; then
        rm -rf "$buyer_dir" 2>/dev/null
    fi
done

echo
echo "[iterate] DONE: $PASS_COUNT passed, $FAIL_COUNT failed of $ITER_COUNT"

# Aggregate
"$ENRICH_VENV" -c "
import json
from pathlib import Path
log_dir = Path('$LOG_DIR')
runs = []
for p in sorted(log_dir.glob('iter_*.json')):
    try:
        runs.append(json.load(p.open()))
    except Exception:
        pass
summary = {
    'iter_count': len(runs),
    'pass_count': sum(1 for r in runs if r['lint_exit'] == 0 and r['lint_errors'] == 0),
    'fail_count': sum(1 for r in runs if not (r['lint_exit'] == 0 and r['lint_errors'] == 0)),
    'avg_capture_seconds': round(sum(r['capture_seconds'] for r in runs) / max(1, len(runs)), 2),
    'avg_total_seconds': round(sum(r['total_seconds'] for r in runs) / max(1, len(runs)), 2),
    'avg_records': round(sum(r['records'] for r in runs) / max(1, len(runs)), 1),
    'min_records': min((r['records'] for r in runs), default=0),
    'max_records': max((r['records'] for r in runs), default=0),
    'total_lint_errors': sum(r['lint_errors'] for r in runs),
}
import json as J
print(J.dumps(summary, indent=2))
J.dump(summary, open(log_dir / 'summary.json', 'w'), indent=2)
"

exit 0
