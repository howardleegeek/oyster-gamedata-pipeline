# PR Title (concise, imperative)

## Summary

<!-- 1-3 bullets on what changed and why. -->

## Heal Contract Checklist (required for new features)

> Per `HEAL_CONTRACT.md` — every PR introducing user-visible behavior or external system interaction MUST satisfy ALL 6 boxes. PRs missing checks will not merge.

- [ ] **Registered feature_id** in `bin/heal_registry.py:VALID_FEATURES` (with rc number comment)
- [ ] **Emits ≥3 event types** via `emit_event()` (e.g. `detect`, `heal_attempt`, `heal_success`/`heal_failed`)
- [ ] **Spec documents failure modes** in `specs/SX_*.md` with `## Failure modes` table
- [ ] **All boolean flags wired both branches** — if `self._x_ok = False` in `__init__`, the `True`-set path exists and is reachable (rc11 SF lesson)
- [ ] **Updated `HEAL_FAILURE_MODES.md`** with new rows
- [ ] **Tested**: at least one failure path triggered locally + emit_event verified in heal_events.jsonl

If this PR is a **bugfix** or **internal refactor** (not a new feature), tick:

- [ ] N/A — not a new feature, heal contract not required

## Test Plan

- [ ] `python3 -c "import ast; ast.parse(open('bin/recorder_consumer_lite.py').read())"` passes
- [ ] Manual run: ___ (describe)
- [ ] CI green

## Risk

<!-- What could break? Rollback strategy? Touches RC release pipeline? -->

---
🐚 Generated per HEAL_CONTRACT.md v1 (2026-05-09)
