# Cluster Week 1 Deliverables — 2026-05-18

Aliyun cluster dispatch from 09:33 PT, Howard "直接集群作业 自己推进自己迭代" directive.

## What's here

| Dir | What | Model | Status |
|-----|------|-------|--------|
| D1-mc-mod/ | Fabric mod that captures GL depth buffer per frame → `tick_<N>.bin` | qwen3.6-plus | Code complete, needs Windows + MC 1.21.1 + Fabric Loader 0.15+ to validate |
| D2-zbuffer-exr/ | Python converter `.bin` ticks → camera-frame-aligned `.exr` | deepseek-v3.2 | Code complete (NEW design, incompatible with existing `bin/zbuffer_to_exr.py`) |
| D3-audit-smoke/ | H8 audit + end-to-end smoke (already landed) | qwen3.6-plus (after MiniMax-M2.5 hallucinated) | **LANDED in main code via 9f20d4a** |

## What's NOT landed yet & why

### D1 (mc-mod)
- Goes into the `vendor/recorder/` submodule, not this repo's tree.
- Cannot be validated on mac1 — needs:
  - Windows machine
  - Minecraft 1.21.1 (or 1.20.x with adapted mappings)
  - Fabric Loader ≥ 0.15
  - Real gameplay session to confirm `tick_<N>.bin` files appear
- **Howard action**: review code in this dir, then if good:
  1. `cd vendor/recorder/mc-mod/`
  2. Copy these files in, `git add`, commit to `gamedata-recorder` upstream
  3. Run `./gradlew build` on Windows; if green, push upstream
  4. Bump submodule pin in this repo

### D2 (zbuffer_to_exr.py NEW_DESIGN)
- Uses 12-byte `.bin` header `(u32 width, u32 height, u32 tick_id)` + JSON marker.
- Existing `bin/zbuffer_to_exr.py` uses raw f32 (no header) + YAML-line marker.
- These are incompatible designs.
- **Decision deferred until D1 produces real `.bin` files we can test against**.
- Once D1 is validated:
  - Either adopt D2's design and update all readers (run_da_v2_depth.py, prd_compliance_audit.py, canonical_pipeline.py, adversarial_quality_check.py)
  - Or adapt D2 to existing format (smaller diff, less elegant)

### D3 (LANDED via commit 9f20d4a)
- `bin/prd_compliance_audit_H8_patch.py` — library function `evaluate_h8()` with 5 status branches + EXR-readable verification + PASS_DEGRADED for high gap_miss_ratio
- `bin/zbuffer_pipeline_smoke.py` — end-to-end synthetic test
- `tests/test_zbuffer_pipeline_smoke.py` — pytest wrapper
- Self-contained — does NOT depend on D1/D2 to run; uses synthetic fixtures.

## Model-task lessons (write these into dispatch-reference.md)

| Model | Strength | Weakness |
|-------|----------|----------|
| qwen3.6-plus | Systems code (Kotlin mc-mod, Python audit) | — |
| deepseek-v3.2 | Algorithm / alignment math (D2's bisect + nearest-tick) | — |
| MiniMax-M2.5 | Crypto/cipher metadata, NOT multi-file Python audit | Hallucinated "TASK RESULT: completed after 40 turns" with zero files when given D3v1 spec |

## Next steps

Week 2 SPECs (S1–S5: sync_tolerance, input_latency, mic capture, game audio, mic consent) will dispatch on the next autonomous loop tick.
