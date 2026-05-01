# Buyer-Spec Production Line — Status & Gaps

**As of 2026-05-01**, the Phase 1 → buyer-spec v1 pipeline runs end-to-end
on real Mineflayer captures with `lint_buyer_spec.py` exit 0.

## Pipeline at a glance

```
Paper Minecraft server (1.20.4, Java 21)
       │
       ▼
oyster-agent run-mc → trajectory.jsonl (cot+metadata+inputs+manifest)
       │
       ▼
oyster-agent adapt-buyer-spec --placeholders <dir> --pad-to-min-records 9000
       │
       ▼
buyer/ {video.mp4, action_camera.json, systeminfo.json, gameinfo.xlsx, manifest.json, gameinfo.json, depth/×1801}
       │
       ▼
lint_buyer_spec.py → exit 0
```

## What's real (production-grade)

| Field | Source | Notes |
|---|---|---|
| `player_position` | `bot.entity.position` | Buyer left-hand frame, X negated |
| `player_rotation_oula` | `bot.entity.yaw/pitch` | Radians → degrees, sign-flipped per frame conversion |
| `player_rotation_quaternion` | derived | Y-X-Z extrinsic Euler order, matches `oyster_enrichment.quaternion_utils` |
| `camera_position` | follow offset | `[0, 1.6, -3.0]` behind player in buyer frame |
| `camera_speed` | finite difference | Vector3 per-axis m/s (was scalar — fixed) |
| `camera_intrinsics` | pinhole | fx/fy from FOV=70°, cx/cy=center for 1920×1080 |
| `metric_scale` | constant | 1.0 (Minecraft block ≈ 1 m) |
| `time` | timestamp | Anchored to `MinecraftStreamWriter` start |
| `frame` | counter | 0..N-1 |
| `fps` | constant | 30.0 |

## What's synthesized (placeholder, with caveats)

| Field | Synthesis | Justification |
|---|---|---|
| `mouse_x`, `mouse_y` | 0.5 | Phase 1 uses high-level Mineflayer actions, no mouse. Buyer spec requires non-null. |
| `mouse_dx`, `mouse_dy` | 0.0 | Same as above. |
| `keyCode` | `[]` | Same as above. |
| `route_type` | 1 | Constant; we don't yet model route taxonomy. |
| `camera_Follow Offset` | `[0, 1.6, -3.0]` | Vanilla Minecraft third-person F5 offset. |

## What's a placeholder file (Phase 2 work)

| File | Current state | Production owner |
|---|---|---|
| `video.mp4` | ffmpeg `testsrc` pattern, 1920×1080@30fps, sized to record count | OBS spectator capture (Phase 2) |
| `gameinfo.xlsx` | Static workbook from `oyster-enrichment/bin/generate_gameinfo_xlsx.py` | Operator-curated per-clip metadata |
| `depth/*.exr` | 1801 hardlinks of one 96×96 single-channel EXR | Depth-Anything V2 inference per frame |

## Iteration validation (100-iter sprint, COMPLETE)

**Final result: 100/100 lint pass, 0 errors, 0 warnings, ~48 min wall clock.**

| Metric | Value |
|---|---|
| Iterations | 100 |
| Lint pass | 100 |
| Lint errors (cumulative) | 0 |
| Lint warnings (cumulative) | 0 |
| Records per iter | exactly 9000 (every iter) |
| Total seconds: min / p50 / mean / p95 / max | 27 / 28 / 28.7 / 34 / 36 |
| Stddev | 1.71s |
| Capture (Mineflayer) mean | 3.3s |
| Adapter (ffmpeg + EXR copy) mean | 25.0s |
| Cumulative compute | 47.8 min |

**Bucket trend (drift check):**
| Bucket | Mean total seconds |
|---|---|
| iters 1-25 | 28.96s |
| iters 26-50 | 29.00s |
| iters 51-75 | 28.32s |
| iters 76-100 | 28.52s |

Spread <0.7s across the entire run — no degradation, no leak.

**Timing histogram (mode = 28s, right-skewed):**
```
27s:  6 ######
28s: 63 ###############################################################   ← MODE (63%)
29s: 17 #################
30s:  4 ####
31s:  4 ####
32s:  1 #
34s:  2 ##
35s:  1 #
36s:  2 ##
```

Live log: `/tmp/oyster_iter_log/iter_NNNN.json`
Final summary: `/tmp/oyster_iter_log/final_summary.json`
10 evidence buyer dirs preserved at `/tmp/oyster_iter_runs/iter_{0010,0020,...,0100}_buyer/`.

## ⚠️ Critical production gap surfaced by sprint (RESOLVED)

**Every one of the 10 preserved buyer dirs from the 100-iter sprint had `unique_camera_positions=1`.**

The mock provider issues `noop` actions only — the Mineflayer bot never moves. All 9000 records in every output bundle had an identical `camera_position`/`player_position`. The buyer-spec linter passed (positions are allowed to repeat) but the resulting training data was degenerate: the same frame replicated 9000 times.

This is the most important finding of the sprint. **Lint conformance ≠ buyer utility.**

### Resolution: ScriptedProvider

`src/oyster_agent_runner/providers/scripted.py` (new) emits randomized
walk/look/noop/dig commands deterministically (seeded RNG, no LLM cost).
Action mix: 60% `move_to`, 25% `look`, 10% `noop`, 5% `dig`. Move
targets are local-relative to the bot's current position (extracted
from the latest observation), so the bot walks rather than teleports.

**Before vs after** (smoke test, 60 max-steps):

| Metric | `--provider mock` | `--provider scripted` |
|---|---|---|
| unique camera positions | 1 | **42** |
| XZ travel distance | 0 | ~9.5 blocks |
| Y delta | 0 | -4.06 (bot fell into a 1-block dip) |
| Lint exit | 0 | 0 (still green) |

`buyer_spec_pipeline.sh` now defaults to `--provider scripted`.
Override with `--provider mock` for legacy stationary captures or
`--provider claude-thinking` for real-LLM-driven trajectories.

Tests: `tests/test_scripted_provider.py` — 12 tests covering RNG
determinism, action distribution (60/25/10/5), move-radius bounds,
look-angle bounds, observation-extraction edge cases (steady-state
vs spawn observation shapes, malformed JSON, missing position).

## Production gaps surfaced by brute iteration

### Gap #1 — Real video capture (Phase 2)

The current ffmpeg-synthesized `testsrc` video matches the buyer-spec
schema (resolution, fps, codec, duration ∈ [300, 360]s) but contains no
gameplay imagery. **This is fine for L4 lint** but not for AI training.

Production fix: OBS embedded recorder (already in `OWL Control` fork) pointed
at a Minecraft spectator client running alongside the Mineflayer bot.

### Gap #2 — Real depth inference (Phase 2)

The 1801 hardlinked placeholder EXRs satisfy the lint but are pure
noise. Depth-Anything V2 inference per frame is the production path.

Production fix: `oyster-enrichment/bin/png_depth_to_exr.py` already wires
DepthAnything → EXR; we just need to run it on the real video.

### Gap #3 — Real gameinfo.xlsx authorship

`generate_gameinfo_xlsx.py` produces a static workbook with placeholder
operator fields (scene complexity tier, FOV degree, notes). For real
shipments these need operator review and per-clip annotation.

Production fix: thin annotation UI (or just operator-edited XLSX) before
buyer delivery.

### Gap #4 — Time-axis drift between video and action_camera

Action_camera timestamps span the **wall-clock duration** of the
Mineflayer session (~8m13s for 9000 steps × ~50ms/step including
overhead). The synthesized video is exactly 300s. Lint doesn't check
time alignment, but a buyer playing the video at 30fps would see
action_camera record times that don't match.

Production fix: Phase 2 OBS capture will be wall-clock-aligned to the
Mineflayer session, eliminating drift.

### Gap #5 — `bot.position` nesting (FIXED)

The adapter's `_position_from_obs` originally only looked at top-level
`obs.position`, but mineflayer's steady-state observations wrap player
state under `obs.bot.position`. Only the spawn event (1 of N) had
top-level position, so 4 of 5 OBSERVATION events were silently dropped.

Fixed in this commit. Regression test added:
`test_adapter_extracts_position_from_obs_bot_nested`.

### Gap #6 — Vector3 vs scalar `camera_speed` (FIXED)

Buyer spec §3 row 9 requires `camera_speed: list[3] floats` (per-axis
m/s), not a scalar magnitude. The adapter previously emitted scalar.

Fixed in this commit. Test updated:
`test_adapter_camera_speed_finite_difference` now asserts the Vector3
shape AND that the mc_z movement maps correctly to buyer +Z.

### Gap #7 — Mouse/keyCode null-by-design (FIXED)

Buyer-spec lint rejected `null` values for `mouse_x/y/dx/dy/keyCode`.
Phase 1 has no mouse/keyboard layer (high-level Mineflayer actions
only), so we synthesize neutral defaults: `0.5/0.0/[]`. Downstream
consumers can detect synthesized inputs via `systeminfo` provenance.

Fixed in this commit.

### Gap #8 — pad_to_min_records option (NEW FEATURE)

Buyer's 5-min minimum video duration (300s @ 30fps = 9000 frames)
exceeds typical short-capture record counts. Added `--pad-to-min-records`
that replicates the last real record at 1/fps spacing, preserving real
ground truth at the head and zeroing camera_speed in the padded tail.

This is a stop-gap for iteration validation. Production should target
real 5-min captures (~9000 steps at the current ~18 steps/sec rate
takes ~8 minutes wall-clock, which exceeds the 300-360s buyer window
and triggers Gap #4).

## Three-game roadmap (Howard's directive)

| Game | Status | Capture mechanism | Notes |
|---|---|---|---|
| Minecraft (Mineflayer) | ✅ pipeline green end-to-end | Mineflayer bot + Paper server (this Mac) | Real engine state, currently running |
| BeamNG.drive | 🟡 telemetry capture script staged in `oyster-enrichment/bin/beamng_telemetry_capture.py` | UDP port 64256 from Howard's Windows box | Needs Windows host |
| CS2 | 🟡 demoparser path designed | Post-hoc `.dem` parse via `demoparser2` | Needs Howard demos |

Minecraft is the "today" game — proven, repeatable, automated. The
other two come online when Howard provides Windows access (BeamNG)
or demo files (CS2).

## Files touched this sprint

- `src/oyster_agent_runner/buyer_spec_adapter.py` — Vector3 speed, bot.position, mouse/keyCode, padding, ffmpeg synth, EXR/XLSX copy
- `src/oyster_agent_runner/cli.py` — `--placeholders` and `--pad-to-min-records` flags
- `tests/test_buyer_spec_adapter.py` — 4 new regression tests (31 total)
- `bin/iterate_buyer_spec.sh` — brute-force iteration runner

## Next steps (after sprint completes)

1. Verify pass rate over 100 iterations; investigate any failures.
2. Wire OBS spectator capture (Phase 2) to replace synthetic video.
3. Wire DepthAnything V2 to replace placeholder EXRs.
4. Add operator UI for per-clip XLSX authorship.
5. Acquire Windows host for BeamNG capture path.
