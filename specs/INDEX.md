# Spec Index — Cluster Dispatch Routing

> Howard 2026-05-07: Iron-law canon — every spec in this directory must
> produce REAL artifacts. No placeholders, no mocks, no TODO stubs. If a
> spec's acceptance criteria can be met with synthetic data, it's broken.
> See `bin/spec_lint.py` (D21) for mechanical enforcement.

## Recent batch (2026-05-07): MC mod + Pipeline 2 enrichment

| ID  | Spec | Priority | Time | Depends on | Status |
|-----|------|---------:|-----:|-----------|--------|
| D15 | recorder metadata stamp | done | 30 m | — | ✅ shipped (lite-v0.25) |
| D16 | server-side Paper Fabric mod | P1 | 60 m | D15 | ✅ shipped (2026-05-07 by self-dispatch, awaits Paper restart for activation) |
| D17 | .exe auto-installs mod + Fabric loader | P1 | 90 m | D15, recorder release | ✅ shipped (2026-05-07 by self-dispatch, helper module + 12 tests; PyInstaller bundling pending GHA workflow update) |
| D18 | D5 detect real_game_state classifier | P2 | 30 m | D5, D15 | ✅ shipped (2026-05-07 by self-dispatch) |
| D19 | multi-MC-version build matrix | P2 | 60 m | D15 | ✅ shipped (2026-05-07 self-dispatch, CodexResearcher verified versions on maven.fabricmc.net, 13 contract tests, full 4-version CI matrix restored) |
| D20 | E2E mod-to-tarball integration test | P1 | 90 m | D15, D16, D18 | ✅ shipped (2026-05-07 by self-dispatch, Python-side E2E with 4 tests; full Paper+Mineflayer chain deferred as Part B) |
| D21 | spec_lint.py + CI gate | P3 | 30 m | — | ✅ shipped (2026-05-07 by self-dispatch) |

## Cluster execution order (parallelizable)

```
Wave 1 (parallel, no deps):
  D16 (server mod)         → 60 min
  D17 (auto-install)       → 90 min
  D21 (spec lint)          → 30 min
  D19 (multi-version)      → 60 min  (depends only on existing D15 jar)

Wave 2 (after Wave 1 done):
  D18 (D5 classifier)      → 30 min  (consumer-side of D17 output)
  D20 (E2E test)           → 90 min  (needs D16 + D18)
```

Total wall clock if perfectly parallel: ~150 min (Wave 1 90 m + Wave 2 90 m).

## Iron-law spec body grep (must NOT match outside 不要做 sections)

```
banned_patterns = [
  "placeholder", "mock", "stub", "fake", "TODO", "FIXME",
]
```

Acceptable in `## 不要做` (banned-list) sections — that's where we DESCRIBE
what NOT to do. Anywhere else = spec broken, run D21 to catch.

## Pipeline closure status

After D16-D21 land:

| Layer | REAL artifact | Confidence |
|-------|--------------|-----------|
| Recorder client (.exe + human player) | video, depth, mouse/kb, **camera/player position via mod** | D15 ✅ + D17 (P1) |
| Cluster (Paper + Mineflayer) | video, depth, **camera/player position via server mod** | D16 (P1) |
| Authenticity validator (D5) | distinguishes mod-driven from metadata-derived | D18 (P2) |
| MC version coverage | 1.20.1, 1.20.4, 1.21.1, 1.21.4 | D19 (P2) |
| End-to-end CI gate | proves chain works on every PR | D20 (P1) |
| Spec template enforcement | rejects placeholder/TODO at lint time | D21 (P3) |

## Pre-authorized actions (Howard 2026-05-07, gym session)

- File creation/deletion in repo, `/tmp/`, `/var/folders/.../T/`
- gh release upload/download/edit on `oyster-gamedata-pipeline`
- gh release creation for new mod versions
- Workflow trigger via `gh workflow run`
- swarm controller continued operation (do NOT restart it)
- side-car daemon launch/stop (auto-upload-watcher, auto-disk-manager)
- Python module edits in `bin/` (no daemon restart needed — re-imports per
  invocation)
- `bin/auto_disk_manager.sh` aggressive cleanup at <15 GiB
- New `mc-mod/` build via gradle (when implemented)
