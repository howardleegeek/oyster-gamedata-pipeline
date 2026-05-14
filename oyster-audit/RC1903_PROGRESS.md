# rc19.0.3 Autonomous Loop — Progress Journal

> Each loop iteration: READ this first, do one chunk, UPDATE this, ScheduleWakeup.
> RFC: oyster-audit/RC1903-AUTONOMOUS-RFC.md

## Status: ✅ LOOP COMPLETE — exit gate met (22 passed, 1 honest skip)

## Layer progress

| Layer | Status | Notes |
|---|---|---|
| L1 — find buyer-pipeline oracle | ✅ DONE | COORDINATE_SYSTEMS_GUIDE.md:55 + quaternion_utils.py |
| L2 — write physics sanity tests | ✅ DONE | 4 tests; 1 (handedness) redesigned mid-loop after design bug found |
| L3 — apply transform in finalize | ✅ DONE | 2 rounds — round 1 negate-yaw insufficient, round 2 rewrote euler_to_quat to buyer_spec |
| L4 — verify all green | ✅ DONE | 3 physics pass + 1 honest skip + 19 backfill regression pass |

## Loop outcome

**SUCCESS**. Exit gate (physics + oracle-match tests green) met after 1 iteration, 2 internal L3 rounds.
The physics-gate design WORKED: it caught 3 real bugs and 1 of my own test-design errors:
- L4 R1 caught: velocity still blocks/tick → fixed (×20)
- L4 R1 caught: gravity field missing → fixed (augment gameinfo)
- L4 R1 exposed: my handedness test was unmeasurable (quaternions are always proper rotations) → redesigned test to diff against the REAL vendor/enrichment oracle module
- L4 R2 caught: the local euler_to_quat did yaw-about-Z; buyer_spec is yaw-about-Y → rewrote to match oracle, verified bit-for-bit
- L4 R2 caught: PR#18 backfill test had stale pre-×20 velocity values → updated

## L1 ORACLE — ground truth (do NOT re-derive, USE this)

**Canonical guide**: `vendor/enrichment/docs/COORDINATE_SYSTEMS_GUIDE.md:55`
**Tested module**: `vendor/enrichment/src/oyster_enrichment/quaternion_utils.py`

### Minecraft → Buyer mapping (verbatim from guide)
- MC: Right-handed, Y-up, `+X` east, `+Y` up, `+Z` south
- Buyer: Left-handed, `X` right, `Y` up, `Z` front
- "South-as-+Z means +Z already points away from a north-facing player" → MC +Z maps to buyer +Z (front), NO Z negation
- **"Negate yaw to swap CW/CCW"** ← this is how RH→LH handedness is handled (rotation, not position)
- 1 block = 1 m → metric_scale = 1.0
- No roll in Minecraft

### Implementation contract for L3
- Position: MC (x, y, z) → buyer (x, y, z) — NO axis negation (per guide; handedness handled via yaw)
- Velocity: MC blocks/tick → buyer m/s: multiply by 20 (20 ticks/sec, 1 block=1m). Same axis mapping as position (no negation).
- Quaternion: use `euler_to_quat_xyzw(pitch, yaw=-mc_yaw, roll=0, convention="buyer_spec")` from quaternion_utils.py — NEGATE the MC yaw. Do NOT hand-roll quaternion math.
- gameinfo.xlsx: world_gravity_mps2 = 32.0 (MC vanilla, NOT Earth 9.8)

### Open question for L4 to resolve via physics test
The guide says position needs NO axis negation and handedness is fixed by yaw-negation alone. The `test_left_handed_coordinate_system` physics test (rotate basis by frame quaternion, check X×Y·Z sign) is the JUDGE. If it fails → the yaw-only approach is insufficient and a position axis ALSO needs negating. The test decides — not AI derivation.

## Iteration log

### Iteration 1 (2026-05-14)
- Executor: mac-1 direct (read-only investigation — allowed under IRON LAW)
- L1: searched ~/Downloads/oyster + oyster-agent-runner → found vendor/enrichment buyer-pipeline submodule
- Located canonical COORDINATE_SYSTEMS_GUIDE.md with explicit MC row
- Recorded the MC→buyer contract above
- Next: L2 — write physics sanity tests

## Fallback ledger

- Iter 1: mac-1 direct (read-only, no executor dispatch needed for L1 search)

## HALT flag

NONE — loop healthy. L1 oracle found cleanly (no guessing needed).
