# rc19.0.3 — Autonomous Coord-System Fix RFC

**Mode**: session-bound autonomous loop. Howard away (driving). mac-1 = lightweight orchestrator only.
**Exit gate**: physics tests + handedness test ALL GREEN. Not "AI says done".
**Created**: 2026-05-14

## The 3 bugs to fix in `bin/finalize_session.py`

1. **Coord handedness** — PRD §3.2 wants left-handed (+X right, +Y up, +Z forward; `docs/PRD_EN.md:142`). MC is right-handed. finalize backfill currently copies MC coords RAW. Need: negate ONE axis per the buyer-pipeline oracle.
2. **Velocity units** — game_state.jsonl `velocity_x/y/z` is blocks/tick. PRD wants m/s. Convert ×20.
3. **Missing gravity** — gameinfo.xlsx needs `world_gravity_mps2 = 32.0` (MC vanilla, NOT Earth 9.8).

## Autonomy mechanisms (the "几个方法让他们自治")

| # | Mechanism | Purpose |
|---|---|---|
| 1 | ScheduleWakeup self-pacing | Loop continues without Howard / mac-1 babysitting |
| 2 | Auto-fallback executor chain | cluster → cluster-retry-diff-model → Engineer subagent. Never stuck on one broken executor |
| 3 | Physics-test exit gate | Objective stop condition. Wrong axis → test red → loop CANNOT ship wrong math |
| 4 | Progress journal (this loop reads RC1903_PROGRESS.md each iteration) | Context bridge across iterations |
| 5 | Max-iteration cap (8) + HALT-flag | Won't infinite-burn; stuck → flags for Howard instead of looping forever |

## Work layers (loop advances through these)

### Layer 1 — Find buyer-pipeline coord oracle (ground truth)
Search `~/Downloads/oyster/` for the Mineflayer/bot pipeline's MC→buyer-LH conversion code. `docs/BUYER_SPEC_COMPLIANCE.md` + `docs/PRODUCTION_LINE.md` say "X negated". Find the ACTUAL code. That's the oracle — copy its axis convention. If not found → HALT, flag Howard (don't guess).

### Layer 2 — Write physics sanity tests (BEFORE touching finalize)
`tests/bin/test_finalize_coord_physics.py`:
- `test_walking_speed_realistic` — post-finalize walking speed magnitude in 2.0-6.0 m/s (MC walk = 4.317 m/s). Catches unit-conversion errors.
- `test_gravity_acceleration` — falling-frame vertical velocity Δ ≈ 32 m/s² (tolerance 28-36). Catches sign/unit errors.
- `test_left_handed_coordinate_system` — (right × up) · forward > 0.99 after applying frame quaternion. Catches handedness errors.
- `test_gravity_field_present` — gameinfo.xlsx has world_gravity_mps2 ≈ 32.0.

### Layer 3 — Apply transform in finalize_session.py
Using Layer 1's oracle axis convention. Velocity ×20 then same axis negate. Quaternion handedness-mirror. gameinfo.xlsx + metadata.json coord markers.

### Layer 4 — Verify ALL green
```
python3 -m pytest tests/bin/test_finalize_coord_physics.py -v
python3 bin/prd_test_left_hand_coordinates.py /tmp/rc19.0.2-session/session_20260513_203931_70db9d7b
python3 -m pytest tests/bin/test_finalize_session_backfill.py -v   # no regression
```
ALL green → loop done. Any red → next iteration.

## Repo
`/Users/howardli/Downloads/oyster-agent-runner`, branch `stream-rc19.0.3-coord-units` from `stream-rc19.0.2`.
Test session: `/tmp/rc19.0.2-session/session_20260513_203931_70db9d7b/`

## Exit conditions
- **SUCCESS**: Layer 4 all green → commit + push branch + open PR + stop loop
- **HALT**: Layer 1 oracle not found OR 8 iterations without green → write HALT reason to journal, stop loop, flag Howard
