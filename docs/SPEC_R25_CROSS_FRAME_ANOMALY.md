# SPEC_R25_CROSS_FRAME_ANOMALY — Cross-Frame Causal-Chain Anomaly Detector

> **Project:** oyster-agent-runner (game-data producer + BFT verifier mesh)
> **Author:** Vera Sterling (Algorithm Agent), commissioned by Howard Li
> **Date:** 2026-05-06
> **Stream:** W4-7 of 8-stream wave 4
> **Status:** Spec / ISC — design only, no Python yet
> **Predecessors:** `SPEC_R20_STATISTICAL_DRIFT.md` (dataset-level drift, sibling), `RED_TEAM_TAXONOMY.md` (Bucket B-01 motivation), `ARCH_BFT_CONSENSUS.md`, `SPEC_R13_MULTIMODAL.md` (IL10/IL11 ABSTAIN pattern).

---

## 0. TL;DR

Per-frame residuals (R01–R16) check internal consistency of one frame.
Dataset-level drift (R20) catches statistical drift across the whole
population. **Neither catches a Bucket B-class attacker who keeps each
frame internally self-consistent but breaks the *causal chain* between
frames.**

Concrete: an attacker mutates `camera_rotation_oula` AND
`camera_rotation_quaternion` together to a different but mutually
consistent rotation (R02 PASSes). They *forget* to also adjust
`mouse_dx`. Per-frame everything looks clean. **Cross-frame, the
correlation between Δyaw and Δmouse_dx collapses** — that is the R25a
signature.

| Sub-residual | Cross-frame relationship checked | Catches |
|---|---|---|
| **R25a** | `Pearson(Δyaw, Δmouse_dx)` over 30-frame window ≥ 0.5 | B-01 oula+quat decoupled from mouse |
| **R25b** | `(pos[n+1]−pos[n])·fps` matches `speed[n]` field (inverse of R03) | speed-field-only mutation |
| **R25c** | Held W key correlates with forward speed component | keyboard↔motion lying |
| **R25d** | `cx ≈ width/2`, `cy ≈ height/2` from systeminfo | intrinsics decoupled from window |

R25 returns `ResidualResult` (per-frame or per-window-anchor frame)
to keep its votes inside the existing BFT consensus stream.

---

## 1. Iron Law Extension — IL12

> **IL12 — Causal Honesty.** Sub-residuals operating on cross-frame
> *causal chains* MUST (a) declare a minimum window size and ABSTAIN
> below it, (b) ABSTAIN rather than FAIL when the *driver* signal has
> insufficient variance to compute a meaningful correlation
> (e.g. zero-input idle frames yield zero variance ⇒ Pearson undefined),
> (c) report `window=N` and the attempted statistic value in `note`,
> and (d) NEVER FAIL solely on a single-frame value — the unit of
> verdict is the *window*, not the frame.

**Rationale.** Honest gameplay frequently has zero-mouse-dx idle
moments where R25a's Pearson is mathematically undefined. Treating
those as FAIL would generate false positives during AFK or menu
screens. ABSTAIN gates the directionality. **Enforcement:**
`bin/audit_causal_honesty.py` checks every R25 entry point starts with
window-size + variance guard.

---

## 2. Type Reuse

R25 reuses the existing `ResidualResult` from `oyster_runner/residuals_types.py`
(NOT `DriftResult`). This is deliberate:

| Aspect | R20 (uses `DriftResult`) | R25 (uses `ResidualResult`) |
|---|---|---|
| Verdict scope | Whole dataset, one verdict | Per-frame stream, BFT-quorum-able |
| Invocation | Once after ingest | Per-frame in existing loop |
| Failure mode | SUSPECT (human review) | REJECT (consensus FAIL ≥ 2) |
| Statistic | `sample_stat` over 9k frames | `residual` over a 30-frame window |

**R25's `note`** carries the window size and statistic so reviewers can
audit, e.g. `note="R25a: corr=0.12 (window=30, |Δmouse|>1e-3 frames=24)"`.

---

## 3. Sub-Residuals

### 3.1 R25a — Yaw ↔ Mouse-dx Correlation

**Predicate.** Over a sliding 30-frame window ending at frame `n`,
compute `Δyaw[i]` (mod-360 unwrap) and `Δmouse_dx[i]` for
`i ∈ [n−30, n−1]`; Pearson `ρ = corr(Δyaw, Δmouse_dx)`.

```
PASS iff |ρ| ≥ 0.5     OR    var(Δmouse_dx) < ε_mouse  (ABSTAIN)
```

`ε_mouse = 1e-6` — zero-input idle ⇒ Pearson undefined ⇒ ABSTAIN per IL12.

```python
def r25a_yaw_mouse_correlation(
    records: list[dict],
    window: int = 30,
    min_corr: float = 0.5,
    min_mouse_var: float = 1e-6,
) -> ResidualResult: ...
```

- **Catches B-01.** Attacker rewrites `oula`+`quat` self-consistently
  (R02 PASS) but forgets `mouse_dx`. New yaw delta uncoupled from
  mouse ⇒ `ρ ≈ 0` ⇒ FAIL.
- One verdict per *window*. Frames `[0, window)` ABSTAIN.
- ABSTAIN if fields missing/malformed or window variance below `ε_mouse`.

### 3.2 R25b — Position ↔ Speed Inverse Causality

**Predicate.** R03 checks the *forward* direction: `pos[n+1] − pos[n]
≈ speed[n] / fps`. **R25b checks the *inverse*:** given the actual
position delta, the implied speed should match the recorded speed.

```
v_implied = ‖pos[n+1] − pos[n]‖ · fps
v_recorded = ‖speed[n]‖
relative_err = |v_implied − v_recorded| / max(v_recorded, ε_speed)
PASS iff relative_err ≤ tolerance     (ε_speed = 1e-3, idle guard)
```

```python
def r25b_speed_position_causality(
    records: list[dict],
    fps: float = 30.0,
    tolerance: float = 0.05,
    epsilon_speed: float = 1e-3,
) -> ResidualResult: ...
```

- **Distinct from R03.** R03 catches `speed mutated, pos clean`; R25b
  catches `pos mutated, speed clean` — the complementary attack.
- ABSTAIN if either field missing, `fps ≤ 0`, or `len(records) < 2`.

### 3.3 R25c — Keyboard ↔ Forward-Speed Causality

**Predicate.** When W (keyCode 87) is held, the camera should be
moving forward. Over a 30-frame window, compute:

- `w_held[i] ∈ {0,1}` from `inputs.jsonl` keyCode 87 state at frame `i`
- `v_forward[i] = camera_speed[i] · forward_axis(camera_rotation)`
  (project 3D speed onto camera-forward, derived from quat × `[0,0,1]`)
- Pearson `ρ = corr(w_held, v_forward)`

```
PASS iff |ρ| ≥ 0.5     OR    var(w_held) < ε_key       (ABSTAIN)
                       OR    var(v_forward) < ε_speed  (ABSTAIN)
```

`ε_key = 0.01` — needs at least some W-state change in window.

```python
def r25c_key_speed_causality(
    records: list[dict],
    window: int = 30,
    threshold: float = 0.5,
    inputs_jsonl_path: str | None = None,
) -> ResidualResult: ...
```

- **Catches** semantic keyboard-fakery: W in inputs and keyCode but
  camera doesn't actually move (R13 catches single-side; R25c catches
  the joint-but-motionless variant).
- ABSTAIN if `inputs.jsonl` missing or window variance below either ε.

### 3.4 R25d — Intrinsics ↔ Window Resolution Causality

**Predicate.** PRD requires `cx ≈ width/2`, `cy ≈ height/2`. `width`/
`height` come from `systeminfo.json` or `video.mp4` ffprobe.

```
err_cx = |cx − width/2| / width
err_cy = |cy − height/2| / height
PASS iff err_cx ≤ 0.02 AND err_cy ≤ 0.02     (2% tolerance)
```

```python
def r25d_intrinsics_resolution_causality(
    rec: dict,
    neighbor: dict | None = None,
    systeminfo_path: str | None = None,
    tolerance: float = 0.02,
) -> ResidualResult: ...
```

- `neighbor` unused at v1 (kept for R03/R04 signature symmetry).
- **Catches** asymmetric intrinsics mutation (centeredness twin of R08,
  which guards `fx == fy`).
- ABSTAIN if no resolution source, missing `cx`/`cy`, or `width`/
  `height` ≤ 0.

---

## 4. Decision Rule (per-frame integration)

R25 plugs into the existing per-frame BFT consensus:

```
verdicts_frame = [r01, r02, ..., r16, r25a, r25b, r25c, r25d]
# Existing consensus rule (≥ 2 verifiers FAIL → REJECT) applies unchanged.
```

R25a/R25c ABSTAIN on frames `[0, window)` and on zero-variance
windows; aggregation counts ABSTAIN frames separately.

---

## 5. What R25 Catches That R01..R23 Do NOT

**B-01 oula+quat coordinated swap.** R02 PASSes (designed-in
consistency). R14 (mouse_dx ↔ Δyaw, per RED_TEAM_TAXONOMY § 3.B-01)
is the canonical defender, but is currently spec-only — **R25a is a
strict superset** (R14 = per-frame delta check; R25a = windowed
Pearson, robust to noise). R25a catches B-01 with high confidence.

**Sloppy-attacker class.** R25 closes the general failure mode where
an attacker mutates one field correctly but forgets that it has
*causal partners* in adjacent frames. Per-frame residuals can't see
causal partners by definition; R20 sees only aggregate distribution.
R25 is the missing middle: **windowed correlations between fields
that should co-vary**.

| Residual layer | Scope | Catches |
|---|---|---|
| R01–R12 | Per-frame single-modal | Type/range/internal consistency |
| R13–R16 | Per-frame cross-modal | Modality disagreements |
| R17 (proposed) | Per-frame video-pixel | Optical flow ↔ speed (B-02) |
| R18 (proposed) | Per-dataset manifest | Frankenstein detection (B-05) |
| **R20** | **Per-dataset statistical** | **Distribution drift (C)** |
| R21–R23 | Per-dataset structural | Frame reorder, codec, depth shuffle (D) |
| **R25** | **Per-window causal** | **Cross-frame causal-chain breaks (B partial)** |

R25 occupies a coordinate (per-window × causal) no other residual covers.

---

## 6. What R25 Does NOT Catch

1. **Coordinated multi-field attack with causal partners falsified.**
   If the attacker mutates oula+quat AND `mouse_dx` AND `camera_speed`
   AND `inputs.jsonl` *all coherently*, R25a/R25b/R25c all PASS. This
   is **V₄ buyer-reference scope** (per `BFT_TRUST_REPORT_FOR_BUYER.md`
   § 8) and an acknowledged limit of the L1–L3 mesh.
2. **Single-frame anomalies.** R25 needs ≥ 30 frames of context for
   R25a/R25c. Single-frame attacks are R01–R12 territory.
3. **Distribution drift below correlation noise floor.** If the
   attacker introduces a tiny (sub-noise) decorrelation, R25 ABSTAINs
   or passes. R20 (statistical drift) is the right defender.
4. **Replay/temporal attacks.** R25 assumes monotonic time — frame
   reordering breaks the windowed assumption silently. R21 (proposed
   monotonic frame index) must run before R25 in the pipeline.
5. **R25d alone cannot detect intrinsics that lie about a real physical
   camera setup** if `systeminfo.json` is also tampered. Cross-checks
   to `video.mp4` ffprobe `width`/`height` (deferred to R25d v2)
   would close that.

---

## 7. Detection Lift Estimate

Baseline: **73 %** (post-R13/R14/R15/R16/R20). Bucket B is hardest.

| Sub-residual | Catches | Lift |
|---|---|---|
| R25a | B-01 (oula+quat self-consistent, mouse decoupled) | **+7 %** |
| R25b | speed-field-only mutation (R03 inverse) | +2 % |
| R25c | keyboard-only mutation, motion clean | +2 % |
| R25d | asymmetric intrinsics centeredness | +1 % |
| **R25 combined** | union (mostly disjoint) | **≈ +12 %** |

**Projected detection after R25: ≈ 85 %.** Calibration on ≥ 10 honest
datasets (Open Q1) required to ratify.

---

## 8. Threshold Calibration

Defaults (`min_corr=0.5`, `tolerance=0.05`, `min_mouse_var=1e-6`) are
author-estimated. Empirical plan:

1. **Collect 10 honest reference datasets** (buyer + open-source:
   Minecraft idle/active, Roblox, FPS, etc.).
2. For each, compute empirical distributions of R25a/b/c/d statistics.
3. **Set thresholds at the 99.5-th percentile of honest distributions**
   (1-in-200 false-positive frame budget on honest data).
4. **Sentinel test:** for each B-bucket attacker, assert R25 = FAIL on
   attacked + PASS on matched honest control.

Per-game profiles may be needed (cf. R20d's per-game `V_max`). **Open Q4.**

---

## 9. ABSTAIN Gates per IL12

Every R25 sub-residual short-circuits to
`ResidualResult(passed=False, residual=math.inf, threshold=…, note="ABSTAIN:<reason>")`
when:

| Condition | reason |
|---|---|
| `records` empty or `len < window` | `ABSTAIN:insufficient_window(N={N}<{w})` |
| Required field missing | `ABSTAIN:missing_field({field})` |
| Wrong shape | `ABSTAIN:malformed_field({field})` |
| Window variance below `ε` (R25a/R25c) | `ABSTAIN:zero_variance({signal})` |
| `fps ≤ 0` (R25b) | `ABSTAIN:invalid_fps` |
| `systeminfo.json` missing AND no inline `width`/`height` (R25d) | `ABSTAIN:missing_resolution` |
| NaN in correlation | `ABSTAIN:nan_in_corr` |

ABSTAIN frames do not count toward FAIL bucket in BFT consensus.

---

## 10. BFT Independence

Same trade-off as R20 § 6: V₁-only first; once thresholds stabilize on
≥ 10 honest baselines, dispatch parallel V₂/V₂' against the
`ResidualResult` contract. Diversity matters *most* here — rolling-
window code is notoriously prone to off-by-one and edge-handling
divergences (a real BFT-diversity payoff). **Recommendation:** V₁-only
first (matches R20 phasing).

---

## 11. ISC Tracker — Phase PLAN

```
┌─ ISC ─────────────────────────────────────────────┐
│ Phase: PLAN (R25 spec)                            │
│ Criteria: 0 → 13   Anti: 0 → 5                    │
├───────────────────────────────────────────────────┤
│ + [C1]  R25a Pearson(Δyaw, Δmouse_dx) ≥ 0.5      │
│ + [C2]  R25b inverse-kinematic check defined      │
│ + [C3]  R25c keyboard↔forward-speed causality     │
│ + [C4]  R25d cx/cy ≈ width/2, height/2            │
│ + [C5]  IL12 causal honesty iron law              │
│ + [C6]  ResidualResult reuse (NOT DriftResult)    │
│ + [C7]  Window size + variance ABSTAIN gates      │
│ + [C8]  Per-frame BFT consensus integration       │
│ + [C9]  +12% absolute lift estimate (≈ 85% total) │
│ + [C10] Calibration plan (10 honest datasets)     │
│ + [C11] B-01 catch path documented (R25a)         │
│ + [C12] Coverage matrix vs R01..R23 documented    │
│ + [C13] V₁-only first; V₂/V₂' planned             │
│ + [A1]  Must NOT FAIL on insufficient variance    │
│ + [A2]  Must NOT FAIL single-frame (window-based) │
│ + [A3]  Must NOT crash on missing inputs.jsonl    │
│ + [A4]  Must NOT silently PASS on missing fields  │
│ + [A5]  Must NOT subsume R20 / R14 cleanup        │
└───────────────────────────────────────────────────┘
```

---

## 12. Open Questions for Howard

1. **Calibration source.** R25's `min_corr=0.5`, `tolerance=0.05`,
   `min_mouse_var=1e-6` are estimates. Should we require ≥ 10
   buyer-supplied honest datasets baselined before R25 lands in CI,
   or ship with current values and tighten in v2 (matching R20's
   Open Q1 phasing)?

2. **Sliding-window aggregation.** Each R25a/R25c verdict covers a
   30-frame window. For a 9000-frame dataset, that's 8970 overlapping
   windows. Should we (a) emit per-window verdicts (one per anchor
   frame, expensive — 8970 BFT votes), (b) emit per-non-overlapping-
   window verdicts (300 votes), or (c) emit one per-dataset
   *aggregate* verdict (1 vote, more like R20)? Current spec implies
   (a); recommend (b) for cost.

3. **R14 vs R25a redundancy.** R14 (mouse_dx ↔ Δyaw, per
   RED_TEAM_TAXONOMY § 3.B-01) is currently spec-only. R25a is a
   strict superset (windowed Pearson > per-frame delta check). Do we
   (a) ship R25a and retire R14 from the roadmap, (b) ship both
   (defense in depth), or (c) re-spec R14 as the single-frame fast
   path (cheap pre-filter) and R25a as the windowed deep check?

4. **Per-game profiles.** R25c assumes WASD↔forward-axis mapping
   (FPS / Minecraft convention). Racing games (W = throttle, motion
   forward but forward-axis differs) and flight sims (W = pitch-down,
   not translation) break R25c entirely. Should `manifest.json` carry
   a `game_class` (matching R20 Open Q4) that selects an R25c profile,
   or do we leave R25c off until per-game motion specs are written?

5. **Forward-axis derivation.** R25c needs to project `camera_speed`
   onto camera-forward. PRD specifies left-handed (right=x, up=y,
   front=z), so forward = quat-rotation of `[0,0,1]`. Confirm that
   convention before implementation — a sign error here makes R25c
   FAIL on every honest dataset.

---

## 13. Document Provenance

- Authored by Vera Sterling (Algorithm Agent), 2026-05-06, Stream W4-7.
- Source-of-truth: `SPEC_R20_STATISTICAL_DRIFT.md` (IL10/IL11 ABSTAIN
  pattern), `RED_TEAM_TAXONOMY.md` § 3.B-01 (motivating attack),
  `ARCH_BFT_CONSENSUS.md` (V₁/V₂/V₂'/V₃ + PBFT contract),
  `bin/v1_claude_residuals/residuals.py` (`ResidualResult` shape).
- Sibling specs NOT modified: `bin/verify_*.py`, the harness, other
  wave-4 streams.
- **Out of scope:** Python implementation, V₂/V₂' ports, per-game
  profile catalog, R14 retirement decision (Open Q3).

*End of SPEC_R25_CROSS_FRAME_ANOMALY.md.*
