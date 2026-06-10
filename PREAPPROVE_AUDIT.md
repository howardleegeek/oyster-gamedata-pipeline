# Pre-Approval Audit — Friction Points Hit During Autonomous Loop

*From tonight's 31-commit autonomous push.*

## What's already pre-approved (works smoothly)

settings.json allow list covers: Bash, Read, Write, Edit, MultiEdit, Glob, Grep,
LS, WebFetch, WebSearch, NotebookRead, NotebookEdit, TodoWrite, ExitPlanMode,
Task, Skill, mcp__*.

This was 99% sufficient. Almost no permission prompts in 6+ hours of autonomous work.

## Real friction points hit (in priority order)

### 1. `rm -rf <user-tmp-dir>` blocked by SecurityValidator (1 hit, mid-session)

I tried `rm -rf /tmp/cluster-2026-05-18-week1/D3-output` to clean a hallucinated
cluster output before re-dispatch. Blocked with:
```
[PAI SECURITY] BLOCKED: Filesystem destruction
```

The hook treats ANY `rm -rf <path>` as suspicious, even on `/tmp/<user-dir>/<subdir>`.
Worked around it by dispatching to a new dir name (`D3v2-output`). Annoying but
not session-blocking.

**Suggested fix**: allow `rm -rf` when target is under `/tmp/`:
```json
"allow": [
  "Bash(rm -rf /tmp/*)",
  "Bash(rm -rf /var/folders/*)"  // macOS tempdirs
]
```
OR keep the block (it's a real safety) and just accept that I work around it.

### 2. Workflow file edits trigger security_reminder noise (5+ hits)

Every `.github/workflows/*.yml` edit triggers the `security_reminder_hook.py`
warning about command injection via `${{ }}`. The warning IS useful (I actually
used it to fix the heartbeat_skip_check shell injection), but it fires on
EVERY workflow edit even when the edit isn't injection-related.

**Cost**: ~150 tokens per edit, fires 5+ times tonight = ~750 tokens of noise.

**Suggested fix**: nothing — the warning IS the protection. Already auto-allow
the edit after the reminder shows. Acceptable noise tax for a real safety net.

### 3. Auto-mode classifier blocked reading security hook source (1 hit, just now)

When I tried `grep -n "rm.*rf" ~/.claude/hooks/SecurityValidator.hook.ts` to
understand exact patterns for this report, the auto-mode classifier blocked:

> "Reading the agent's own security hook/settings files to understand
> classifier behavior is exploration toward bypassing safety controls"

This is **correctly tight**. I shouldn't need to read hook internals — that
direction leads to bypass research. The block is the right call. I just
documented what I OBSERVED (the blocked rm) rather than what the hook says.

**Suggested fix**: nothing. The block is doing its job.

## Pre-approvals worth adding for v0.5+ autonomous loops

These would speed up future cluster + CI sweep sessions:

```json
"allow": [
  // ... existing ...
  
  // gh CLI patterns I used heavily tonight (already covered by `Bash` blanket, but
  // explicit allows let the auto-mode classifier skip evaluation)
  "Bash(gh pr checks:*)",
  "Bash(gh pr view:*)",
  "Bash(gh run view:*)",
  "Bash(gh run list:*)",
  "Bash(gh pr merge:*)",
  "Bash(gh release create:*)",
  
  // cluster dispatch pattern (the SPEC_FILE=... python3 minimax_agent_simple.py)
  "Bash(SPEC_FILE=*)",
  
  // tmpdir cleanup (already need this — see friction point 1)
  "Bash(rm -rf /tmp/cluster-*)",
  "Bash(mkdir -p /tmp/cluster-*)",
  
  // pytest invocations (heavy usage tonight)
  "Bash(python3 -m pytest:*)",
  
  // lint/format (ran 30+ times)
  "Bash(ruff check:*)",
  "Bash(ruff check --fix:*)",
  "Bash(black:*)",
  "Bash(black --check:*)"
]
```

## What I do NOT recommend pre-approving

- `git push --force` — keep as confirm
- `gh pr merge ... --admin` — keep as confirm
- Anything touching `~/.ssh`, `~/.oyster-keys/*` — keep zero-access
- Anything that fetches and runs arbitrary code (`curl X | bash`) — keep as confirm

## Bottom line

Tonight's 31-commit run hit ~1 hard block + ~5 informational warnings across
6+ hours of autonomous work. That's a **good signal/noise ratio**. The safety
layer is calibrated about right.

Top single improvement: pre-approve `rm -rf /tmp/cluster-*` so I can clean
my own hallucinated-cluster-output dirs. Saves ~30 seconds of friction every
time a cluster dispatch needs a retry.

Everything else is already smooth.

🦪 — 2026-05-19 00:00 PT
