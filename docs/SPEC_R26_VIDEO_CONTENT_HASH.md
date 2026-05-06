# SPEC_R26_VIDEO_CONTENT_HASH — Video-Pixel Perceptual Hash Residual

> **Project:** oyster-agent-runner | **Author:** Vera Sterling (Algorithm Agent), commissioned by Howard Li
> **Date:** 2026-05-06 — Stream W4-6 of 8-stream wave-4 push | **Status:** Spec / ISC — design only, no Python
> **Predecessors:** `SPEC_R13_MULTIMODAL.md` (IL10 ABSTAIN, MultiModalExtras, V₃ ABSTAIN-by-design),
> `SPEC_V4_BUYER_SIGNED_PROTOCOL.md` (B-01/B-03 threat model, raw-RGB hash technique).
> **Numeric registry:** R24 reserved for buyer-byte-diff (W4-2); R25 reserved for W4-4; **R26 = this spec.**

---

## 0. TL;DR — Why R26 Exists

At wave-3 detection (`recorder-v0.24.0-wave3-73pct`) only two critical
attacks remain:

| Attack | Why uncatchable today | Resolution |
|---|---|---|
| **B-01** Self-consistent oula+quat swap | R02/R03 PASS by construction | V₄ (heavy) **OR** R26 (cheap) |
| **B-03** Coordinated keyCode + inputs.jsonl W→B | Both artifacts lie consistently; R13 PASSes | V₄ definitive; R26 partial (HUD only) |

V₄ closes both byte-exactly but loads 5 frames/dataset of buyer signing
overhead (24h SLA per V₄ § 7.2). **R26 is the cheap B-01 alternative:**
the producer mutated `camera_rotation_oula` but `video.mp4` pixels still
show the original yaw=90° turn. Hash sampled frames; compare against the
hash predicted by declared yaw. Discrepancy ⇒ yaw was mutated.

R26 is **complementary, not a replacement, for V₄.** V₄ remains gold
standard for shipping (B-03 + worst-case B-01 with HUD-less games).
R26 ships first as a low-overhead V₁-only signal; V₂ dispatch in Phase B.

| Property | V₄ | R26 |
|---|---|---|
| Operational overhead | High (5 buyer-signed frames, 24h SLA) | Low (one-time per-game LUT) |
| Catches B-01 | Yes (definitive) | Yes (when oula→frame mapping exists) |
| Catches B-03 | Yes (definitive) | Partial (HUD only) |
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

**Rationale.** A pHash mapping calibrated on Minecraft daylight at
yaw=90° is meaningless on a Half-Life 2 corridor. Silent reuse is the
worst failure — harder to detect than missing artifacts.

**Enforcement.** `bin/audit_video_content_hash.py` parses every shipped
`expected_hash_table_*.json`, verifies `metadata.game_id` populated,
and verifies R26 ABSTAINs on synthetic cross-game datasets.

---

## 2. Type Extensions

R26 reuses `MultiModalExtras.video_path` and `video_status` from
`SPEC_R13_MULTIMODAL.md` § 2. Adds:

```python
# oyster_runner/residuals_types.py — TYPES ONLY
from typing import TypedDict, Literal
from dataclasses import dataclass

class ExpectedHashTableMetadata(TypedDict):
    schema_version: Literal["expected-hash-table/v1"]
    game_id: str             # "minecraft-1.20.4-vanilla", per session_manifest.game_id
    scene_tag: str | None    # "outdoor-daylight" | None
    hash_algo: Literal["pHash64", "dHash64", "block_avg64"]
    grid_resolution_deg: float       # e.g. 5.0 → snap yaw/pitch to 5° grid
    sample_period_sec: float         # e.g. 1.0 → 1 frame per second
    n_calibration_frames: int
    built_at: str                    # ISO-8601
    builder_identity: str            # "howard.li@berkeley.edu" — NOT an LLM (per A-26-04)

class ExpectedHashTable(TypedDict):
    metadata: ExpectedHashTableMetadata
    table: dict[str, int]    # key: f"yaw={y:.1f},pitch={p:.1f}"; value: 64-bit hash

@dataclass(frozen=True)
class VideoFrameSample:
    frame_idx: int
    timestamp_sec: float
    declared_yaw: float
    declared_pitch: float
    actual_phash: int
```

**Convention.** Entrypoint loads `ExpectedHashTable` once per dataset
(cached by `game_id`); ffmpeg sampling and pHash computation run once
per dataset (every 30th frame ≈ 1 Hz at fps=30). R26 receives
precomputed `list[VideoFrameSample]` via partial-application — **not
per-frame ffmpeg invocation.**

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

    config keys (read from buyer_spec):
      sample_period_frames (int, default 30)
      hamming_threshold_bits (int, default 8)
      hash_algo (str, default "pHash64")
      grid_resolution_deg (float, default 5.0)
    """
```

**Note.** R26's signature follows the wave-4 convention rather than
R13's `(window, extras, config)`. Per-dataset state bound by entrypoint
via `functools.partial`. See open question #4.

### 3.2 Algorithm sketch

```
Phase A — One-time per dataset (entrypoint, NOT R26 itself):
  1. Load expected_hash_table from disk (cached by game_id).
  2. ffmpeg-extract every Nth frame from video.mp4:
       for i in range(0, total_frames, sample_period_frames):
         ffmpeg -ss <i/fps> -i video.mp4 -frames:v 1 -f rawvideo \
                -pix_fmt rgb24 -s 32x32 -      # downscale before hashing
  3. For each extracted frame:
       gray  = rgb_to_grayscale(rgb)
       phash = pHash64(gray)                    # standard DCT-based pHash
       store VideoFrameSample(frame_idx=i, ..., actual_phash=phash)

Phase B — Per-frame R26 invocation:
  1. If rec['frame_idx'] % sample_period_frames != 0 → return PASS (skip).
  2. Look up matching VideoFrameSample (else ABSTAIN).
  3. y_snap = round(rec.camera_rotation_oula[1] / grid_res) * grid_res
     p_snap = round(rec.camera_rotation_oula[0] / grid_res) * grid_res
     key = f"yaw={y_snap:.1f},pitch={p_snap:.1f}"
  4. expected = expected_hash_table.table.get(key)
     If None → ABSTAIN(yaw_outside_table_coverage).
  5. residual_bits = popcount(actual_phash XOR expected)
  6. PASS iff residual_bits <= hamming_threshold_bits.
```

**Why pHash, not raw-RGB SHA-256.** V₄ uses raw-RGB SHA-256 because
buyer signs **exact decoded pixels**; any drift is producer
nondeterminism (V₄ § 2.3). R26's "expected" comes from a LUT built on
golden recordings that compress/encode slightly differently from new
datasets. pHash absorbs that drift while still detecting wholesale
content change (yaw=0° vs yaw=90° → vastly different DCT signatures).

**Why threshold 8 bits / 64.** pHash64 inter-class on natural images:
25-32 bits typical (unrelated scenes); 0-4 bits (re-encodings of same
content); 5-15 bits gray zone. **8 splits re-encoding (PASS) from
content-change (FAIL).** See open question #1.

### 3.3 Verdict

| Field | Value |
|---|---|
| `id` | `"R26_video_content_hash"` |
| `value` | Hamming distance bits; 0 if SKIP/PASS-by-skip; −1 if ABSTAIN |
| `threshold` | `config["hamming_threshold_bits"]` (default 8) |
| `passed` | `value <= threshold` OR `detail == "not_a_sampled_frame"` |
| `unit` | `"hamming_bits"` |
| `detail` | on FAIL: `f"frame={i} declared_yaw={y:.1f} actual_phash={ah:#x} expected={eh:#x} hamming={v}"` |

### 3.4 ABSTAIN encoding (per IL10 + IL13)

| Reason | When |
|---|---|
| `ABSTAIN:no_video_file` | `video_path` None or `extras.video_status == "missing"` |
| `ABSTAIN:ffmpeg_failed` | ffmpeg extract returned non-zero |
| `ABSTAIN:no_expected_hash_table` | `expected_hash_table` None |
| `ABSTAIN:game_id_mismatch` | `manifest.game_id != table.metadata.game_id` (IL13) |
| `ABSTAIN:yaw_outside_table_coverage` | Grid-snapped key not in `table.table` |
| `ABSTAIN:no_yaw_field` | `rec['camera_rotation_oula']` missing |
| `ABSTAIN:hash_algo_mismatch` | `config.hash_algo != table.metadata.hash_algo` |
| `ABSTAIN:fps_unknown` | Cannot resolve declared fps |

### 3.5 Adversarial test it should catch

```yaml
test_id: FI-B-01-R26
description: |
  The B-01 attack. Swap camera_rotation_oula[1] = 90° → -90° AND
  re-derive camera_rotation_quaternion consistently (Hamilton pair).
  R02 PASS (norm=1 by construction). R03 PASS if pos also re-derived.
  Video pixels: untouched — player saw the original yaw=90° turn.
expected: R26 FAIL on every sampled frame inside mutated region.
  Hamming distance ≈ 22-30 bits, well above threshold=8.

test_id: FI-B-01b-R26-coordinated-pos
description: Variant where attacker also coordinates pos to maintain R03.
expected: R26 FAIL identically (R26 is video-driven, immune to pos coord).

test_id: FI-VIDEO-RECODE-R26
description: |
  False-positive sentinel. Re-encode video.mp4 with different x265 CRF
  (no content change). Compare against table built on original.
expected: R26 PASS. Hamming ≤ 4 bits for re-encoding noise.
  If FAIL, threshold=8 needs raising (open question #1).
```

### 3.6 BFT consensus interaction

| Verifier | R26? | Rationale |
|---|---|---|
| **V₁ (Claude)** | YES (Phase A) | Re-derives from spec. Reference impl. |
| **V₂ (MiniMax/GLM)** | **DEFER to Phase B** | Ships V₁-only initially per W4-6 brief. After 30d clean shadow, dispatch V₂ re-impl per BFT § 4.1 (disjoint imports: V₁=imagehash, V₂=opencv DCT). |
| **V₃ (physics oracle)** | **ABSTAIN by design** | pHash distance not tabulatable on open Cartesian (yaw × pitch × game). V₃ stub: `{decision:"ABSTAIN", reason:"R26_not_in_oracle_table"}`. |

**Per-frame quorum during V₁-only.** V₁ PASS / V₂ ABSTAIN / V₃ ABSTAIN
⇒ PASS. **V₁ FAIL alone MUST NOT decree REJECT** — flag SUSPECT, route
to HUMAN_REVIEW per BFT § 6.4. R26 binds REJECT only after V₂ joins.
See open question #5.

---

## 4. Limitations (Honest Threat Model)

### 4.1 Per-game LUT required (NOT auto-derivable from physics)

R26 needs a per-game LUT. Building requires golden recordings from the
actual game running the actual rendering pipeline. **No way to derive
Minecraft's pHash at yaw=90° from first principles** — textures,
shaders, time-of-day, biome all matter. **v1 ships Minecraft-only.**
Each new game requires calibration (open question #2). Cross-game
silent reuse forbidden by IL13.

### 4.2 pHash fragility to encoder change (false-positive risk)

Switching x265 → x264, changing CRF, or changing `pix_fmt` shifts
pHash 3-7 bits on identical content. If producer pipeline upgrades
encoder without re-calibrating, R26 false-FAILs across the board.
Mitigation: pin encoder/CRF in `manifest.video_pipeline_id`; rebuild
table on pipeline change. See open question #6.

### 4.3 ffmpeg extract cost (~10s per dataset)

9000-frame datasets sampled every 30 frames: 300 ffmpeg invocations
× ~30ms = ~10s. Acceptable for shipping; borderline for shadow
back-fill. Optimization: single-pass `-vf select` filter extracts all
300 frames in one process (~2s). See open question #3.

### 4.4 Static-scene blind spot

If player stands still (yaw constant), all sampled frames have the
same expected hash — and so does an attacker mutating yaw to a
different constant value if the table has both entries. R26 catches
**transient yaw mutations during motion**. Static defers to V₄ on F4.

### 4.5 HUD-rendered input is the ONLY B-03 partial

B-03 mutates `keyCode 87 → 88`. R26 sees this only if game's HUD
renders the pressed key on-screen (e.g., tutorial overlay). Most
production gameplay does not. **R26 does NOT close B-03.** V₄
remains required.

### 4.6 White-box attacker who reads the table

Attacker with read access to `expected_hash_table_minecraft.json` can
compute *valid* yaw values matching mutated pixel content, then
choose mutations within those equivalence classes. v1 defense: keep
table out of producer's read path (`s3://oyster-bft/_lookups/` not
`s3://oyster-data/<run-id>/`); v2: per-dataset salt. Same class as
V₄ § 6.5 frame-selection-oracle leak.

---

## 5. Detection Lift Estimate

Per `RED_TEAM_TAXONOMY.md` § 6.2 wave-4 scoreboard:

```
wave-3 baseline (recorder-v0.24.0-wave3-73pct):  73%
+ V₄ deployed (B-01 + B-03 closed):              +13%  → 86%
+ R26 deployed (B-01 closed via pixel hash):     +7%   → 80%
                                                (alternative path)
```

R26 is **alternative**, not additive, to V₄ for B-01 coverage.
Combined deployment does NOT linearly stack — same attack class via
different mechanisms. Combined estimated lift: ~14% (V₄'s 13% +
marginal R26 contribution from HUD-bearing B-03 partials and
additional sampling density above V₄'s 5 frames).

**Strategic case:** if V₄ signing SLA slips into Phase B delays, R26
carries B-01 coverage at 7% absolute — significant fraction of V₄'s
value at materially less buyer overhead. If V₄ ships on schedule, R26
adds defense-in-depth and faster shadow-detection (no human in loop).

---

## 6. Operational Protocol

### 6.1 Building expected_hash_table (per game, on first onboarding)

`bin/build_expected_hash_table.py` (Phase A deliverable):

```
$ python3 bin/build_expected_hash_table.py \
    --golden-video samples/minecraft_golden_outdoor.mp4 \
    --golden-action samples/minecraft_golden_outdoor.action_camera.json \
    --game-id minecraft-1.20.4-vanilla \
    --scene-tag outdoor-daylight \
    --grid-resolution-deg 5.0 \
    --sample-period-sec 1.0 \
    --output expected_hash_table_minecraft.json
```

Output: JSON mapping grid-snapped (yaw, pitch) → pHash64. Built once;
signed by Howard (NOT an LLM, per A-26-04); shipped via
`s3://oyster-bft/_lookups/`.

### 6.2 Per-game vs per-scene granularity

**v1: per-game only** (`game_id` is the lookup key). Different scenes
within a game (cave vs outdoor) have different pHashes for same yaw,
raising false-FAIL rate but acceptable for v1: Minecraft outdoor
dominates; indoor/cave scenes filtered via existing scene-tag
metadata and excluded from R26 sampling. **v2: per-scene tables** if
v1 false-FAIL > 5% on shadow runs.

### 6.3 Auto-derivation via golden video + script

Yes — `build_expected_hash_table.py` (§ 6.1) is exactly that. Input:
producer-emitted golden recording captured by Howard or trusted
engineer in a controlled session. Inputs and yaw read from
`action_camera.json`; pixels from `video.mp4`; script populates table
by grid-snapping and storing the median pHash per (yaw, pitch)
bucket. Multiple golden recordings per game compose (median over
sources) to harden against single-recording encoding artifacts.

### 6.4 Table refresh cadence

Refresh required when **any** of: (1) game version bump (new
textures/shaders); (2) producer encoder pipeline change (new CRF,
pix_fmt); (3) sustained false-FAIL > 2% on shadow runs. Procedure:
re-run builder on fresh golden under new conditions; diff old vs new;
commit both with deprecation flag on old; verifier picks new table by
`manifest.video_pipeline_id` + `manifest.game_version`.

### 6.5 Failure modes

| Scenario | R26 vote | Decree | Response |
|---|---|---|---|
| Sampled frames all PASS | PASS | per BFT § 3.3 | ship |
| One sampled frame FAIL inside mutation region | FAIL | SUSPECT (V₁-only); REJECT (V₂ joined) | producer fixes / human review |
| ABSTAIN(game_id_mismatch) | ABSTAIN | per BFT § 8.1 | onboarding gap; build new table |
| ABSTAIN(ffmpeg_failed) | ABSTAIN | per IL10 | producer ships unreadable video — flag |
| Sustained R26 PASS but V₄ FAIL | (R26 false-negative) | V₄ governs | rebuild table; re-calibrate threshold |

---

## 7. ISC — Ideal State Criteria for R26

Each criterion binary (YES/NO ≤ 1 second).

### 7.1 Existence (V₁ Phase A)

- **[C-26-01]** `bin/verify_pinns_claude.py` contains callable
  `r26_video_content_hash` matching § 3.1. *Evidence:* AST grep returns 1.
- **[C-26-02]** `bin/build_expected_hash_table.py` exists with `--help`
  exposing all flags in § 6.1. *Evidence:* CLI smoke test.
- **[C-26-03]** `bin/audit_video_content_hash.py` enforces IL13.
  *Evidence:* CI step exits 0; synthetic cross-game test ABSTAINs.
- **[C-26-04]** `expected_hash_table_minecraft.json` exists in
  `s3://oyster-bft/_lookups/`, signed by Howard. *Evidence:* file
  exists, `metadata.builder_identity == "howard.li@berkeley.edu"`.

### 7.2 Coverage

- **[C-26-05]** Adversarial harness contains
  `tests/byzantine/test_FI_B_01_R26.py` AND
  `test_FI_B_01b_R26_coordinated_pos.py` per § 3.5. R26 disabled:
  BFT PASSes mutation; enabled (Phase B): BFT REJECTs.
- **[C-26-06]** False-positive sentinel
  `tests/sentinels/test_R26_recode_passes.py` per FI-VIDEO-RECODE-R26.
- **[C-26-07]** R26 ABSTAINs on the 8 cases in § 3.4. *Evidence:*
  `tests/residuals/test_R26_abstain.py` covers all 8.

### 7.3 Operational

- **[C-26-08]** Per-dataset R26 cost p95 < 15s. *Evidence:*
  `s3://oyster-bft/_metrics/r26_latency.jsonl`.
- **[C-26-09]** False-FAIL rate on canonical Minecraft recordings
  < 1%. *Evidence:* nightly shadow run dashboard.

### 7.4 Anti-criteria — must remain false

- **[A-26-01]** R26 never returns `passed=True` when video extraction
  fails. *Evidence:* `tests/residuals/test_R26_ffmpeg_failure_abstains.py`.
- **[A-26-02]** R26 never silently reuses one game's table for
  another. *Evidence:* IL13 audit + `tests/residuals/test_R26_cross_game_abstains.py`.
- **[A-26-03]** R26 V₁-only phase never decrees REJECT alone (must
  flag SUSPECT, route HUMAN_REVIEW). *Evidence:*
  `tests/orchestrator/test_R26_v1_only_no_reject.py`.
- **[A-26-04]** No `expected_hash_table_*.json` accepted whose
  `metadata.builder_identity` is an LLM identity. *Evidence:*
  git-blame audit + `bin/audit_video_content_hash.py` enforces
  builder allowlist (mirrors BFT § 4.2).

### 7.5 ISC Tracker

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
   re-encoding distance > 6, raise default to 12. Spec calibration
   job now, or "calibrate later"?

2. **Per-game LUT scope at v1 launch.** § 4.1 says Minecraft-only.
   What's the second game? Voxel/blocky games (Vintage Story,
   Boundless): same pHash works at similar thresholds. High-fidelity
   games (Cyberpunk, HL Alyx): pHash sensitivity may need re-tuning
   per game. **Most blocking question for implementation:** what
   shipping game roadmap should R26 plan for?

3. **ffmpeg extract optimization (one-pass vs N invocations).** § 4.3
   notes 10s → 2s via single `-vf select` filter. Worth implementing
   in v1, or ship N-invocations and optimize when binding? My rec:
   ship N-invocations (simpler, debuggable); optimize in v2 if
   Phase A profiling shows R26 dominating dataset latency.

4. **Function signature mismatch with R13 family.** W4-6 brief
   prescribes `(rec, neighbor, video_path, expected_hash_table)`,
   but R13–R16 use `(window, extras, config)`. R26's signature
   deviates. My rec: keep R26's signature in spec; document
   per-dataset state is bound by entrypoint via `functools.partial`;
   add `R26State` to residuals_types.py mirroring `R14State`.
   Acceptable, or harmonize fully?

5. **V₂ dispatch timing for Phase B.** § 3.6 defers V₂ to Phase B
   based on V₁ shadow stability. Trigger? My rec: 30 consecutive
   shipping datasets where V₁ R26 PASSes AND V₄ also PASSes (R26
   not contradicting V₄'s ground truth). Then dispatch V₂ re-impl.
   Sets a clear, automatable graduation. Acceptable?

6. **Encoder pinning in producer manifest.** § 4.2 + § 6.4: R26 needs
   producer to pin encoder/CRF in `manifest.video_pipeline_id`. Is
   producer currently emitting this? If not: blocks R26 (deeper
   change, producer-side spec). If yes: name the field; we wire it
   in immediately. Resolves with `cat samples/canonical_recording_03/manifest.json | jq '.video_pipeline_id'`.

---

## 9. Document Provenance

- Authored by Vera Sterling (Algorithm Agent), 2026-05-06, Stream W4-6.
- Source-of-truth: `docs/SPEC_R13_MULTIMODAL.md` (IL10, V₃ ABSTAIN,
  MultiModalExtras, residuals_types conventions);
  `docs/SPEC_V4_BUYER_SIGNED_PROTOCOL.md` (B-01/B-03 threat model,
  raw-RGB hash technique); `docs/RED_TEAM_TAXONOMY.md` (B-01 § 99-114,
  scoreboard § 6.2); `docs/ARCH_BFT_CONSENSUS.md` (V₁/V₂/V₃ roles,
  GREEN-ABSTAIN § 8.1, HUMAN_REVIEW § 6.4).
- Sibling specs NOT modified: V₄ buyer protocol stays authoritative
  for shipping-grade B-01 coverage; R24 (W4-2) and R25 (W4-4)
  reservations preserved in registry.
- Scope: R26 residual design, IL13 (per-game predictability), ISC.
  **Out of scope:** Python implementation, V₂ implementation
  (Phase B), per-scene LUT extension (v2), encoder-pinning
  producer-side spec, multi-game LUT roadmap beyond Minecraft.

*End of SPEC_R26_VIDEO_CONTENT_HASH.md.*
