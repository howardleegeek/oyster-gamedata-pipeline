# SPEC_R20_STATISTICAL_DRIFT — Distribution-Level Drift Detector

> **Project:** oyster-agent-runner (game-data producer + BFT verifier mesh)
> **Author:** Vera Sterling (Algorithm Agent), commissioned by Howard Li
> **Date:** 2026-05-06
> **Stream:** 4 of 5 parallel push (siblings: A=R17, B=R18, C=R19, E=R21)
> **Status:** Spec / ISC — design only, no Python yet
> **Predecessors:** `ARCH_BFT_CONSENSUS.md`, `ARCH_PINNS_BUYER_SPEC.md`, `RED_TEAM_TAXONOMY.md` (Bucket C), `SPEC_R13_MULTIMODAL.md` (IL10 ABSTAIN pattern).

---

## 0. TL;DR

Red Team scorecard: **Bucket C catches 2/5 (40%)** of sub-threshold attacks under BFT N=4. Single-frame R01–R16 are blind to **distribution-level drift**: each frame passes its threshold, but the aggregate dataset has drifted (e.g., C-01 `‖q‖ = 1 + 5e-7` per frame slips R01's `1e-6`, but `mean(‖q‖) − 1.0` over 9k frames is detectable).

**R20 runs once per dataset**, computes aggregate stats over all records, compares against expected distributions.

| Sub-residual | Aggregate stat | Catches |
|---|---|---|
| **R20a** | `‖q‖` distribution: mean, std | C-01 quat-norm drift |
| **R20b** | `Σ mouse_dx` vs endpoint integral | C-02 mouse-dx drift |
| **R20c** | `dt` jitter: mean, std | C-03 fps boundary |
| **R20d** | speed magnitude profile | C-05 sub-50 m/s absurdity |
| **R20e** | yaw turn rate ratio | impossible-turn aggregates |

R20 does not REJECT — FAIL flags **SUSPECT** for human review.

---

## 1. Iron Law Extension — IL11

> **IL11 — Distribution Honesty.** Sub-residuals operating on aggregated
> dataset statistics MUST (a) declare a minimum sample size and ABSTAIN
> below it, (b) return `passed=False, detail="ABSTAIN:<reason>"` rather
> than crash on degenerate inputs (all-NaN, empty, single-frame), and
> (c) report `sample_size` and `n_outliers` so reviewers can audit the
> population. Verdict logic MUST NOT short-circuit on the first failing
> record — every sub-residual operates over the full population.

**Rationale.** One corrupt record must not cause REJECT at dataset level
(false positive); a stat over 3 frames is not a stat. ABSTAIN gates protect
both directions. **Enforcement:** `bin/audit_drift_honesty.py` checks every
entry point starts with the sample-size guard and that exceptions route to
the same ABSTAIN return.

---

## 2. Type Extensions

```python
# oyster_runner/residuals_types.py — TYPES ONLY
from dataclasses import dataclass

@dataclass(frozen=True)
class DriftResult:
    """Per-dataset verdict. Sibling of ResidualResult, returned once
    per dataset rather than once per frame."""
    name: str                # "R20a", "R20b", "R20c", "R20d", "R20e"
    passed: bool             # True = no drift detected
    sample_stat: float       # primary statistic (mean, std, max, ratio…)
    threshold: float         # numeric threshold the stat is compared against
    n_outliers: int          # count of records contributing to FAIL
    sample_size: int         # total records considered (after ABSTAIN gate)
    detail: str              # human-readable, "ABSTAIN:<reason>" if abstain
```

`DriftResult` is **not** `ResidualResult`. It is reported separately
in the dataset-level audit JSON, not the per-frame residuals stream.

---

## 3. Sub-Residuals

### 3.1 R20a — Quaternion Norm Distribution

**Predicate.** For every record's `camera_rotation_quaternion = (x,y,z,w)`,
compute `‖q‖ = sqrt(x²+y²+z²+w²)`. Across the whole dataset:

```
μ_q = mean(‖q‖_n for n in records)
σ_q = stdev(‖q‖_n for n in records)
PASS  iff   |μ_q − 1.0| ≤ 1e-5   AND   σ_q ≤ 1e-4
```

**Function signature.**

```python
def r20a_quat_norm_distribution(
    records: list[dict],
    max_std: float = 1e-4,
    max_offset: float = 1e-5,
    min_frames: int = 10,
) -> DriftResult: ...
```

- **Catches C-01** — R01's per-frame `1e-6` slips, but `μ_q − 1.0 ≈ 5e-7`
  exceeds `1e-5`? No — but the constant offset over 9k frames forces
  `μ_q ≈ 1 + 5e-7` and the test on `\|μ_q − 1\|` requires retuning to `5e-6`
  for full catch. **Open Q1** addresses calibration.
- ABSTAIN if `N < 10`, missing field, or wrong length.
- `n_outliers` = count of records `> 3σ` from `μ_q` (informational).

### 3.2 R20b — Mouse-dx Cumulative Integral

**Predicate.** Over the whole stream, `mouse_dx[n]` is supposed to be
the per-frame delta of `mouse_x[n]`. Therefore `Σ mouse_dx ≈
mouse_x[N−1] − mouse_x[0]` (modulo recorder roundoff). Any persistent
sub-threshold drift in `mouse_dx` accumulates and breaks this identity.

```
S = Σ_{n=0..N-1} mouse_dx[n][0]
ΔX = mouse_x[N-1][0] − mouse_x[0][0]
drift_residual = |S − ΔX|
PASS iff drift_residual ≤ 1e-3
```

**Function signature.**

```python
def r20b_mouse_dx_cumulative(
    records: list[dict],
    tolerance: float = 1e-3,
    min_frames: int = 10,
) -> DriftResult: ...
```

- **Catches C-02** (mouse_dx + 5e-7 per frame). After 9 000 frames,
  `S − ΔX ≈ 9000 · 5e-7 = 4.5e-3` >> 1e-3 → FAIL.
- ABSTAIN if `mouse_x` or `mouse_dx` arrays missing or wrong shape.
- ABSTAIN if `N < min_frames` (drift is sub-detectable).
- Use `mouse_dx[n][0]` (first axis) consistent with FrameDict shape.

### 3.3 R20c — fps / dt Jitter

**Predicate.** Records carry `time` (ISO timestamp string). Compute
`dt[n] = t[n+1] − t[n]` for `n = 0..N−2`. Honest 30 fps capture has
`mean(dt) ≈ 33.333 ms` with `std(dt) < 0.5 ms`.

```
μ_dt = mean(dt)
σ_dt = stdev(dt)
target_dt = 1.0 / declared_fps  (taken from records[0].fps; fallback 30.0)
PASS iff   |μ_dt − target_dt| ≤ 1e-4 s   AND   σ_dt ≤ 5e-3 s
```

**Function signature.**

```python
def r20c_fps_jitter(
    records: list[dict],
    max_std_ms: float = 5.0,
    max_offset_ms: float = 0.1,
    min_frames: int = 10,
) -> DriftResult: ...
```

- **Catches C-03** (`fps = 29.51`). With declared `fps = 30`,
  `target_dt = 33.333 ms` but `μ_dt ≈ 33.886 ms` → `|μ_dt − target| ≈
  0.55 ms` >> 0.1 ms → FAIL.
- Pairs with R15 (`fps` field vs `ffprobe`) to triangulate frame-rate
  honesty across modalities.
- ABSTAIN on unparseable timestamps, non-monotone time, or `N < min_frames`.

### 3.4 R20d — Speed Magnitude Profile

**Predicate.** Build histogram of `‖speed‖_n = sqrt(vx² + vy² + vz²)`
over the population. Honest PRD games (Minecraft, Roblox-class platformers)
operate in the walk-run band `[0.5, 8] m/s` for the bulk of frames.

```
n_high  = count(records where ‖speed‖ > 30 m/s)
ratio_high = n_high / N
μ_speed = mean(‖speed‖)
PASS iff   ratio_high ≤ 0.10   AND   μ_speed ≤ 15.0 m/s
```

**Function signature.**

```python
def r20d_speed_profile(
    records: list[dict],
    max_outlier_pct: float = 0.10,
    max_mean_speed: float = 15.0,
    high_speed_threshold: float = 30.0,
    min_frames: int = 10,
) -> DriftResult: ...
```

- **Catches C-05** (constant 49.99 m/s). Per-frame R10 has `V_max = 50` so
  every frame PASSes. Aggregate `μ_speed = 49.99 m/s` and `ratio_high = 1.0`
  → both conditions FAIL.
- Game-aware fallback: if `manifest.json.game_id` declares a custom
  `V_max_human`, override `high_speed_threshold` per-game.
- ABSTAIN if `camera_speed` or `player_speed` arrays missing or wrong shape.
- Choose `camera_speed` for canonical reading (player-induced motion).

### 3.5 R20e — Yaw Turn Rate

**Predicate.** Compute `Δyaw[n] = yaw[n+1] − yaw[n]` (modulo 360°
wrap-around). Convert to rate `r_n = |Δyaw[n]| / dt[n]` deg/s. Real
human gameplay has bounded turn rates; mouse-look caps at roughly
360°–720°/s on aggressive sensitivity, almost never sustained.

```
n_extreme = count(r_n > 720 deg/s)
ratio_extreme = n_extreme / (N-1)
PASS iff ratio_extreme ≤ 0.05
```

**Function signature.**

```python
def r20e_yaw_turn_rate(
    records: list[dict],
    max_rate_deg_per_sec: float = 720.0,
    max_outlier_pct: float = 0.05,
    min_frames: int = 10,
) -> DriftResult: ...
```

- Catches **synthetic data** with quantized look-deltas (RL replay,
  scripted bot turns) where Δyaw exceeds human reflex repeatedly.
- Yaw extracted from `camera_rotation_oula[1]` (Y-Euler, per FrameDict).
- ABSTAIN on missing yaw, non-monotone time, or `N < min_frames`.

---

## 4. Decision Rule

```
verdicts = [r20a, r20b, r20c, r20d, r20e]
abstained = [v for v in verdicts if v.detail.startswith("ABSTAIN")]
failed    = [v for v in verdicts if not v.passed and v not in abstained]

if len(abstained) == len(verdicts): dataset_status = "ABSTAIN"
elif len(failed) >= 1:               dataset_status = "SUSPECT — HUMAN REVIEW"
else:                                dataset_status = "PASS"
```

**Key contract.** `SUSPECT` is **not** `REJECT`. R20 flags the dataset
into a human-review queue; the per-frame BFT verdict still stands.
This protects against false positives from rare gameplay patterns
(speedrun, modded movement) while ensuring no Bucket-C attack ships
silently endorsed.

---

## 5. Bucket C Coverage Analysis

| Attack | Mechanism | R20 catch path | Single-frame? | Estimated detection |
|---|---|---|---|---|
| **C-01** | ‖q‖ = 1 + 5e-7 | R20a `\|μ_q − 1.0\|` > 1e-5 over 9k frames | NO | **YES** ≈100% |
| **C-02** | mouse_dx + 5e-7 | R20b `\|S − ΔX\|` > 1e-3 over 9k frames | NO | **YES** ≈100% |
| **C-03** | fps = 29.51 | R20c `\|μ_dt − target\|` > 0.1 ms | NO (boundary) | **YES** ≈95% |
| **C-04** | pitch = 180.001° | (boundary; not R20 territory — covered by spec lock) | NO | n/a (spec fix) |
| **C-05** | speed = 49.99 m/s | R20d `μ_speed > 15` and `ratio_high > 0.10` | NO | **YES** ≈100% |

**Bucket C uplift:** 2/5 → 5/5 caught. (C-04 already handled by R12 boundary
spec lock per RED_TEAM_TAXONOMY.md item 3.) **Net gain: 3/5 catches in Bucket C,
raising overall scorecard from 27% → ~42%** (60% relative gain on structured
attacks). Combined with R19 (sibling) coverage of C-01 single-frame guard, the
mesh becomes redundant for distribution-level attacks (defense in depth).

---

## 6. BFT Independence

R20 as written is **V₁-only (Claude)**. For BFT consensus on distribution-
level checks, V₂ (MiniMax) and V₂' (GLM) need their own R20 implementations.

| Approach | BFT | Cost | Risk |
|---|---|---|---|
| V₁-only | No vote | 1 dispatch | Single-implementer threshold bias |
| V₁ + V₂ | 2-of-2 unanimity | 2 dispatches | ≈$0.01/dataset MiniMax tokens |
| V₁ + V₂ + V₂' | 2-of-3 quorum | 3 dispatches | Full BFT diversity |

**Recommendation:** land V₁-only first; once thresholds stabilize on ≥10
honest baselines, dispatch parallel V₂/V₂' against the same `DriftResult`
contract. Diversity matters most for thresholds (empirical, prone to
single-implementer overfitting).

---

## 7. ABSTAIN Gates per IL11

Every sub-residual short-circuits to `DriftResult(passed=False,
detail="ABSTAIN:<reason>", sample_size=0, ...)` when:

| Condition | reason |
|---|---|
| `records` empty | `ABSTAIN:empty_records` |
| `N < min_frames` | `ABSTAIN:insufficient_sample(N={N}<{min})` |
| Required field missing | `ABSTAIN:missing_field({field})` |
| Wrong shape (length, type) | `ABSTAIN:malformed_field({field})` |
| Non-monotone timestamps (R20c, R20e) | `ABSTAIN:non_monotone_time` |
| NaN in stat | `ABSTAIN:nan_in_stat` |

ABSTAIN ≠ PASS. All-ABSTAIN → dataset `ABSTAIN`; partial-ABSTAIN uses
only non-abstained sub-residuals for `SUSPECT` determination.

---

## 8. Adversarial Harness Extensions

`bin/red_team/blue_team_score.py` invokes residuals **per-frame** via
`_baseline_frame()` and `fn(frame_dict)`. R20 is **per-dataset** — needs
a different invocation pattern.

**Required additions:**

```python
# blue_team_score.py
def _baseline_dataset(n_frames: int = 9000) -> list[dict]:
    """9000 frames = 5 min at 30 fps. Walk forward 0.5 m/s, yaw 0.1°/frame."""
    ...

def _vote_v1_dataset(fn, records) -> bool:
    try:
        r = fn(records)
        return (not r.passed) and (not r.detail.startswith("ABSTAIN"))
    except Exception:
        return True
```

**Catalog extension:** `attackers.AttackResult` gains
`dataset_mutator: Callable[[list[dict]], list[dict]] | None`. C-01..C-05
attackers already have per-frame mutators; need parallel dataset-level
mutators applying the same perturbation across all 9 000 frames.

**Scorecard:** `BlueScore` adds `r20_caught: bool` and
`r20_status: Literal["PASS", "SUSPECT", "ABSTAIN"]`. Final tally row
gets a `Bucket C w/ R20` column.

---

## 9. ISC Tracker — Phase PLAN

```
┌─ ISC ─────────────────────────────────────────────┐
│ Phase: PLAN (R20 spec)                            │
│ Criteria: 0 → 14   Anti: 0 → 4                    │
├───────────────────────────────────────────────────┤
│ + [C1-C5]  R20a..R20e thresholds defined          │
│ + [C6]     IL11 distribution honesty              │
│ + [C7]     DriftResult dataclass                  │
│ + [C8]     ABSTAIN gates per sub-residual         │
│ + [C9]     Decision rule (PASS/SUSPECT/ABSTAIN)   │
│ + [C10]    Bucket-C coverage quantified           │
│ + [C11]    BFT independence trade-off             │
│ + [C12]    Harness dataset invocation pattern     │
│ + [C13]    AttackResult.dataset_mutator field     │
│ + [C14]    BlueScore.r20_caught reporting         │
│ + [A1]     R20 must NOT REJECT — only flag        │
│ + [A2]     Must NOT short-circuit on first FAIL   │
│ + [A3]     Must NOT crash on empty records        │
│ + [A4]     Must NOT silently PASS on missing fld  │
└───────────────────────────────────────────────────┘
```

---

## 10. Open Questions for Howard

1. **Threshold calibration source.** R20a..R20e thresholds are
   author-estimated for honest 30-fps PRD-style gameplay. Should we
   require ≥3 buyer-supplied honest datasets baselined before R20 lands
   in CI, or ship with current values and tighten in v2?

2. **SUSPECT vs REJECT.** Spec calls SUSPECT a soft flag for human
   review. Does the buyer SLA support this, or do they need binary
   PASS/REJECT only? If binary, R20 needs a quorum rule (e.g.
   ≥3-of-5 sub-residuals fail → REJECT).

3. **BFT scope.** Land V₁-only first vs dispatch V₂ + V₂' R20 in
   parallel? Latter triples spec/dispatch cost but gives true BFT
   independence on distribution-level checks. Recommendation §6 leans
   V₁-only first; confirm.

4. **Per-game V_max overrides.** R20d's `high_speed_threshold = 30 m/s`
   assumes Minecraft-class movement. For racing-game data (Forza, GT)
   this would falsely SUSPECT every dataset. Should `manifest.json`
   carry a `game_class` (`platformer`, `racing`, `flight-sim`, …) that
   selects an R20d profile, or do we leave R20d off until per-game
   V_max specs are written?

5. **Window vs full-dataset.** Should R20 also offer rolling-window
   variants (e.g. 1000-frame windows) so very long datasets can locate
   *where* the drift happens rather than only saying "this 60-min
   dataset has drift somewhere"? This adds N/window verdicts at modest
   cost; may be valuable for forensics. Defer to R20.1?
