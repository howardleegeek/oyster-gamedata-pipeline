#!/usr/bin/env bash
# dispatch_qwen_to_minipc.sh
#
# Reverse-call cluster dispatch:
#   mac-1 (this script) holds Qwen API key, calls API, gets code.
#   Generated code shipped to minipc via SSH+rsync (NO key transfer).
#   minipc runs python tests; logs streamed back.
#
# Usage:
#   bin/cluster/dispatch_qwen_to_minipc.sh <task_id> <spec.md path>
#
# Howard 2026-05-06: cluster discipline — mac-1 = git+API; minipc = run.

set -euo pipefail

TASK_ID="${1:?task_id required}"
SPEC_PATH="${2:?spec.md path required}"
MINIPC_HOST="${MINIPC_HOST:-minipc-bwdxs}"
QWEN_MODEL="${QWEN_MODEL:-qwen-coder-plus-latest}"
KEY_FILE="${HOME}/.oyster-keys/aliyun-token-plan.env"

[ -f "$KEY_FILE" ] || { echo "ERROR: $KEY_FILE missing"; exit 1; }
[ -f "$SPEC_PATH" ] || { echo "ERROR: spec $SPEC_PATH missing"; exit 1; }

# Source key + base URL into our shell only — never SSH-export.
# shellcheck disable=SC1090
source "$KEY_FILE"

WORK_DIR="/tmp/qwen_dispatch_${TASK_ID}_$$"
mkdir -p "$WORK_DIR"
cp "$SPEC_PATH" "$WORK_DIR/spec.md"

echo "[1/5] mac-1 → Qwen API call ($QWEN_MODEL)..."
# Build request: "implement spec.md and return the python source files as
# fenced code blocks tagged with their target file paths".
SYSTEM_PROMPT="You are a senior Python engineer. Implement the spec given. Output ONLY fenced code blocks, each prefixed with the target file path on the line above the opening fence, like this: PATH: bin/foo.py\n\`\`\`python\n...\n\`\`\` Output every file the spec requires. No prose, no explanation."

USER_PROMPT="$(cat "$SPEC_PATH")"

REQUEST=$(python3 -c "
import json, sys
print(json.dumps({
    'model': '$QWEN_MODEL',
    'messages': [
        {'role': 'system', 'content': '''$SYSTEM_PROMPT'''},
        {'role': 'user',   'content': sys.stdin.read()},
    ],
    'max_tokens': 8192,
    'temperature': 0.2,
}))
" <"$SPEC_PATH")

curl -sS -X POST "$ALIYUN_TOKEN_PLAN_BASE_URL/chat/completions" \
    -H "Authorization: Bearer $ALIYUN_TOKEN_PLAN_API_KEY" \
    -H "Content-Type: application/json" \
    -d "$REQUEST" >"$WORK_DIR/response.json"

CONTENT=$(python3 -c "
import json
d = json.load(open('$WORK_DIR/response.json'))
if 'choices' in d:
    print(d['choices'][0]['message']['content'])
else:
    print('QWEN_ERROR', d, end='')
")

if [[ "$CONTENT" == QWEN_ERROR* ]]; then
    echo "[ERROR] Qwen API failed: $CONTENT" >&2
    exit 2
fi

echo "$CONTENT" >"$WORK_DIR/qwen_output.txt"

echo "[2/5] Parse fenced blocks → individual files..."
python3 - "$WORK_DIR" <<'PY_EOF'
import re, sys, os, pathlib
work = pathlib.Path(sys.argv[1])
content = (work / "qwen_output.txt").read_text()
# Match: PATH: <path>\n```<lang>\n<code>\n```
pat = re.compile(r"PATH:\s*([^\n]+)\n```(?:\w+)?\n(.*?)\n```", re.DOTALL)
files = pat.findall(content)
out_dir = work / "out"
out_dir.mkdir(exist_ok=True)
for path, code in files:
    target = out_dir / path.strip()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(code)
    print(f"  wrote {target}")
print(f"PARSED {len(files)} files")
PY_EOF

echo "[3/5] rsync → minipc:/tmp/qwen_${TASK_ID}/"
ssh "$MINIPC_HOST" "wsl bash -c 'mkdir -p /tmp/qwen_${TASK_ID}'"
rsync -a -e ssh "$WORK_DIR/out/" "$MINIPC_HOST:/tmp/qwen_${TASK_ID}.staging/" 2>&1 | tail -3 || true
# Fallback: scp via WSL path
scp -r "$WORK_DIR/out/." "$MINIPC_HOST:/tmp/qwen_${TASK_ID}.staging/" 2>&1 | tail -3 || true
ssh "$MINIPC_HOST" "wsl bash -c 'cp -r /mnt/c/tmp/qwen_${TASK_ID}.staging/. /tmp/qwen_${TASK_ID}/ 2>/dev/null || true'" || true

echo "[4/5] minipc: py_compile + pytest"
ssh "$MINIPC_HOST" "wsl bash -c 'cd /tmp/qwen_${TASK_ID} && find . -name \"*.py\" -exec python3 -m py_compile {} \\; 2>&1 | head -20 && python3 -m pytest -q . 2>&1 | tail -10'" 2>&1 | tail -25

echo "[5/5] Pull back results"
mkdir -p "$WORK_DIR/results"
scp -r "$MINIPC_HOST:/tmp/qwen_${TASK_ID}/." "$WORK_DIR/results/" 2>&1 | tail -3 || true

echo ""
echo "=== DISPATCH DONE ==="
echo "Workdir:  $WORK_DIR"
echo "Generated files in: $WORK_DIR/out/"
echo "minipc results:    $WORK_DIR/results/"
echo "Review + git mv into oyster-agent-runner/ if good."
