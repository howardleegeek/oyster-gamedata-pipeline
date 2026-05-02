#!/bin/bash
set -euo pipefail

echo "[e2e_smoke.sh] Starting buyer-spec pipeline smoke test"

# Step 1: Create temporary directory
echo "[step 1] Creating temporary directory"
TEMP_DIR=$(mktemp -d)
echo "Created temp dir: $TEMP_DIR"
echo "[OK]"

# Step 2: Run oyster-agent
echo "[step 2] Running oyster-agent"
OAGENT_PATH="/Users/howardlee/Downloads/oyster-agent-runner/.venv/bin/oyster-agent"
MC_TASK_FILE="/Users/howardlee/Downloads/oyster-agent-runner/tasks/MC-tutorial-001.json"
OUTPUT_BUNDLE="$TEMP_DIR/bundle"
"$OAGENT_PATH" run-mc --task-file "$MC_TASK_FILE" --output-dir "$OUTPUT_BUNDLE" --provider scripted --max-steps 30 --bot-username e2e_smoke
if [ $? -eq 0 ]; then
    echo "[OK]"
else
    echo "[FAIL] oyster-agent failed"
    echo "Cleaning up temp dir: $TEMP_DIR"
    rm -rf "$TEMP_DIR"
    exit 1
fi

# Step 3: Run buyer_spec.adapter.adapt_phase1_to_buyer_spec
echo "[step 3] Running buyer_spec.adapter.adapt_phase1_to_buyer_spec"
ENRICHMENT_PATH="/Users/howardlee/Downloads/oyster-enrichment"
BUYER_SPEC_PATH="/Users/howardlee/Downloads/oyster-buyer-spec-pipeline"
PYTHONPATH="$ENRICHMENT_PATH:$BUYER_SPEC_PATH:$PYTHONPATH" \
python3 -c "
import sys
sys.path.insert(0, '$ENRICHMENT_PATH')
sys.path.insert(0, '$BUYER_SPEC_PATH')
from buyer_spec.adapter import adapt_phase1_to_buyer_spec
import json
import os

# Load the bundle
bundle_path = '$OUTPUT_BUNDLE'
if not os.path.exists(bundle_path):
    print('Bundle not found:', bundle_path)
    sys.exit(1)

# Find the phase1 output file
phase1_file = None
for root, dirs, files in os.walk(bundle_path):
    for file in files:
        if file.endswith('.json') and 'phase1' in file.lower():
            phase1_file = os.path.join(root, file)
            break
    if phase1_file:
        break

if not phase1_file:
    print('No phase1 JSON file found in bundle')
    sys.exit(1)

print(f'Found phase1 file: {phase1_file}')

# Load and adapt
with open(phase1_file, 'r') as f:
    phase1_data = json.load(f)

adapted = adapt_phase1_to_buyer_spec(phase1_data)

# Save adapted output
adapted_file = '$TEMP_DIR/adapted_buyer_spec.json'
with open(adapted_file, 'w') as f:
    json.dump(adapted, f, indent=2)

print(f'Saved adapted buyer spec to: {adapted_file}')
"
if [ $? -eq 0 ]; then
    echo "[OK]"
else
    echo "[FAIL] adapt_phase1_to_buyer_spec failed"
    echo "Cleaning up temp dir: $TEMP_DIR"
    rm -rf "$TEMP_DIR"
    exit 1
fi

# Step 4: Run lint_buyer_spec.py
echo "[step 4] Running lint_buyer_spec.py"
ADAPTED_FILE="$TEMP_DIR/adapted_buyer_spec.json"
LINT_SCRIPT="$BUYER_SPEC_PATH/lint_buyer_spec.py"
if [ ! -f "$LINT_SCRIPT" ]; then
    echo "Error: lint_buyer_spec.py not found at $LINT_SCRIPT"
    echo "Cleaning up temp dir: $TEMP_DIR"
    rm -rf "$TEMP_DIR"
    exit 1
fi

python3 "$LINT_SCRIPT" "$ADAPTED_FILE"
LINT_RESULT=$?
if [ $LINT_RESULT -eq 0 ]; then
    echo "[OK]"
    echo "PASS"
else
    echo "[FAIL]"
    echo "FAIL"
fi

# Step 5: Cleanup
echo "[step 5] Cleaning up"
echo "Removing temp dir: $TEMP_DIR"
rm -rf "$TEMP_DIR"
echo "[OK]"

# Exit with lint result
exit $LINT_RESULT