# SPEC_R13_MULTIMODAL — Multi-Modal Residuals R13–R16

> **Project:** oyster-agent-runner (game-data producer + BFT verifier mesh)
> **Author:** Vera Sterling (Algorithm Agent), commissioned by Howard Li
> **Date:** 2026-05-05
> **Stream:** D of 5-way parallel push (parallel siblings A/B/C/E)
> **Status:** Spec / ISC — design only, no Python yet
> **Predecessors:** `ARCH_BFT_CONSENSUS.md` (V₁/V₂/V₃ roles, PBFT, ABSTAIN semantics), `ARCH_PINNS_BUYER_SPEC.md` (FrameDict, FrameWindow, ResidualResult, buyer-spec YAML), `PRD_FORMULAS.md` (§ B13 VK codes, criteria #3 & #5).

---

## 0. TL;DR — Why R13–R16 Exist

R01–R12 read only `action_camera.json` and check single-modal
consistency or closed-form physics. **FI-02 broke this:** mutation
`keyCode 87 (W) → 88 (B)` left `action_camera.json` internally
consistent — both 87 and 88 are valid VK codes — so R09's predicate
`code ∈ VK_TABLE` PASSed on all three verifiers {V₁ Claude, V₂
MiniMax, V₃ physics-table}. The screen showed W; the recorded keyCode
was B. **PRD criterion #5 was violated; the BFT mesh endorsed it 3-of-3.**

Architectural gap, not verifier bug: any residual reading only
`action_camera.json` cannot detect a mutation that leaves that file
self-consistent but disagrees with another modality (input events,
video pixels, depth files). R13–R16 close the gap by reading at least
two producer artifacts each. All four use artifacts the producer
already emits — **no new producer responsibilities**.

| Residual | Modalities | PRD criterion | New coverage |
|---|---|---|---|
| **R13** | `keyCode` ⊕ `inputs.jsonl` | #5 | W→B substitution, key-frame skew, dropped events |
| **R14** | `mouse_dx` ⊕ Δ(`yaw`) | #3 | Mouse-camera decoupling (sign flip, zeroed input) |
| **R15** | `fps` field ⊕ `ffprobe(video.mp4)` | frame-rate honesty | fps field lying about real video |
| **R16** | `count(depth/*.exr)` ⊕ `ffprobe(video).duration` | "每秒 6 张均匀抽帧" | Depth dropout, over-emission, time misalignment |

---

## 1. Iron Law Extension — IL10

> **IL10 — Producer-Artifact Honesty.** Any residual that depends on a
> producer-side artifact other than `action_camera.json` (e.g.,
> `inputs.jsonl`, `video.mp4`, `depth/*.exr`) MUST declare an explicit
> ABSTAIN behavior when the artifact is absent, unreadable, or
> schema-invalid. The residual MUST NOT silently return PASS in those
> cases and MUST NOT throw an uncaught exception. The producer MUST
> emit a manifest entry for each such artifact in
> `manifest.json.artifacts[]` with `sha256` and `size_bytes`.

**Rationale.** Silent PASS on a missing artifact is the worst failure
mode — producer ships incomplete data under apparent BFT endorsement.
ABSTAIN is the only honest verdict.

**Enforcement.** CI step `bin/audit_artifact_honesty.py` parses every
new `bin/verify_*_R1[3-6]*.py`, locates `open(`, `ffprobe` subprocess,
`cv2.VideoCapture(`, `glob.glob(`, and verifies each is wrapped in a
try/except mapping to `ResidualResult(passed=False,
detail="ABSTAIN:<reason>")`. IL10 violations auto-rejected.

---

## 2. Shared Type Extensions

R13–R16 require types not in `oyster_runner/residuals_types.py`. Lands
in residuals_types PR, not here.

```python
# oyster_runner/residuals_types.py — TYPES ONLY, no math
from typing import TypedDict, Literal
from dataclasses import dataclass

class InputEvent(TypedDict):
    """One row of inputs.jsonl from recorder_consumer_lite.py."""
    ts_ns: int                      # pynput monotonic ns timestamp
    kind: Literal["key_down", "key_up", "mouse_move", "mouse_button", "session_start"]
    code: int | None                # VK code for keys, button id for mouse
    dx: float | None                # mouse delta px (None for keys)
    dy: float | None
    raw: dict                       # passthrough for forensics

class MultiModalExtras(TypedDict, total=False):
    """Additional inputs to multi-modal residuals beyond FrameWindow.
    Loaded once per dataset, not per frame."""
    inputs_log_path: str
    inputs_log_status: Literal["ok", "missing", "corrupt", "schema_mismatch"]
    inputs_events: list[InputEvent]
    video_path: str
    video_probe: "VideoProbe | None"
    video_status: Literal["ok", "missing", "ffprobe_failed", "schema_mismatch"]
    depth_dir: str
    depth_files: list[str]          # sorted by parsed timestamp
    depth_status: Literal["ok", "missing", "no_files", "filename_unparseable"]

@dataclass(frozen=True)
class VideoProbe:
    fps_avg: float                  # avg_frame_rate as float
    fps_r: float                    # r_frame_rate (raw)
    duration_sec: float
    n_frames: int
    width: int
    height: int
    codec: str
    raw_json: dict                  # full ffprobe -of json output
```

**Convention:** `MultiModalExtras` computed once per dataset by the
verifier entrypoint; passed to each multi-modal residual alongside
`FrameWindow`. Amortizes ffprobe (one call/dataset) and inputs.jsonl
parsing (one O(N) read).

R14 additionally needs an accumulator. The residual ABI is extended
with optional `aux_state`; existing R01–R12 pass `None`.

```python
@dataclass
class R14State:
    window_size: int
    mouse_dx_buf: "collections.deque[float]"
    yaw_buf: "collections.deque[float]"
    last_yaw: float | None = None
```

---

## 3. R13 — keyCode-vs-Input-Replay

### 3.1 Function signature

```python
def r13_keycode_replay(
    window: FrameWindow,
    extras: MultiModalExtras,
    config: dict,
) -> ResidualResult:
    """Verify frame.keyCode equals VK codes inferred from inputs.jsonl
    events whose timestamp falls within frame's [t_start, t_end] interval,
    with ±1 frame skew tolerance.
    config keys: skew_frames (int, default 1),
                 key_set_equality ("exact" | "subset_ok", default "exact").
    Returns ResidualResult(id="R13_keycode_replay", ...).
    """
```

### 3.2 Frame-time alignment

```
  action_camera.json frame i, fps=30 ─┐    inputs.jsonl events
                                      │   (pynput monotonic ns)
                                      ▼
              t_start_i = i / fps
              t_end_i   = (i+1) / fps
              t_lo      = t_start_i − skew/fps
              t_hi      = t_end_i   + skew/fps
                                      │
                                      ▼
              ┌─────────────────────────────────┐
              │ events e where t_lo ≤           │
              │ (e.ts_ns − t0_ns)/1e9 ≤ t_hi    │
              └────────────┬────────────────────┘
                           ▼
              ┌─────────────────────────────────┐
              │ replay state machine:           │
              │   keys_held = {}                │
              │   key_down code: keys_held∪={c} │
              │   key_up code:   keys_held−={c} │
              │ snapshot at t_end_i             │
              └────────────┬────────────────────┘
                           ▼
              keys_held_i  ==  set(curr.keyCode)  ?  PASS  :  FAIL
```

**Time-zero anchor.** `inputs.jsonl` is monotonic-ns from pynput.
Producer emits sentinel first row `{kind: "session_start", ts_ns:
<t0>}`; verifier subtracts `t0_ns` to align with
`action_camera.json`'s `frame × 1/fps` axis. Sentinel missing → ABSTAIN
(`inputs_no_session_start`).

**Why ±1 frame skew.** Keyboard listener samples at OS scheduler
granularity (1–5 ms on Windows); camera thread at frame boundary
(33.3 ms @ 30 fps). A keyDown 2 ms before frame `i` may be observed
between frames `i-1` and `i`. ±1 absorbs the race. Skew > 1 = dropped
events or threading bug; should not be absorbed. Default
`skew_frames=1`; `>2` requires buyer-signed justification per
buyer_spec § 5.

### 3.3 Equality semantics

```
config["key_set_equality"] :
  "exact"      → keys_held == set(curr.keyCode)        # FAIL on any mismatch
  "subset_ok"  → set(curr.keyCode) ⊆ keys_held         # producer may drop, never invent
```

Default `"exact"`. **`"superset_ok"` is deliberately not offered** —
it would let the producer invent keys not in `inputs.jsonl`, which is
the FI-02 attack class.

### 3.4 Verdict

| Field | Value |
|---|---|
| `id` | `"R13_keycode_replay"` |
| `value` | `len(keys_held ^ set(keyCode))` (symmetric-difference size) |
| `threshold` | 0 |
| `passed` | `value == 0` |
| `unit` | `"keycount_diff"` |
| `detail` | on FAIL: `f"frame={i} expected={sorted(keys_held)} got={sorted(curr.keyCode)}"` |

### 3.5 ABSTAIN encoding

Per IL10, R13 returns `passed=False, value=-1, detail="ABSTAIN:<reason>"`:

| Reason | When |
|---|---|
| `ABSTAIN:no_inputs_log` | `inputs_log_status == "missing"` |
| `ABSTAIN:inputs_corrupt` | JSON parse error |
| `ABSTAIN:inputs_schema_mismatch` | row lacks ts_ns or kind |
| `ABSTAIN:inputs_no_session_start` | first row's `kind != "session_start"` |
| `ABSTAIN:fps_unknown` | `curr.fps` missing AND `video_probe is None` |

`detail.startswith("ABSTAIN:")` is the orchestrator's machine-readable
distinguisher (does not count as REJECT, contributes to ABSTAIN bucket
per `ARCH_BFT_CONSENSUS.md` § 5).

### 3.6 Adversarial test it should catch

```yaml
test_id: FI-02-R13
description: |
  The original FI-02. Mutate frame.keyCode[i]: [87] → [88] in
  action_camera.json; leave inputs.jsonl untouched (W actually pressed).
expected: R13 FAIL, value=2, sister R09 PASS (88 is valid VK)

test_id: FI-02b-R13_skew_attack
description: |
  Producer offsets all inputs.jsonl events by +5 frames; each press
  appears 5 frames late.
expected: R13 FAIL on every frame in offset region. skew_frames=1
  default makes this catchable; skew_frames=10 absorbs it (which is
  why >2 requires buyer signoff).
```

### 3.7 BFT consensus interaction

| Verifier | R13? | Rationale |
|---|---|---|
| **V₁ (Claude)** | YES | Re-derives from this spec. |
| **V₂ (MiniMax/GLM, dispatched later)** | YES | Re-derives from PRD criterion #5, NOT V₁'s code. Disjoint imports per BFT § 4.1. |
| **V₃ (physics oracle)** | **ABSTAIN by design** | Open Cartesian input set (any keyCode × any inputs.jsonl); not tabulatable. Hard-codes `{decision:"ABSTAIN", reason:"R13_not_in_oracle_table"}`. Per BFT § 8.1, GREEN-ABSTAIN halts only if no other GREEN votes; V₂ is GREEN ⇒ V₃ ABSTAIN is normal. |

**Per-frame quorum:** PASS/PASS → PASS. PASS/ABSTAIN → PASS (V₂
dispatch down). FAIL/PASS or PASS/FAIL → FAIL (defer to GREEN).
ABSTAIN/ABSTAIN → ABSTAIN, lifts to dataset-level per IL10. Dataset
verdict: AND of per-frame, modulo `per_residual_quorum` (default 0.99
per ARCH_PINNS_BUYER_SPEC § 6).

---

## 4. R14 — Mouse-Velocity vs Camera-Yaw Correlation

### 4.1 Function signature

```python
def r14_mouse_yaw_correlation(
    window: FrameWindow,
    extras: MultiModalExtras,
    config: dict,
    aux_state: "R14State",
) -> ResidualResult:
    """Verify mouse_dx and Δ(yaw) are correlated over a sliding window.
    |corr| ≥ threshold ⇒ PASS. Sign-agnostic (free-look games invert).
    config: window_size_frames (int, default 30 = 1 s @ 30 fps),
            min_abs_correlation (float, default 0.5),
            min_motion_threshold_px (float, default 2.0).
    aux_state: per-dataset R14State; entrypoint allocates one per
               dataset because R14 needs >3 frames; existing R01–R12
               pass aux_state=None. PINNS_RESIDUALS.md flags R14 with
               requires_aux_state: true.
    """
```

### 4.2 Sliding-window algorithm

```
Frame i ──► Δyaw_raw = yaw[i] − yaw[i-1]
            Δyaw     = Δyaw_raw − 360·round(Δyaw_raw/360)   ← unwrap to (−180,+180]
            mouse_dx = curr.mouse_dx
                ▼
       append (mouse_dx, Δyaw); pop oldest if full
                ▼
   ┌─ deque not full?     → PASS, value=0, detail="WARMUP"  (FAIL would
   │                          fire spuriously on first window-1 frames)
   ├─ Σ|mouse_dx| < thr?  → PASS, value=0, detail="STATIC"  (player held
   │                          still; corr undefined, not a defect)
   └─ else: corr = Pearson(mouse_dx_buf, yaw_buf)
                ▼
            |corr| ≥ min_abs_correlation  ?  PASS  :  FAIL
```

**Yaw-wrap.** Without unwrap, R14 fires every time the player turns
past 180°.

### 4.3 Sign-agnostic threshold

```
PASS  iff  |corr| ≥ min_abs_correlation
```

Most FPS: mouse-right ⇒ yaw right (positive corr). Inverted free-look:
opposite polarity (negative corr). Buyer cares that input *causes*
camera motion consistently — both polarities satisfy. What fails is
corr ≈ 0 (inputs decoupled from camera — FI-02 failure mode applied to
mouse).

**Default 0.5.** R² ≥ 0.25; mouse explains ≥ 25 % of yaw variance.
Legitimate test recordings (`samples/canonical_recording_03.tar.gz`)
exhibit |corr| ≥ 0.85; FI-mutation zeroing `mouse_dx` ⇒ corr ≈ 0; 0.5
splits cleanly. See open question #1.

### 4.4 ABSTAIN

| Reason | When |
|---|---|
| `ABSTAIN:no_yaw_field` | `curr.camera_rotation_oula` missing |
| `ABSTAIN:no_mouse_dx_field` | `curr.mouse_dx` missing |
| `ABSTAIN:nan_in_input` | NaN in either |

R14 deliberately does **not** ABSTAIN on warmup/static windows — those
are PASS, because absence of evidence at single-window granularity is
not evidence of decoupling, and dataset quorum re-tests on later
non-static windows.

### 4.5 Verdict

| Field | Value |
|---|---|
| `id` | `"R14_mouse_yaw_corr"` |
| `value` | `abs(corr)` if computed; 0 (warmup/static); −1 (abstain) |
| `threshold` | `config["min_abs_correlation"]` (default 0.5) |
| `passed` | `value >= threshold` OR `detail in ("WARMUP","STATIC")` |
| `unit` | `"pearson_r"` |
| `detail` | on FAIL: `f"corr={corr:+.3f} window=[{i-W+1},{i}]"` |

### 4.6 Adversarial tests

```yaml
test_id: FI-MOUSE-01
description: Zero out mouse_dx in frames [100, 200] while yaw varies
  correctly (recorder pretended mouse drove a gamepad-driven camera).
expected: R14 FAIL on each window overlapping [100, 200]; value ≈ 0.0.

test_id: FI-MOUSE-02
description: Invert sign of mouse_dx in random 10 % of frames (partial
  saboteur).
expected: R14 FAIL on windows where corruption drives |corr| < 0.5;
  value ≈ 0.3–0.4.
```

### 4.7 BFT consensus interaction

| Verifier | R14? | Notes |
|---|---|---|
| **V₁** | YES | numpy.corrcoef. |
| **V₂** | YES | scipy.stats.pearsonr — implementation diversity welcomed; numerical agreement to 1e-6 enforced by sentinel frames per `ARCH_PINNS_BUYER_SPEC.md` § 6. |
| **V₃** | **ABSTAIN** | Correlation residual cannot be tabulated. |

---

## 5. R15 — Frame-Rate vs Video-FPS Consistency

### 5.1 Function signature

```python
def r15_fps_video_consistency(
    window: FrameWindow,
    extras: MultiModalExtras,
    config: dict,
) -> ResidualResult:
    """Verify declared fps (curr.fps) matches ffprobe avg_frame_rate.
    |diff| < 0.5 fps ⇒ PASS.
    config: max_abs_fps_diff (float, default 0.5),
            tolerate_fractional (bool, default True; allows 30 vs 29.97).
    """
```

**One-time per dataset.** fps is a dataset-level property; entrypoint
calls R15 on `curr.frame == 0` only and caches via lru_cache.
Subsequent frames carry the cached verdict. Documented because it
deviates from R13/R14 per-frame model.

### 5.2 ffprobe pattern

`ffprobe` is already a recorder dependency
(`recorder_consumer_lite.py`, `recorder_metadata_emitter.py`). Verifier
reuses:

```
$ ffprobe -v error -select_streams v:0 \
    -show_entries stream=avg_frame_rate,r_frame_rate,nb_frames,codec_name \
    -show_entries format=duration -of json video.mp4
```

`avg_frame_rate` is rational `"30000/1001"`; verifier evaluates:

```python
def _eval_rational(s: str) -> float:
    num, den = s.split("/")
    return 0.0 if float(den) == 0 else float(num)/float(den)
```

R15 uses `avg_frame_rate` (actual stream avg) not `r_frame_rate` (raw
container) — `avg_frame_rate` catches a producer that lied about fps
but actually encoded fewer frames per second.

### 5.3 Verdict

```
|curr.fps − probe.fps_avg| < max_abs_fps_diff   ?   PASS  :  FAIL
```

| Field | Value |
|---|---|
| `id` | `"R15_fps_video"` |
| `value` | `abs(curr.fps − probe.fps_avg)` |
| `threshold` | `config["max_abs_fps_diff"]` (default 0.5) |
| `passed` | `value < threshold` |
| `unit` | `"fps"` |
| `detail` | on FAIL: `f"declared={curr.fps:.3f} probed={probe.fps_avg:.3f} diff={value:.3f}"` |

**Why 0.5 default.** 30 fps with 1 dropped frame/sec → 29 fps avg,
1 fps gap, fails (correctly). 30000/1001 NTSC declared as 30 → 0.03 fps
gap, passes. 0.5 splits. `tolerate_fractional` lets buyers tighten
around NTSC.

### 5.4 ABSTAIN

| Reason | When |
|---|---|
| `ABSTAIN:no_video_file` | `video_status == "missing"` |
| `ABSTAIN:ffprobe_failed` | ffprobe exit ≠ 0 |
| `ABSTAIN:no_fps_in_action` | `curr.fps` missing AND `dataset_meta.fps` missing |
| `ABSTAIN:zero_fps_video` | `probe.fps_avg == 0` (corrupt video; different residual handles it) |

### 5.5 Adversarial tests

```yaml
test_id: FI-FPS-01
description: Producer recorded at 24 fps but declared 30 (hardware
  capture rate ≠ encoder config — common bug).
expected: R15 FAIL, value=6.0, detail="declared=30.000 probed=24.000 diff=6.000".

test_id: FI-FPS-02
description: NTSC 29.97 declared as 30.
expected: R15 PASS, value=0.030.
```

### 5.6 BFT consensus interaction

| Verifier | R15? | Notes |
|---|---|---|
| **V₁** | YES | subprocess + json. |
| **V₂** | YES | Different ffprobe arg ordering / parser; same numeric output. |
| **V₃** | **YES (limited) — default ABSTAIN** | V₃ *can* contain row `ORACLE_R15_<dataset_id>: avg_fps == declared_within_0.5` IF Howard pre-populates per shipping-target dataset. For "any dataset, any declared fps", V₃ ABSTAINs. Hybrid documented in BFT § 4.3. **R15 is the residual where V₃ has the strongest case for opting in** because ffprobe outputs are deterministic and tabulatable per dataset. See open question #2. |

---

## 6. R16 — Depth-Frame-Count vs Video-Time Consistency

### 6.1 Function signature

```python
def r16_depth_count_consistency(
    window: FrameWindow,
    extras: MultiModalExtras,
    config: dict,
) -> ResidualResult:
    """Verify count(depth/*.exr) == ceil(duration_sec × depth_fps),
    ±depth_count_tolerance end-of-stream slack.
    config: depth_fps (float, default 6.0 per PRD),
            depth_count_tolerance (int, default 2),
            check_uniform_spacing (bool, default True).
    """
```

Dataset-level; computed once.

### 6.2 Filename parsing

Producer `recorder_depth_filler.py` writes either:

```python
PATTERN_A = re.compile(r"depth_(\d+)\.exr$")              # monotonic index
PATTERN_B = re.compile(r"depth_(\d+)_(\d+\.\d+)s\.exr$")  # index + timestamp
```

Neither matches → `depth_status = "filename_unparseable"` → ABSTAIN.

### 6.3 Count-equality

```
expected = ceil(duration_sec × depth_fps)
actual   = len(extras.depth_files)
diff     = abs(expected − actual)
PASS  iff  diff ≤ depth_count_tolerance
```

**Why ±2.** Depth thread samples at 6 Hz wall-clock independently of
the encoder. A recording ending at `t_end = 4.95 s` has
`ceil(4.95×6) = 30` expected frames; depth thread may write 29 (last
sample didn't fire) or 31 (one extra after stop). ±2 absorbs
end-of-stream race; ±3 would absorb a real bug (full 0.5-second gap).

### 6.4 Uniform-spacing (Pattern B only)

When `config["check_uniform_spacing"] == True` and timestamped names:

```
ts_i = parsed timestamp of i-th file (sorted by index)
expected_ts_i = i / depth_fps
PASS iff max_i |ts_i − expected_ts_i| < (1 / depth_fps) / 2
                                         ⌊── ½ depth-frame slack
```

Catches "producer wrote 30 files, all from the first half of the
recording" (stuck-thread bug). With Pattern A, silently skipped (does
not ABSTAIN; just doesn't contribute evidence).

### 6.5 Verdict

| Field | Value |
|---|---|
| `id` | `"R16_depth_count"` |
| `value` | `abs(expected − actual)` |
| `threshold` | `config["depth_count_tolerance"]` (default 2) |
| `passed` | `value <= threshold` AND uniform-spacing (if applicable) PASS |
| `unit` | `"frame_count"` |
| `detail` | on FAIL: `f"expected={expected} actual={actual} diff={value} max_spacing_drift={...}"` |

### 6.6 ABSTAIN

| Reason | When |
|---|---|
| `ABSTAIN:no_depth_dir` | `depth_status == "missing"` |
| `ABSTAIN:no_depth_files` | dir exists, empty |
| `ABSTAIN:filename_unparseable` | neither pattern matches |
| `ABSTAIN:no_video_duration` | `video_probe is None or duration_sec == 0` |

### 6.7 Adversarial tests

```yaml
test_id: FI-DEPTH-01
description: Producer dropped every other depth frame (thread starvation).
  10 s recording: expected=60, actual=30.
expected: R16 FAIL, value=30.

test_id: FI-DEPTH-02
description: Producer wrote depth at 12 fps instead of 6 (config error).
  10 s: expected=60, actual=120.
expected: R16 FAIL, value=60.

test_id: FI-DEPTH-03
description: 60 depth frames all timestamped within first 5 seconds
  (stuck-thread).
expected: R16 FAIL, detail="max_spacing_drift > 0.083 (= 1/2 of 1/6 s)".
```

### 6.8 BFT consensus interaction

| Verifier | R16? | Notes |
|---|---|---|
| **V₁** | YES | os.listdir + re + ffprobe. |
| **V₂** | YES | pathlib + scipy; same arithmetic. |
| **V₃** | **ABSTAIN** | Per-dataset depth count not tabulatable without per-dataset oracle row. May opt in like R15 if buyer-reference workflow becomes routine; not in this spec. |

---

## 7. Decision Table — Dataset-Level Verdict

```
For each R ∈ {R13, R14, R15, R16}:
  for each frame i:  v_i = R(window_i, extras, config)
  pass_count[R] = Σ(v_i.passed)
  total[R]      = N − abstain_count[R]
  rate          = pass_count[R] / total[R]   if total > 0 else None
```

| Per-residual quorum hit (rate ≥ 0.99)? | ABSTAIN ratio | Dataset verdict |
|---|---|---|
| YES | < 0.10 | **PASS** |
| YES | ≥ 0.10 | PASS-WITH-WARN (logged, not failing) |
| NO | < 0.10 | **FAIL** |
| NO | ≥ 0.50 | **ABSTAIN** (artifact dominates) |
| n/a | == 1.00 | **ABSTAIN** (artifact entirely missing) |

The 0.10 / 0.50 thresholds are placeholders; calibration in open
question #5.

---

## 8. What This Does NOT Solve

R13–R16 cover *cross-modal alignment* — quantifiable, deterministic
predicates. The following classes of buyer concern are **out of scope**
for the V₁/V₂/V₃ residual catalog and require V₄ buyer-signed sample
diff per `ARCH_BFT_CONSENSUS.md` § 2.4:

- **Camera-motion smoothness.** Perceptual; depends on buyer's downstream
  model architecture; can be technically correct yet unusable.
  Resolution: V₄ on a buyer-pre-signed "motion smoothness exemplar"
  frame. **Do not** invent an R-residual for smoothness.
- **Scene diversity.** Distribution over many recordings, not a single
  recording. Belongs in `bin/aggregate_sprint_report.py`, not residuals.
- **Cultural/content appropriateness.** Visible in-game ads, NSFW-adjacent
  visuals, UI text. Not a math problem; separate moderation pipeline.
- **Audio quality.** `audio_event_track.py` is the locus; this spec
  stays out of audio entirely.

**General rule:**

> A V₁/V₂/V₃ residual must, given a fully-specified `FrameWindow` +
> `MultiModalExtras` + buyer config, produce a deterministic verdict
> with the same numerical value on any compliant Python runtime.
> Anything requiring *human judgment* — even highly-trained,
> well-calibrated, agreeable judgment — is by construction a V₄
> matter, not a residual.

R13–R16 satisfy this. Smoothness, diversity, appropriateness do not.
Treat anyone proposing "an R-residual for camera smoothness" as having
missed the architectural distinction; the right move is V₄, not a
residual.

---

## 9. Migration

### 9.1 Existing R01–R12

**Stay as-is.** R13–R16 added incrementally without touching existing
residual code, schemas, or buyer-spec YAML. `enabled_residuals` lists
that don't enumerate R13–R16 simply don't run them; **no implicit
default-on**. Buyer must opt in:

```yaml
enabled_residuals:
  # ... existing R01–R12 unchanged ...
  - id: R13_keycode_replay
    threshold: 0
    config: {skew_frames: 1, key_set_equality: exact}
  - id: R14_mouse_yaw_corr
    threshold: 0.5
    config: {window_size_frames: 30, min_motion_threshold_px: 2.0}
  - id: R15_fps_video
    threshold: 0.5
    config: {tolerate_fractional: true}
  - id: R16_depth_count
    threshold: 2
    config: {depth_fps: 6.0, check_uniform_spacing: true}
```

### 9.2 Per-residual ship gate

Each new residual ships with **all of**:

1. Spec section (this document, §§ 3–6).
2. Type extensions in `oyster_runner/residuals_types.py`.
3. Producer-side artifact contract (existing producer; no producer code
   changes for R13–R16).
4. V₁ implementation in `bin/verify_pinns_claude.py`.
5. V₂ implementation in `bin/verify_pinns_glm.py`, re-derived from
   spec by a non-Claude LLM, audit per BFT § 4.1.
6. V₃ stub voting ABSTAIN with reason `R<NN>_not_in_oracle_table` (or
   PASS, R15 only with buyer-reference workflow).
7. Adversarial test in `bin/bft_adversarial_harness.py` registered in
   FI-* table per BFT § 6.5. Test must demonstrate: with residual
   disabled, mesh PASSes mutation; with residual enabled, mesh REJECTs.
8. Sentinel frame in `tests/sentinels/` exercising residual on
   known-good (PASS) and FI-mutated (FAIL) variants.

PR rejected by CI if any of 1–8 missing.

### 9.3 Roll-out order

Easiest first:

1. **R15** — single subprocess, single verdict per dataset, forgiving
   tolerance. Highest signal, lowest risk.
2. **R16** — file listing + arithmetic. Similar simplicity.
3. **R13** — FI-02 catch (highest value), but requires `inputs.jsonl`
   parsing and time-alignment care.
4. **R14** — correlation residual; needs `aux_state` ABI extension and
   sliding-window state.

BFT Phase A (dual-run shadow per BFT § 9.2) covers all four; advance to
Phase B (active gating) only after 100 consecutive consensus rounds
without spurious failure on known-good recordings, per residual.

### 9.4 Rollback

Each residual independently disable-able by removing its
`enabled_residuals` entry. No schema migration. If R-N produces a
sustained false-FAIL spike, response is to lower threshold in
buyer_spec, not hot-fix code, until calibration window completes and
proper threshold lands via signed change.

---

## 10. ISC — Ideal State Criteria for R13–R16

Each criterion binary (YES/NO ≤ 1 second).

### 10.1 Per-residual existence (V₁)

- **[C-13a]** `bin/verify_pinns_claude.py` contains callable
  `r13_keycode_replay` matching § 3.1. *Evidence:* AST grep returns 1.
- **[C-14a]** Same for `r14_mouse_yaw_correlation` per § 4.1.
- **[C-15a]** Same for `r15_fps_video_consistency` per § 5.1.
- **[C-16a]** Same for `r16_depth_count_consistency` per § 6.1.

### 10.2 Per-residual non-Claude implementation (V₂)

- **[C-13b]** `bin/verify_pinns_glm.py` contains `r13_keycode_replay`
  with import set disjoint from V₁'s per BFT § 4.1. *Evidence:*
  import-graph audit.
- **[C-14b]–[C-16b]** Same for R14, R15, R16.

### 10.3 Adversarial coverage

- **[C-13c]** `bin/bft_adversarial_harness.py` contains FI-02-R13 and
  FI-02b-R13_skew_attack per § 3.6. With R13 disabled: BFT PASSes;
  enabled: BFT REJECTs. *Evidence:* harness exit code on both.
- **[C-14c]** FI-MOUSE-01, FI-MOUSE-02 per § 4.6.
- **[C-15c]** FI-FPS-01, FI-FPS-02 per § 5.5.
- **[C-16c]** FI-DEPTH-01, FI-DEPTH-02, FI-DEPTH-03 per § 6.7.

### 10.4 IL10 enforcement

- **[C-IL10]** `bin/audit_artifact_honesty.py` exits 0 in latest CI on
  R13–R16 modules.

### 10.5 Anti-criteria — must remain false

- **[A-13]** No R13 implementation may return `passed=True` when
  `inputs_log_status != "ok"`. *Evidence:*
  `tests/residuals/test_R13_abstain.py` exercises 5 ABSTAIN paths.
- **[A-14]** No R14 implementation may return PASS when
  `len(mouse_dx_buf) < window_size`, *unless* `detail == "WARMUP"`.
- **[A-15]** No R15 implementation may return PASS when ffprobe exit
  ≠ 0.
- **[A-16]** No R16 implementation may return PASS when
  `depth_status == "missing"`.

### 10.6 ISC Tracker

```
┌─ 🎯 ISC: Ideal State Criteria ────────────────────┐
│ Phase: PLAN (R13–R16 multimodal residual design)  │
│ ✅ Criteria: 0 → 13  (+13)                        │
│ ⛔ Anti:     0 → 4   (+4)                         │
├───────────────────────────────────────────────────┤
│ ➕ [C-13a..C-16a] Per-residual V₁ existence       │
│ ➕ [C-13b..C-16b] Per-residual V₂ existence       │
│ ➕ [C-13c..C-16c] Adversarial coverage             │
│ ➕ [C-IL10]       IL10 enforcement                 │
│ ➕ [A-13..A-16]   ABSTAIN-honesty anti-criteria   │
└───────────────────────────────────────────────────┘
```

---

## 11. Open Questions for Howard

1. **R14 default threshold = 0.5.** |corr| ≥ 0.5 was chosen as middle
   ground (sensitivity vs specificity). Test recordings show |corr| ≥
   0.85 on legitimate runs — we could tighten to 0.7 and still cover all
   known FI cases. **Should default be 0.5, 0.6, or 0.7?**

2. **R15 V₃ opt-in for buyer-signed-sample workflow.** § 5.6 leaves V₃
   on R15 as a knob. Spec the `physics_oracle_table.json` schema
   extension for `expected_fps` per dataset_id, or stay V₃-ABSTAIN-by-default
   and defer to a later spec? **My rec: defer.** Per-dataset oracle
   rows materially complicate V₃'s "no-LLM, hand-tabulated, < 200 LOC"
   guarantee per BFT § 2.3.

3. **R16 depth_fps default 6.0.** PRD says "每秒 6 张均匀抽帧". Is this
   exactly 6.0, or do we permit 5.99 / 6.01 from container clock drift?
   If exact, ±2 tolerance suffices; if drift permitted, R16 needs a
   `depth_fps_tolerance` knob (target, not constant).

4. **`inputs.jsonl` t₀ sentinel.** R13 § 3.2 requires first row
   `kind: "session_start"`. **Is the recorder already emitting this?**
   Resolves with `head -n 1
   samples/canonical_recording_03/inputs.jsonl`. If not: (a) producer
   change (deeper, blocks R13 ship) or (b) verifier infers t₀ from
   `min(events.ts_ns)` (weaker, no producer change). I lean (a).

5. **Per-residual ABSTAIN ratio thresholds (§ 7).** Placeholder 0.10 /
   0.50. Real data needed. Run R13–R16 in shadow 2 weeks, plot ABSTAIN
   ratio per residual, lock thresholds. **Spec the calibration job
   now, or "calibrate later" acceptable for v1 ship?**

6. **R15 `tolerate_fractional` default = True.** Permits NTSC 29.97 →
   30 PASS — buyer-convenience-correct, but technically allows 0.03 fps
   lie. **Default-True, or default-False with NTSC buyers explicitly
   opting in?** My position: default-True. False default's failure mode
   (flood of spurious FAILs eroding trust in R15) is worse than
   under-detecting 0.03 fps.

---

## 12. Document Provenance

- Authored by: Vera Sterling (Algorithm Agent), 2026-05-05, Stream D.
- Source-of-truth files cited:
  - `docs/PRD_FORMULAS.md` — § B13 VK codes, criterion #3 mouse, #5 keyCode.
  - `docs/PRD_DIGEST.md` — iron-laws scaffold (this spec proposes IL10).
  - `docs/ARCH_BFT_CONSENSUS.md` — V₁/V₂/V₃ roles, PBFT decree, audit
    (§ 4.1), ABSTAIN semantics (§ 5, § 8.1).
  - `docs/ARCH_PINNS_BUYER_SPEC.md` — `FrameDict`, `FrameWindow`,
    `ResidualResult`, buyer-spec YAML, sentinels, quorum.
- Producer files cited (read-only):
  - `bin/recorder_consumer_lite.py` — emits `inputs.jsonl`.
  - `bin/recorder_metadata_emitter.py` — emits `video.mp4` metadata.
  - `bin/recorder_depth_filler.py` — emits `depth/*.exr`.
- Sibling specs **NOT modified**: `ARCH_BFT_CONSENSUS.md`,
  `ARCH_PINNS_BUYER_SPEC.md`, plus parallel-stream A/C/E specs.
- Scope: R13–R16 residual design, IL10, ISC for these four only.
  **Out of scope:** any R-residual ≥ R17, V₂ dispatch implementation
  choice (MiniMax vs GLM vs Codex), orchestrator tally code, V₄
  buyer-reference workflow.

---

*End of SPEC_R13_MULTIMODAL.md.*
