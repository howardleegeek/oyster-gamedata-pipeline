# SPEC_R26_VIDEO_CONTENT_HASH — Video-Pixel Perceptual Hash Residual

> **Project:** oyster-agent-runner | **Author:** Vera Sterling (Algorithm Agent), commissioned by Howard Li
> **Date:** 2026-05-06 — Stream W4-6 of 8-stream wave-4 push | **Status:** Spec / ISC — design only
> **Predecessors:** `SPEC_R13_MULTIMODAL.md` (IL10 ABSTAIN, MultiModalExtras, V₃ ABSTAIN-by-design),
> `SPEC_V4_BUYER_SIGNED_PROTOCOL.md` (B-01/B-03 threat model, raw-RGB hash technique).
> **Numeric registry:** R24 reserved for buyer-byte-diff (W4-2); R25 for W4-4; **R26 = this spec.**

---
## 0. TL;DR — Why R26 Exists

At wave-3 detection (`recorder-v0.24.0-wave3-73pct`) only two critical
attacks remain: **B-01** (self-consistent oula+quat swap; R02/R03 PASS
by construction) and **B-03** (coordinated keyCode + inputs.jsonl W→B
swap; both producer artifacts lie consistently, R13 PASSes). V₄ closes
both byte-exactly but loads 5 frames/dataset of buyer signing overhead
(24h SLA per V₄ § 7.2).

**R26 is the cheap B-01 alternative:** the producer mutated
`camera_rotation_oula` but `video.mp4` pixels still show the original
yaw=90° turn. Hash sampled frames; compare against the hash predicted
by declared yaw. Discrepancy ⇒ yaw was mutated.

R26 is **complementary, not a replacement, for V₄.** V₄ remains gold
standard for shipping (B-03 + worst-case B-01 with HUD-less games).
R26 ships first as low-overhead V₁-only signal; V₂ dispatch in Phase B.

| Property | V₄ | R26 |
|---|---|---|
| Operational overhead | High (5 frames buyer-signed, 24h SLA) | Low (one-time per-game LUT) |
| Catches B-01 / B-03 | Yes / Yes (definitive) | Yes (cond.) / Partial (HUD only) |
| Per-dataset cost | O(1) sig verify | ~10s ffmpeg extract |
| Trust anchor | Buyer pubkey | Per-game LUT signed by Howard |
| Solo lift estimate | ~13% (B-01 + B-03) | **~7% absolute (B-01 only)** |

---
## 1. Iron Law Extension — IL13

> **IL13 — Per-Game Predictability.** Any residual comparing observed
> video-frame hash to expected MUST (a) declare the `game_id` calibrated
> against in `expected_hash_table.metadata.game_id`, (b) ABSTAIN when
> dataset's `game_id` is not in the table, (c) never auto-extrapolate
> from one game to another. Cross-game silent reuse degrades to false
> positives the buyer cannot debug.

**Rationale.** A pHash calibrated on Minecraft daylight at yaw=90° is
meaningless on a Half-Life 2 corridor. Silent reuse is harder to detect
than missing artifacts. **Enforcement.** `bin/audit_video_content_hash.py`
parses every shipped `expected_hash_table_*.json`, verifies
`metadata.game_id` populated, and verifies R26 ABSTAINs on synthetic
cross-game datasets.

---
## 2. Type Extensions

R26 reuses `MultiModalExtras.video_path` and `video_status` from
`SPEC_R13_MULTIMODAL.md` § 2. Adds three new types in
`oyster_runner/residuals_types.py`:

```python
class ExpectedHashTableMetadata(TypedDict):
    schema_version: Literal["expected-hash-table/v1"]
    game_id: str; scene_tag: str | None
    hash_algo: Literal["pHash64", "dHash64", "block_avg64"]
    grid_resolution_deg: float       # snap yaw/pitch to N° grid
    sample_period_sec: float; n_calibration_frames: int
    built_at: str                    # ISO-8601
    builder_identity: str            # NOT an LLM identity (per A-26-04)

class ExpectedHashTable(TypedDict):
    metadata: ExpectedHashTableMetadata
    table: dict[str, int]    # f"yaw={y:.1f},pitch={p:.1f}" → 64-bit hash

@dataclass(frozen=True)
class VideoFrameSample:
    frame_idx: int; timestamp_sec: float
    declared_yaw: float; declared_pitch: float; actual_phash: int
```

**Convention.** Entrypoint loads `ExpectedHashTable` once per dataset
(cached by `game_id`); ffmpeg sampling + pHash run once per dataset
(every 30th frame ≈ 1 Hz at fps=30). R26 receives precomputed
`list[VideoFrameSample]` via partial-application — **not per-frame
ffmpeg invocation.**

---
## 3. R26 — Video-Content-Hash Predicate

### 3.1 Function signature

```python
def r26_video_content_hash(
    rec: dict,
    neighbor: dict | None = None,
    video_path: str | Path | None = None,
    expected_hash_table: dict[float, int] | None = None,
) -> ResidualResult:
    """Verify video-frame pixel content matches declared yaw/pitch.

    For sampled frames (every Nth, default N=30 ≈ 1 Hz @ 30 fps):
      1. Decode frame at t = rec['frame_idx'] / declared_fps via ffmpeg.
      2. Compute pHash64 (or dHash/block-avg per table.metadata.hash_algo).
      3. Look up expected_hash_table[grid_snap(declared_yaw, declared_pitch)].
      4. residual = popcount(actual_phash XOR expected_phash).
      5. PASS iff residual ≤ threshold (default 8 bits).

    Non-sampled frames: SKIP (passed=True, detail='not_a_sampled_frame').

    config: sample_period_frames (int, default 30); hamming_threshold_bits
    (int, default 8); hash_algo (str, default "pHash64");
    grid_resolution_deg (float, default 5.0).
    """
```

R26 follows the wave-4 signature convention (`rec`, `neighbor`, plus
residual-specific args), unlike R13–R16's `(window, extras, config)`.
Per-dataset state bound by entrypoint via `functools.partial`. See
open question #4.

### 3.2 Algorithm sketch

**Phase A (one-time per dataset, in entrypoint).** Load
`expected_hash_table` from disk, cache by `game_id`. Iterate
`for i in range(0, total_frames, sample_period_frames)`; invoke
`ffmpeg -ss <i/fps> -i video.mp4 -frames:v 1 -f rawvideo -pix_fmt
rgb24 -s 32x32 -`; RGB→grayscale; compute `pHash64(gray)` via DCT;
store `VideoFrameSample`.

**Phase B (per-frame R26).** Skip non-sampled frames (PASS,
detail=`not_a_sampled_frame`). Otherwise: snap declared yaw/pitch to
grid `f"yaw={round(yaw/g)*g:.1f},pitch={round(pitch/g)*g:.1f}"`; look
up `expected = table.get(key)` (None → ABSTAIN
`yaw_outside_table_coverage`); `residual_bits = popcount(actual_phash
XOR expected)`; PASS iff `residual_bits <= hamming_threshold_bits`.

**Why pHash, not raw-RGB SHA-256.** V₄ uses raw-RGB SHA-256 because
buyer signs exact decoded pixels (V₄ § 2.3). R26's "expected" comes
from a LUT built on golden recordings that compress/encode slightly
differently from new datasets. pHash absorbs that drift while still
detecting wholesale content change (yaw=0° vs 90° → vastly different
DCT signatures). **Threshold 8 bits / 64:** pHash64 inter-class on
natural images is 25-32 bits (unrelated), 0-4 bits (re-encoding), 5-15
gray zone — 8 splits re-encoding (PASS) from content-change (FAIL).
See open question #1.

### 3.3 Verdict

`id="R26_video_content_hash"`; `value` = Hamming distance bits (0 if
SKIP, −1 if ABSTAIN); `threshold = config["hamming_threshold_bits"]`
(default 8); `passed = value <= threshold OR detail ==
"not_a_sampled_frame"`; `unit="hamming_bits"`; on FAIL `detail=
f"frame={i} declared_yaw={y:.1f} actual_phash={ah:#x} expected={eh:#x}
hamming={v}"`.

### 3.4 ABSTAIN encoding (per IL10 + IL13)

Eight reasons, all mapping to `passed=False, value=-1, detail=
"ABSTAIN:<reason>"`: `no_video_file` (path None or
`video_status=="missing"`), `ffmpeg_failed` (extract non-zero),
`no_expected_hash_table` (None), `game_id_mismatch` (per IL13),
`yaw_outside_table_coverage` (key not in `table.table`),
`no_yaw_field` (`camera_rotation_oula` missing), `hash_algo_mismatch`
(config ≠ metadata), `fps_unknown` (cannot resolve declared fps).

### 3.5 Adversarial test it should catch

```yaml
test_id: FI-B-01-R26
description: Swap camera_rotation_oula[1] = 90° → -90° AND re-derive
  camera_rotation_quaternion consistently. R02 PASS (norm=1 by
  construction). R03 PASS if pos also re-derived. Video pixels untouched.
expected: R26 FAIL every sampled frame in mutated region. Hamming ≈ 22-30.

test_id: FI-B-01b-R26-coordinated-pos
description: Variant where attacker also coordinates pos to keep R03 PASS.
expected: R26 FAIL identically (video-driven, immune to pos coord).

test_id: FI-VIDEO-RECODE-R26
description: False-positive sentinel. Re-encode video.mp4 with different
  x265 CRF (no content change). Compare against table built on original.
expected: R26 PASS. Hamming ≤ 4 for re-encoding. If FAIL, threshold=8
  needs raising (open question #1).
```

### 3.6 BFT consensus interaction

**V₁ (Claude):** YES (Phase A) — reference impl, re-derives from spec.
**V₂ (MiniMax/GLM):** DEFER to Phase B — ships V₁-only per W4-6 brief;
after 30d clean shadow, dispatch V₂ re-impl per BFT § 4.1 (disjoint
imports: V₁=imagehash, V₂=opencv DCT). **V₃ (physics oracle):** ABSTAIN
by design — pHash distance not tabulatable on open Cartesian
(yaw × pitch × game); stub `{decision:"ABSTAIN",
reason:"R26_not_in_oracle_table"}`.

**Per-frame quorum during V₁-only.** V₁ PASS / V₂ ABSTAIN / V₃ ABSTAIN
⇒ PASS. **V₁ FAIL alone MUST NOT decree REJECT** — flag SUSPECT, route
to HUMAN_REVIEW per BFT § 6.4. R26 binds REJECT only after V₂ joins
(open question #5).

---
## 4. Limitations (Honest Threat Model)

**4.1 Per-game LUT required (NOT auto-derivable from physics).** R26
needs a per-game LUT built from golden recordings of the actual game
+ rendering pipeline. **No way to derive Minecraft's pHash at yaw=90°
from first principles** — textures, shaders, time-of-day, biome all
matter. **v1 ships Minecraft-only**; each new game requires
calibration (open question #2). Cross-game silent reuse forbidden by
IL13.

**4.2 pHash fragility to encoder change.** Switching x265 → x264, CRF,
or `pix_fmt` shifts pHash 3-7 bits on identical content. Producer
pipeline upgrade without re-calibration ⇒ R26 false-FAILs broadly.
Mitigation: pin encoder/CRF in `manifest.video_pipeline_id`; rebuild
table on pipeline change (open question #6).

**4.3 ffmpeg extract cost (~10s per dataset).** 9000-frame datasets
sampled every 30 frames: 300 ffmpeg invocations × ~30ms = ~10s.
Acceptable for shipping; borderline for shadow back-fill. Optimization:
single-pass `-vf select` (~2s; open question #3).

**4.4 Static-scene blind spot.** Player stands still ⇒ all sampled
frames share the same expected hash — so does an attacker mutating yaw
to a different constant if the table has both entries. R26 catches
**transient yaw mutations during motion**; static defers to V₄ F4.

**4.5 HUD-rendered input is the ONLY B-03 partial.** B-03 mutates
`keyCode 87 → 88`. R26 sees this only if the game's HUD renders the
pressed key on-screen (tutorial overlays). Most production gameplay
does not. **R26 does NOT close B-03.** V₄ remains required.

**4.6 White-box attacker reading the table.** Attacker with read
access to `expected_hash_table_minecraft.json` can compute *valid*
yaw values matching mutated pixel content, then choose mutations
within equivalence classes. v1 defense: table out of producer's read
path (`s3://oyster-bft/_lookups/`); v2: per-dataset salt. Same class
as V₄ § 6.5 frame-selection-oracle leak.

---
## 5. Detection Lift Estimate

Per `RED_TEAM_TAXONOMY.md` § 6.2 wave-4 scoreboard, baseline 73% +
V₄ → 86% (closes B-01 + B-03); **alternative path:** baseline + R26
→ 80% (closes B-01 only). R26 is **alternative**, not additive, to V₄
for B-01. Combined deployment does NOT linearly stack — same attack
class via different mechanisms; combined ~14% (V₄'s 13% + marginal R26
from HUD-bearing B-03 partials + extra sampling density). **Strategic
case:** if V₄ signing SLA slips, R26 carries B-01 at 7% absolute —
significant fraction of V₄'s value at materially less buyer overhead.
If V₄ ships on schedule, R26 still adds defense-in-depth and faster
shadow-detection (no human in loop).

---
## 6. Operational Protocol

**6.1 Building expected_hash_table (per game, on first onboarding).**
`bin/build_expected_hash_table.py` (Phase A deliverable) takes
`--golden-video`, `--golden-action`, `--game-id`, `--scene-tag`,
`--grid-resolution-deg`, `--sample-period-sec`, `--output`. Output:
JSON mapping grid-snapped (yaw, pitch) → pHash64. Built once; signed
by Howard (NOT an LLM, per A-26-04); shipped via
`s3://oyster-bft/_lookups/`.

**6.2 Per-game vs per-scene granularity.** **v1: per-game only**
(`game_id` is the lookup key). Different scenes (cave vs outdoor) have
different pHashes for same yaw, raising false-FAIL but acceptable for
v1: Minecraft outdoor dominates; indoor/cave filtered via existing
scene-tag metadata and excluded from sampling. **v2: per-scene tables**
if v1 false-FAIL > 5% on shadow.

**6.3 Auto-derivation via golden video + script.** Yes — § 6.1's
builder. Input: producer-emitted golden recording from Howard or
trusted engineer; inputs/yaw from `action_camera.json`; pixels from
`video.mp4`; script grid-snaps and stores median pHash per (yaw, pitch)
bucket. Multiple goldens compose (median over sources) to harden
against single-recording encoding artifacts.

**6.4 Table refresh cadence.** Required when **any** of: game version
bump; producer encoder pipeline change; sustained false-FAIL > 2% on
shadow. Procedure: re-run builder on fresh golden; diff old vs new;
commit both with deprecation flag; verifier picks new table by
`manifest.video_pipeline_id` + `manifest.game_version`.

**6.5 Failure modes.** All sampled PASS → R26 PASS, decree per BFT
§ 3.3. One frame FAIL inside mutation region → R26 FAIL, decree
SUSPECT (V₁-only) / REJECT (V₂ joined). ABSTAIN(`game_id_mismatch`)
→ onboarding gap, build table. ABSTAIN(`ffmpeg_failed`) → producer
ships unreadable video, flag per IL10. R26 PASS but V₄ FAIL same frame
→ R26 false-negative, V₄ governs, rebuild + re-calibrate.

---
## 7. ISC — Ideal State Criteria for R26

Each criterion binary (YES/NO ≤ 1 second).

**7.1 Existence (V₁ Phase A).**
- **[C-26-01]** `bin/verify_pinns_claude.py` contains callable
  `r26_video_content_hash` matching § 3.1 (AST grep returns 1).
- **[C-26-02]** `bin/build_expected_hash_table.py` exists with `--help`
  exposing all § 6.1 flags (CLI smoke test).
- **[C-26-03]** `bin/audit_video_content_hash.py` enforces IL13 (CI
  exits 0; synthetic cross-game ABSTAINs).
- **[C-26-04]** `expected_hash_table_minecraft.json` in
  `s3://oyster-bft/_lookups/`, signed by Howard
  (`metadata.builder_identity == "howard.li@berkeley.edu"`).

**7.2 Coverage.**
- **[C-26-05]** Adversarial harness has `tests/byzantine/test_FI_B_01_R26.py`
  AND `test_FI_B_01b_R26_coordinated_pos.py` per § 3.5; R26 disabled →
  BFT PASSes mutation, enabled (Phase B) → BFT REJECTs.
- **[C-26-06]** False-positive sentinel
  `tests/sentinels/test_R26_recode_passes.py` per FI-VIDEO-RECODE-R26.
- **[C-26-07]** R26 ABSTAINs on the 8 cases in § 3.4
  (`tests/residuals/test_R26_abstain.py`).

**7.3 Operational.**
- **[C-26-08]** Per-dataset R26 cost p95 < 15s
  (`s3://oyster-bft/_metrics/r26_latency.jsonl`).
- **[C-26-09]** False-FAIL rate on canonical Minecraft < 1% (nightly
  shadow dashboard).

**7.4 Anti-criteria — must remain false.**
- **[A-26-01]** R26 never returns `passed=True` when video extraction
  fails (`tests/residuals/test_R26_ffmpeg_failure_abstains.py`).
- **[A-26-02]** R26 never silently reuses one game's table for another
  (IL13 audit + `test_R26_cross_game_abstains.py`).
- **[A-26-03]** R26 V₁-only phase never decrees REJECT alone (must
  flag SUSPECT, route HUMAN_REVIEW;
  `tests/orchestrator/test_R26_v1_only_no_reject.py`).
- **[A-26-04]** No `expected_hash_table_*.json` accepted whose
  `metadata.builder_identity` is an LLM identity (git-blame audit +
  builder allowlist; mirrors BFT § 4.2).

**7.5 ISC Tracker.**
```
ISC: Ideal State Criteria
Phase: PLAN (R26 video-content-hash design)
Criteria: 0 -> 9   (+9)
Anti:     0 -> 4   (+4)
+ [C-26-01..04] V1 existence + table + audit
+ [C-26-05..07] Adversarial + false-pos coverage
+ [C-26-08..09] Operational (latency, FP rate)
+ [A-26-01..04] ABSTAIN-honesty + IL13 + V1-only
```

---
## 8. Open Questions for Howard

1. **Hamming threshold default = 8 bits.** § 3.2 splits re-encoding
   noise (≤4 bits) from content change (≥22 bits) at 8. Minecraft's
   repeating textures (grass, stone) may compress the gap. Run § 3.5
   FI-VIDEO-RECODE-R26 sentinel on 100 canonical recordings; if max
   re-encoding distance > 6, raise default to 12. Spec calibration job
   now, or "calibrate later"?

2. **Per-game LUT scope at v1 launch.** § 4.1 says Minecraft-only.
   What's the second game? Voxel/blocky (Vintage Story, Boundless):
   same pHash at similar thresholds. High-fidelity (Cyberpunk, HL Alyx):
   pHash sensitivity may need re-tuning per game. **Most blocking
   question for implementation:** what shipping game roadmap should
   R26 plan for?

3. **ffmpeg extract optimization (one-pass vs N invocations).** § 4.3
   notes 10s → 2s via single `-vf select` filter. Worth implementing
   in v1, or ship N-invocations and optimize when binding? My rec:
   ship N-invocations (simpler, debuggable); optimize in v2 if Phase A
   profiling shows R26 dominating dataset latency.

4. **Function signature mismatch with R13 family.** W4-6 brief
   prescribes `(rec, neighbor, video_path, expected_hash_table)`, but
   R13–R16 use `(window, extras, config)`. R26's signature deviates.
   My rec: keep R26's signature; document per-dataset state bound via
   `functools.partial`; add `R26State` mirroring `R14State`.
   Acceptable, or harmonize fully?

5. **V₂ dispatch timing for Phase B.** § 3.6 defers V₂ based on V₁
   shadow stability. Trigger? My rec: 30 consecutive shipping datasets
   where V₁ R26 PASSes AND V₄ also PASSes (R26 not contradicting V₄'s
   ground truth). Then dispatch V₂ re-impl. Clear automatable
   graduation. Acceptable?

6. **Encoder pinning in producer manifest.** § 4.2 + § 6.4: R26 needs
   producer to pin encoder/CRF in `manifest.video_pipeline_id`. Is
   producer currently emitting this? If not: blocks R26 (deeper change,
   producer-side spec). If yes: name the field; we wire it in
   immediately. Resolves with `cat samples/canonical_recording_03/manifest.json | jq '.video_pipeline_id'`.

---
## 9. Document Provenance

Authored by Vera Sterling (Algorithm Agent), 2026-05-06, Stream W4-6.
Source-of-truth: `SPEC_R13_MULTIMODAL.md` (IL10, V₃ ABSTAIN,
MultiModalExtras), `SPEC_V4_BUYER_SIGNED_PROTOCOL.md` (B-01/B-03 threat
model, raw-RGB hash technique), `RED_TEAM_TAXONOMY.md` (B-01 § 99-114,
scoreboard § 6.2), `ARCH_BFT_CONSENSUS.md` (V₁/V₂/V₃ roles,
GREEN-ABSTAIN § 8.1, HUMAN_REVIEW § 6.4). Sibling specs NOT modified;
V₄ remains authoritative for shipping-grade B-01 coverage; R24 (W4-2)
and R25 (W4-4) reservations preserved. **Out of scope:** Python impl,
V₂ impl (Phase B), per-scene LUT (v2), encoder-pinning producer-side
spec, multi-game roadmap beyond Minecraft. *End of SPEC_R26.*
