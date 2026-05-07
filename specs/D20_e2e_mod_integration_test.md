---
task_id: D20
project: oyster-gamedata-pipeline
priority: 1
estimated_minutes: 90
depends_on: [D15 mc-mod, D16 server mod, D18 D5 classifier]
modifies:
  - tests/e2e/  (new dir)
  - tests/e2e/test_mod_to_tarball.py  (new)
  - .github/workflows/e2e-mod.yml  (new)
must_not_touch:
  - production .exe path
  - production swarm_controller.sh
executor: glm
iron_law: REAL ONLY — E2E test runs a REAL Paper server + REAL bot, NO mocks
---

# D20: End-to-end mod ↔ recorder integration test

## 目标

Today we have:
- D15 contract test (schema-level only — no JVM)
- D5 unit tests (record-level only — synthetic data)

Missing: **a single test that proves the whole chain works**: Paper server
loads mc-mod-server.jar → spawns bot → bot moves → JSONL accumulates →
buyer_spec_pipeline.sh consumes it → buyer_spec_adapter overlays real
fields → D5 verdicts as REAL (mod-driven).

Without this test, every release is yolo. With it, CI catches the silent
failures (JSONL path mismatch, schema rename, fabric-api breaking change,
permission issue) before they reach testers.

## Architecture

```
test_mod_to_tarball.py:
  setup:
    - download mc-mod-server-X.Y.Z.jar from D16 GHA artifacts
    - boot Paper 1.21.4 with the .jar in mods/, on random port
    - spawn ONE Mineflayer bot, walk it 30 seconds in a loop
  exercise:
    - run buyer_spec_pipeline.sh against the captured session
    - assert it emits a buyer-spec tarball
  verify:
    - extract tarball
    - assert action_camera.json contains records with _real_game_state=true
    - assert position values are non-zero AND non-constant
    - assert D5 verdicts as REAL (mod-driven) per D18
  teardown:
    - kill Paper, remove temp dirs
```

## 验收标准 (REAL ONLY)

- [ ] `tests/e2e/test_mod_to_tarball.py` test_full_chain():
      - boots an actual Paper 1.21.4 server (downloaded once, cached)
      - spawns an actual Mineflayer bot (Node 20 + mineflayer 4.x)
      - runs an actual buyer_spec_pipeline.sh
      - extracts an actual tarball
      - runs an actual D5 classifier
      - asserts REAL (mod-driven) verdict
- [ ] All assertions check for REAL behaviour:
      - tarball file size > 50 MB (= real video + real depth)
      - action_camera N records ≥ 1500 (covers buyer-spec 1801 minimum)
      - ≥1000 records have `_real_game_state=true`
      - position variance > 1 m (= bot actually moved)
- [ ] CI workflow `.github/workflows/e2e-mod.yml`:
      - sets up Java 21 + Node 20 + Python 3.11
      - downloads mc-mod-server jar from D16 build
      - downloads Paper jar from official Paper API
      - runs the test (timeout 10 min)
      - uploads tarball as artifact for inspection
- [ ] Test takes < 8 min wall clock (Paper boot 30 s + bot 60 s + D4 90
      s + D5 5 s + buffer)
- [ ] No mocks: no `mock.patch`, no synthetic JSONL, no fake Paper
      response. Failure = REAL failure of REAL infra.

## REAL artifact criterion

- Test failure with NO ERROR is forbidden. If chain breaks, traceback or
  explicit `assert <reason>` — never silent skip.
- Test must NOT pass when mod is disabled. Add a negative-control variant
  that boots Paper WITHOUT the mod and asserts D5 reports PARTIAL.

## 不要做

- 不要 mock Paper server (no embedded Java fakes — cargo cult)
- 不要 use Mineflayer scripted provider only for E2E (use real LLM-driven
  bot per CLAUDE.md spec, even if rate-limited via mock LLM gateway)
- 不要 cache D5 verdicts (run real classifier every time)
