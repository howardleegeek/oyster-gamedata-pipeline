#!/usr/bin/env bash
# dispatch_qwen_to_minipc.sh
#
# Reverse-call cluster dispatch:
#   mac-1 (this script) holds Qwen API key, calls API, gets code.
#   Generated code shipped to minipc via tar-pipe-over-ssh-into-WSL.
#   minipc runs python tests; logs streamed back.
#
# Usage:
#   bin/cluster/dispatch_qwen_to_minipc.sh <task_id> <spec.md path>
#
# Env overrides:
#   QWEN_MODEL    (default qwen3.6-plus; alts: deepseek-v3.2 MiniMax-M2.5 glm-5)
#   MINIPC_HOST   (default minipc-bwdxs)
#
# Howard 2026-05-06 — cluster discipline: mac-1 = git+API; minipc = run.
# v2: tar-pipe transport (no Windows-side staging, no nested quoting).

set -euo pipefail

TASK_ID="${1:?task_id required}"
SPEC_PATH="${2:?spec.md path required}"
MINIPC_HOST="${MINIPC_HOST:-minipc-bwdxs}"
QWEN_MODEL="${QWEN_MODEL:-deepseek-v3.2}"  # Aliyun token-plan: deepseek-v3.2 (fast codegen, default) | qwen3.6-plus (reasoning, slower) | MiniMax-M2.5 | glm-5
KEY_FILE="${HOME}/.oyster-keys/aliyun-token-plan.env"

[ -f "$KEY_FILE" ] || { echo "ERROR: $KEY_FILE missing"; exit 1; }
[ -f "$SPEC_PATH" ] || { echo "ERROR: spec $SPEC_PATH missing"; exit 1; }

# shellcheck disable=SC1090
source "$KEY_FILE"

WORK_DIR="/tmp/qwen_dispatch_${TASK_ID}_$$"
mkdir -p "$WORK_DIR"
cp "$SPEC_PATH" "$WORK_DIR/spec.md"

echo "[1/5] mac-1 → Qwen API call ($QWEN_MODEL)..."

SYSTEM_PROMPT='You are a senior Python engineer. Implement the spec given by the user.

OUTPUT RULES (strict — parser is regex-based):
1. Output ONLY fenced code blocks. No prose, no explanation, no commentary outside fences.
2. EVERY file in the spec'"'"'s "Validation" / "Tests" sections MUST be emitted, including test files.
3. Each code block MUST be preceded by a line of form "PATH: <relative/path>" with no leading whitespace.
4. Code blocks open with ```python and close with ``` on its own line.
5. NEVER emit empty code blocks. If a file should be empty (e.g. __init__.py), put a single comment like "# package marker".
6. Path lines do NOT appear inside code bodies.

Example format:
PATH: bin/foo/bar.py
```python
import math
def f(): return 0
```

PATH: tests/test_bar.py
```python
def test_f(): assert True
```

PATH: bin/foo/__init__.py
```python
# package marker
```'

REQUEST=$(SYSTEM_PROMPT="$SYSTEM_PROMPT" QWEN_MODEL="$QWEN_MODEL" python3 -c "
import json, sys, os
print(json.dumps({
    'model': os.environ['QWEN_MODEL'],
    'messages': [
        {'role': 'system', 'content': os.environ['SYSTEM_PROMPT']},
        {'role': 'user',   'content': sys.stdin.read()},
    ],
    'max_tokens': 8192,
    'temperature': 0.2,
}))
" <"$SPEC_PATH")

curl -sS -X POST "$ALIYUN_TOKEN_PLAN_BASE_URL/chat/completions" \
    -H "Authorization: Bearer $ALIYUN_TOKEN_PLAN_API_KEY" \
    -H "Content-Type: application/json" \
    --max-time 300 --retry 2 --retry-delay 5 --retry-all-errors \
    -d "$REQUEST" >"$WORK_DIR/response.json"

CONTENT=$(WORK_DIR="$WORK_DIR" python3 -c "
import json, os
d = json.load(open(os.environ['WORK_DIR'] + '/response.json'))
if 'choices' in d:
    print(d['choices'][0]['message']['content'])
else:
    print('QWEN_ERROR', d, end='')
")

if [[ "$CONTENT" == QWEN_ERROR* ]]; then
    echo "[ERROR] Qwen API failed: $CONTENT" >&2
    exit 2
fi

printf '%s\n' "$CONTENT" >"$WORK_DIR/qwen_output.txt"

echo "[2/5] Parse fenced blocks → individual files..."
WORK_DIR="$WORK_DIR" python3 - <<'PY_EOF'
import re, os, sys, pathlib
work = pathlib.Path(os.environ["WORK_DIR"])
content = (work / "qwen_output.txt").read_text()

# Robust parser: split on PATH: at line start to bound each block.
# This avoids the "lazy regex spans across PATH boundaries" bug
# when an LLM emits an empty fenced body.
blocks = re.split(r"^PATH:\s*", content, flags=re.MULTILINE)
files = []
for block in blocks[1:]:  # blocks[0] is preamble (usually empty)
    lines = block.split("\n", 1)
    path = lines[0].strip()
    rest = lines[1] if len(lines) > 1 else ""
    # First fenced block in `rest` is the file body. Tolerate empty body.
    m = re.search(r"```(?:\w+)?\n?(.*?)\n?```", rest, re.DOTALL)
    if m and path:
        body = m.group(1)
        files.append((path, body))
    else:
        print(f"  SKIP unparseable block for path={path!r}", file=sys.stderr)

out_dir = work / "out"
out_dir.mkdir(exist_ok=True)
for path, code in files:
    rel = pathlib.PurePosixPath(path.strip())
    # Reject path traversal and absolute paths (security: Qwen could be jailbroken)
    if rel.is_absolute() or any(part == ".." for part in rel.parts):
        print(f"  REJECT suspicious path: {path}", file=sys.stderr)
        continue
    target = out_dir / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    # Empty bodies become a one-line stub so we don't ship a 0-byte file
    target.write_text(code if code.strip() else "# package marker\n")
    print(f"  wrote {target}  ({len(code)} bytes)")
print(f"PARSED {len(files)} files")
if not files:
    print("WARNING: no fenced PATH:-tagged blocks found", file=sys.stderr)
PY_EOF

NUM_FILES=$(find "$WORK_DIR/out" -type f 2>/dev/null | wc -l | tr -d ' ')
if [[ "$NUM_FILES" -eq 0 ]]; then
    echo "[ERROR] Qwen produced no parseable files. Raw output: $WORK_DIR/qwen_output.txt" >&2
    exit 3
fi

REMOTE_DIR="/tmp/qwen_${TASK_ID}"

echo "[3/5] tar-pipe → minipc:${REMOTE_DIR} (${NUM_FILES} files)"
ssh "$MINIPC_HOST" wsl rm -rf "$REMOTE_DIR" 2>&1 | head -3 || true
ssh "$MINIPC_HOST" wsl mkdir -p "$REMOTE_DIR"
# COPYFILE_DISABLE=1 suppresses ._AppleDouble files (BSD tar quirk on macOS)
# --format=ustar avoids PAX extended headers that carry com.apple.* xattrs
COPYFILE_DISABLE=1 tar --format=ustar -czf - -C "$WORK_DIR/out" . \
    | ssh "$MINIPC_HOST" wsl tar -xzf - -C "$REMOTE_DIR"
echo "  payload delivered"

echo "[4/5] minipc: py_compile + pytest"
ssh "$MINIPC_HOST" wsl bash <<EOF | tee "$WORK_DIR/test_output.txt"
set +e
cd ${REMOTE_DIR}
echo "=== files ==="
find . -type f -name "*.py" | head -20
echo ""
echo "=== py_compile ==="
find . -name "*.py" -exec python3 -m py_compile {} \;
PY_RC=\$?
echo "py_compile rc=\$PY_RC"
echo ""
echo "=== pytest ==="
PYTHONPATH=. python3 -m pytest -q . 2>&1 | tail -25
PYTEST_RC=\${PIPESTATUS[0]}
echo "pytest rc=\$PYTEST_RC"
echo ""
echo "EXIT_SUMMARY py_compile=\$PY_RC pytest=\$PYTEST_RC"
EOF

echo "[5/5] Pull back results (tarball over ssh stdout)"
mkdir -p "$WORK_DIR/results"
ssh "$MINIPC_HOST" wsl tar -czf - -C "$REMOTE_DIR" . 2>/dev/null | tar -xzf - -C "$WORK_DIR/results" || echo "  (results pull failed, non-fatal)"

# Parse final RC from test_output.txt
SUMMARY_LINE=$(grep -E '^EXIT_SUMMARY' "$WORK_DIR/test_output.txt" 2>/dev/null | tail -1 || echo "")

echo ""
echo "=== DISPATCH DONE ==="
echo "Workdir:           $WORK_DIR"
echo "Generated files:   $WORK_DIR/out/"
echo "minipc results:    $WORK_DIR/results/"
echo "Summary:           ${SUMMARY_LINE:-(no summary line — see test_output.txt)}"
echo ""
echo "Review + git mv into oyster-agent-runner/ if good:"
echo "  cp -r $WORK_DIR/out/* ."
