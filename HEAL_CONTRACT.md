# HEAL_CONTRACT.md — Self-Heal & Report Contract (v1, 2026-05-09)

> **Iron law (Howard 2026-05-09)**:
> "我们 introduce 了新的功能都是需要有良好的自愈和 report 系统."
>
> Every new feature in oyster-agent-runner MUST register a heal contract before merging. No exceptions.

## Why this exists

By rc11 we shipped 5 self-heal features (SF/SG/SH/SI/SK + rc10 B1-B5) ad hoc, each with its own logging, error handling, and reporting. Result:

- rc11 SF added `_mod_handshake_ok` field but never wired the `True` branch → real data shipped with `mod_handshake_ok=false` → buyer auto-quarantine
- rc9 depth pipeline silently hardcoded `device="cpu"` → testers waited 30 min then got empty manifest
- R02 fabric-yarn-watcher daemon died 2 days before anyone noticed

These are all **contract violations** that a framework would catch. This doc is the framework.

## The 4-layer model

```
L1 Convention   → standard event schema (in bin/heal_registry.py VALID_FEATURES)
L2 Aggregator   → emit_event() writes append-only JSONL
L3 Viewer       → heal_report.html generator (local, opens in browser)
L4 Enforcement  → this doc + PR template + lint check
```

## Contract for new features

When adding **any** new feature that touches user-visible behavior or external systems, the PR MUST include all 6 of:

### 1. Register feature_id

Add a constant to `bin/heal_registry.py:VALID_FEATURES`:
```python
"SX_my_feature_short_name",  # rc{N} description
```
Naming: `<phase_letter><number>_<purpose>` so SX = "Phase X". Use rc number in comment.

### 2. Emit at least 3 event types

A real feature emits across the lifecycle:
```python
from heal_registry import emit_event

# At detect time:
emit_event("SX_my_feature", "detect", "info",
           "feature ran, condition X observed",
           details={"x_value": 42})

# At remediation time:
emit_event("SX_my_feature", "heal_attempt", "warn",
           "X out of range, attempting auto-fix",
           remediation={"action": "auto_clean", "performed": True})

# At outcome time:
emit_event("SX_my_feature", "heal_success", "info",
           "auto-fix completed, X back in range")
# OR
emit_event("SX_my_feature", "heal_failed", "error",
           "auto-fix failed, prompting user",
           remediation={"action": "user_prompt", "performed": True,
                        "next_step": "tester restarts ffmpeg manually"})
```

### 3. Document the failure mode

In the spec (`specs/SX_*.md`), include a section:
```markdown
## Failure modes
| Mode | Detector | Severity | Auto-heal? | User next step |
|------|----------|----------|------------|----------------|
| X timeout | event_loop polls | warn | retry once | restart recorder |
| X corrupt | SHA mismatch | error | quarantine | re-record |
```

### 4. Wire BOTH branches of every boolean flag

If you add `self._x_ok: bool = False` in `__init__`, you MUST also write the code path that sets it to `True`. The rc11 SF mod_handshake bug was this exact failure: field defaulted False, "True branch" never wired.

Lint check (`scripts/lint_heal_contract.py` — to be added) greps for `self\._\w+_ok\s*:\s*bool\s*=\s*False` then verifies a matching `self\.\1_ok\s*=\s*True` exists somewhere in the file. PR fails CI if not.

### 5. Update HEAL_FAILURE_MODES.md

Add a row to the master table (covering all 49 known failure modes from Phase A audit). New features extend this — ensures we keep "knowable failure surface" visible.

### 6. PR checklist tick

`.github/PULL_REQUEST_TEMPLATE.md` (to be added) requires the PR author to tick:
- [ ] Registered feature_id in `heal_registry.py:VALID_FEATURES`
- [ ] Emits ≥3 event types via `emit_event()`
- [ ] Spec documents failure modes
- [ ] All boolean flags have both True and False branches wired
- [ ] Updated HEAL_FAILURE_MODES.md

## Enforcement timeline

- **rc15 (planned)**: framework lands. New features required to comply. Existing features migrated incrementally.
- **rc16**: lint script added. CI fails on contract violations.
- **rc17**: heal_report.html opens automatically on first launch + after each session.
- **Phase C**: heal_events.jsonl ships to backend → cross-tester aggregation → regression detection.

## What this is NOT

- ❌ Replacement for try/except — exception handling is per-function; heal contract is per-feature
- ❌ Replacement for logging — `_trace()` keeps logging implementation details; `emit_event()` is structured for aggregation
- ❌ Required for internal helpers (e.g. `_format_timestamp()`) — only for "feature" boundaries
- ❌ Excuse to skip tests — heal events complement tests, not replace

## Migration plan for existing features

| Feature | rc shipped | Compliance | Migration |
|---------|------------|------------|-----------|
| B1 close_confirm | rc10 | partial (terminator only) | wire `emit_event` rc15 |
| B2 ffmpeg_clean_close | rc10 | partial | wire rc15 |
| B3+B4 update bat | rc10 | none | wire rc15 |
| B5 disk space | rc10 | partial (terminator) | wire rc15 |
| SF terminator | rc11 | self-emits via terminator.json | already structured, wire to registry rc15 |
| SG heartbeat | rc11 | self-emits via health.json | wire to registry rc15 |
| SH preflight | rc12 | self-logs to startup.log | wire rc15 |
| SI orphan | rc13 | self-emits via terminator.json | wire rc15 |
| SK duration prompt | rc13 | none | wire rc15 |
| depth_dual_track | rc14 | self-emits via depth_manifest | wire rc15 |

All 10 features get wired in rc15 in one batch.
