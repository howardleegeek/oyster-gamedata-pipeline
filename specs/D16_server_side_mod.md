---
task_id: D16
project: oyster-gamedata-pipeline
priority: 1
estimated_minutes: 60
depends_on: [mc-mod foundation]
modifies:
  - mc-mod-server/  (new dir)
  - bin/buyer_spec_pipeline.sh  (one-line: read JSONL if present)
  - src/oyster_agent_runner/buyer_spec_adapter.py  (overlay branch)
must_not_touch:
  - mc-mod/  (client mod stays as-is)
  - bin/recorder_consumer_lite.py  (client-side, separate path)
executor: glm
iron_law: REAL ONLY — no placeholder, no mock, no TODO
---

# D16: Server-side Paper Fabric mod for Pipeline 2

## 目标

Pipeline 2 (cluster Mineflayer bots → buyer-spec tarball) currently derives
`action_camera` records from bot metadata. Build a SERVER-side Fabric mod
that runs inside the Paper server and streams REAL per-tick game state for
EACH connected bot to a JSONL file the cluster's `buyer_spec_adapter.py`
can consume.

This closes the same placeholder gap that D15 + the client mod close on
Pipeline 1 — but for the cluster path, where data volumes are much higher
(60+ slots × 4 cycles/hr).

## Architecture

```
Paper server (with mc-mod-server.jar in mods/)
        │ ServerTickEvents.END_SERVER_TICK (~20 Hz)
        ▼
ServerStateCapture per ServerPlayerEntity
        │ position, rotation, velocity per bot
        ▼
JsonlWriter — one file per bot username
~/.../paper_server/oyster_state/<bot_username>.jsonl
        │
        │ buyer_spec_pipeline.sh reads it for the matching bot
        ▼
buyer_spec_adapter.py uses real values (overrides metadata-derived ones)
```

## 验收标准 (REAL ONLY)

- [ ] `mc-mod-server/build.gradle` builds a Fabric server-side jar against
      Paper-compatible Fabric API (verify via `gradle build` in CI)
- [ ] Mod registers `ServerTickEvents.END_SERVER_TICK` listener
- [ ] For every `ServerPlayerEntity` (= every bot), one JSONL line per tick
      with EXACTLY the schema D15 defines (`game_state_overlay.EXPECTED_FIELDS`)
- [ ] Output dir defaults to `<server_dir>/oyster_state/` — created on
      first tick if missing
- [ ] `bin/buyer_spec_pipeline.sh` step 3.6 (NEW): reads the bot's JSONL,
      passes path to `oyster-agent adapt-buyer-spec --game-state-jsonl ...`
- [ ] `buyer_spec_adapter.py` accepts `--game-state-jsonl` flag, branches:
      if present → `game_state_overlay.apply_to_record` per frame; if
      absent → keep current metadata-derived behaviour
- [ ] D5 verdict on a fresh tarball has `action_camera` field analysis
      mention "real_game_state=true" (not just non-padded fingerprint)
- [ ] `tests/test_d16_server_mod_contract.py` mirrors D15's contract test
      against `mc-mod-server` Java + buyer_spec_adapter Python paths

## REAL artifact criterion

- The mod jar MUST start a Paper server when dropped into `mods/`. CI:
  spin Paper headless 30 s, drop a synthetic player, confirm one JSONL
  line per tick produced.
- buyer_spec_adapter MUST emit non-zero camera_position values when JSONL
  is supplied — not constants. CI: assert `set(record["camera_position"])
  != {0.0, 64.0, 0.0}` for at least 90% of records.

## 不要做

- 不要碰 client mc-mod/ (D15 已经覆盖)
- 不要做新的 buyer-spec 字段 (沿用 D15 schema)
- 不要加 socket / RPC dependencies between mod and adapter — JSONL only
  (per "跨语言对接精髓" lesson 2026-05-07)
