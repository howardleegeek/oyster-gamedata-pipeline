# PINNs-Style Anti-Circular Buyer-Spec Architecture

> Status: ARCHITECTURE SPEC (no implementation yet)
> Author: Architect agent (delegated from Opus), 2026-05-05
> Owner: Howard Li
> Reviewers needed: Howard (final), one verifier-side engineer, one producer-side engineer (must be different humans/agents)
> Replaces: alias-tolerant validation in `verify_action_camera.py` / `verify_prd_schema.py`
> Companion files (must coexist):
>   - `docs/PRD_FORMULAS.md` — math+physics constants extracted from PDF (single anchor)
>   - `docs/PINNS_RESIDUALS.md` — formal residual catalog (this spec generates it)
>   - `buyer-specs/<buyer>.yaml` — per-buyer enabled checks + tolerances

---

## 0. Why this exists (read first)

The producer (`bin/recorder_consumer_lite.py`, `bin/sample_tarball_builder.py`) and the verifier
(`bin/verify_*.py`, `bin/lint_v3_prd_grounded.py`) were both authored by the same Claude reading
the same PDF. **Any misreading of the PDF leaks into both sides**, and the verifier rubber-stamps
the producer's mistake. v0.19.0 shipped with `oula→euler`, `camera_Follow Offset → camera_follow_offset`,
and matching `KNOWN_FIELD_ALIASES` tables on the verifier — three identical wounds, zero alarm.

Howard's directive: *"如果俩都有问题，会有一模一样的问题."*

The fix is borrowed from **Physics-Informed Neural Networks (PINNs)**: don't treat physics as a
post-hoc check, treat it as a constraint that the data must satisfy *as a hard residual*, and
**evaluate the residual independently on both sides** (producer at write-time, verifier at
read-time) using **two different code paths** that reference **one** mathematical anchor.

The anchor is `docs/PRD_FORMULAS.md`. The two code paths live in two modules that must not
import each other. The buyer chooses which residuals matter and how tight the bounds are via
a YAML.

This spec defines that system: data layout, schemas, function signatures, flow, tests, CI gates,
and migration. **No Python implementation in this document.**

---

## 1. Glossary

| Term | Meaning |
|------|---------|
| **PRD anchor** | `docs/PRD_FORMULAS.md`. The only file allowed to be cited as ground truth for math/units. Every other artifact derives from it. |
| **Residual** | A scalar `r(frame) → ℝ` that measures how much a frame violates one physical/mathematical constraint. `r ≈ 0` = compliant. |
| **PINNs constraint** | A residual that producers compute at write-time **and** verifiers re-compute at read-time. The two computations must agree within ε. |
| **Buyer spec** | YAML file specifying which residuals matter for that buyer, what tolerances to use, and what acceptance strategy applies. |
| **Sentinel frame** | A pre-computed frame embedded in every dataset for which the residuals have known closed-form values. Used to detect codepath drift. |
| **Antibody test** | A test that intentionally injects a producer bug and asserts ≥95% of three-layer verifier catches it. |

---

## 2. Tarball layout (the data contract)

A delivered dataset is a single gzipped tarball:

```
dataset_<buyer_id>_<session_id>.tar.gz
└── dataset_<buyer_id>_<session_id>/
    ├── manifest.json                  # contents + checksums + buyer_spec ref
    ├── buyer_spec.snapshot.yaml       # IMMUTABLE COPY of the spec used to produce this dataset
    ├── prd_formulas.snapshot.md       # IMMUTABLE COPY of the formula anchor used
    ├── action_camera.json             # per-frame data (PRD authoritative field names)
    ├── residuals.jsonl                # per-frame producer-side residuals
    ├── sentinels.json                 # auto-embedded sentinel frames + expected residuals
    ├── systeminfo.json                # 5 fields per PRD page 3-4
    ├── gameinfo.xlsx                  # PRD page 12 template
    ├── video.mp4                      # H.265, 1920x1080, 30fps, 5-6 min
    ├── depth/                         # EXR float32 single-channel, 6 fps
    │   ├── 000000.exr
    │   ├── 000001.exr
    │   └── ...
    ├── audit/
    │   ├── producer_version.json      # exact git SHA, host, build flags
    │   ├── prd_anchor.sha256          # hash of prd_formulas.snapshot.md
    │   └── buyer_spec.sha256          # hash of buyer_spec.snapshot.yaml
    └── README.md                      # auto-generated from buyer_spec
```

### 2.1 `manifest.json`

```yaml
schema_version: "pinns-1.0"
tarball_id: "dataset_lark_wm_2026q2_20260505T143000Z"
buyer_id: "lark_wm_2026q2"
session_id: "20260505T143000Z"
producer:
  agent: "recorder_consumer_lite.py"
  version: "lite-v0.21.0"
  git_sha: "<40-hex>"
  build_host: "win-tester-04"
  built_at: "2026-05-05T14:30:00Z"
buyer_spec:
  path: "buyer_spec.snapshot.yaml"
  sha256: "<64-hex>"
  source_url: "https://github.com/howardleegeek/oyster-gamedata-pipeline/blob/main/buyer-specs/lark_wm_2026q2.yaml"
prd_anchor:
  path: "prd_formulas.snapshot.md"
  sha256: "<64-hex>"
contents:
  - {name: "action_camera.json", sha256: "...", size_bytes: 12345678}
  - {name: "residuals.jsonl",    sha256: "...", size_bytes: 234567}
  # ... etc
sentinel_count: 6
frame_count: 9000
```

**Rule:** every consumer (verifier, buyer-side ingest, replay tooling) reads `manifest.json`
first, verifies the two `*.sha256` fields match the embedded snapshot files, then proceeds.
If the snapshots disagree with the workspace `docs/PRD_FORMULAS.md`, the verifier **must**
warn but use the snapshot — datasets are immutable post-build.

### 2.2 `action_camera.json` field names (PRD-literal)

Copy verbatim from `docs/PRD_FORMULAS.md` and `docs/PRD_DIGEST.md` § 5. The wire format is
**fixed** at this list — no aliases, no synonyms, no auto-corrections:

```
time, fps, frame, route_type,
camera_position, camera_rotation_oula, camera_rotation_quaternion,
camera_Follow Offset,           ← literal: space + capital F
camera_intrinsics, camera_speed,
player_position, mouse_x, mouse_y, mouse_dx, mouse_dy,
keyCode, player_rotation_oula, player_rotation_quaternion, player_speed,
metric_scale
```

Vector3 = JSON array `[x, y, z]`. Vector4 quaternion = JSON array `[x, y, z, w]`.
camera_intrinsics = JSON object `{fx, fy, Cx, Cy}` with capital `Cx`/`Cy`.
keyCode = JSON array of int (ASCII). All field names are **case-sensitive and whitespace-sensitive**.

---

## 3. Buyer spec schema

`buyer-specs/<buyer_id>.yaml` is the **only** file a new buyer requires. Producer and verifier
read it identically. No code changes for new buyers.

### 3.1 Schema

```yaml
# buyer_spec_version follows semver. Breaking changes bump major.
buyer_spec_version: "1.0.0"

buyer:
  id: "lark_wm_2026q2"            # globally unique, lowercase + underscore
  name: "Lark World Model 2026 Q2"
  contact: "wm-data@lark.example"
  delivery_format: "tarball"      # only "tarball" supported in v1

# Which physical/mathematical residuals must hold for this buyer.
# A residual not listed here is NEITHER computed by producer NOR checked by verifier.
# The catalog of residual_ids lives in docs/PINNS_RESIDUALS.md.
enabled_residuals:
  - id: R01_quat_unit_norm
    enabled: true
    threshold: 0.01            # |‖q‖ - 1| < 0.01
  - id: R02_oula_quat_consistency
    enabled: true
    threshold_deg: 0.5         # |round_trip_err| < 0.5°
  - id: R03_kinematics_speed
    enabled: true
    threshold_rel: 0.05        # |speed_recorded - Δp·fps| / |speed| < 5%
  - id: R04_mouse_diff
    enabled: true
    threshold: 1e-6
  - id: R05_frame_dt_uniform
    enabled: true
    threshold_ms: 5
  - id: R06_intrinsics_symmetry
    enabled: true
    threshold_rel: 1e-6        # |fx - fy| / fx
  - id: R07_keycode_ascii
    enabled: true
    allowed_set: "PRD_DIGEST_TABLE_5"   # named lookup, not literal list
  - id: R08_speed_upper_bound
    enabled: true
    walking_max_mps: 5
    running_max_mps: 10
    flying_max_mps: 50
  - id: R09_depth_invalid_marker
    enabled: true
    sentinel_value: 0.0
    valid_min: 0.001
    valid_max: 1000.0
  - id: R10_pitch_range
    enabled: true
    min_deg: -180
    max_deg: 180
  - id: R11_quat_temporal_continuity
    enabled: true
    max_angular_jump_rad: 0.5
  - id: R12_video_fps_match
    enabled: true
    expected_fps: 30
    threshold_rel: 0.001

# Schema-level checks (not residuals — these are structural).
schema:
  required_top_level_files:
    - manifest.json
    - action_camera.json
    - residuals.jsonl
    - sentinels.json
    - systeminfo.json
    - video.mp4
    - depth/
  required_action_camera_fields:
    # Authoritative PRD-literal list. NO aliases.
    - time
    - fps
    - frame
    - route_type
    - camera_position
    - camera_rotation_oula
    - camera_rotation_quaternion
    - "camera_Follow Offset"            # quoted because of the space
    - camera_intrinsics
    - camera_speed
    - player_position
    - mouse_x
    - mouse_y
    - mouse_dx
    - mouse_dy
    - keyCode
    - player_rotation_oula
    - player_rotation_quaternion
    - player_speed
    - metric_scale
  forbidden_aliases:
    # Verifier MUST hard-fail if any of these appear. No silent rename.
    - camera_rotation_euler
    - player_rotation_euler
    - camera_follow_offset
    - cx                               # PRD uses capital Cx
    - cy

# Sentinels: producer MUST embed N synthetic frames at known indices with
# closed-form residuals. Verifier re-computes and compares. Drift here = bug.
sentinel_frames:
  count: 6
  positions: [0, 100, 1000, 4500, 8000, 8999]   # frame indices
  generator:
    seed: 42
    profile: "physics_canonical"     # named generator from docs/PINNS_RESIDUALS.md §6

# Acceptance strategy: how does the buyer decide a tarball passes?
acceptance_strategy:
  mode: "per_frame_with_quorum"      # or "per_clip_strict"
  per_residual_quorum: 0.99          # 99% of frames must pass each residual
  global_quorum: 0.97                # ≥97% of frames pass ALL enabled residuals
  fail_on_sentinel_mismatch: true    # any sentinel drift = whole tarball reject
  fail_on_forbidden_alias: true      # any forbidden alias = whole tarball reject

# Non-residual checks that the buyer cares about.
extras:
  video:
    expected_resolution: [1920, 1080]
    expected_codec: "h265"
    min_duration_sec: 300
    max_duration_sec: 360
  depth:
    expected_format: "exr"
    expected_channels: 1
    expected_dtype: "float32"
    expected_fps: 6
  systeminfo_required_fields:
    - gameProcessName
    - x
    - y
    - width
    - height
    - recordDpi

# Reporting & escalation
reporting:
  emit_per_frame_csv: true
  emit_html_report: true
  webhook: null                      # optional buyer endpoint
```

### 3.2 Validation rules for the YAML itself

A separate `bin/validate_buyer_spec.py` (out of scope here, separate spec) must reject any
buyer YAML that:

1. References an `id` not in `docs/PINNS_RESIDUALS.md`.
2. Uses a `forbidden_aliases` value that is also in `required_action_camera_fields` (contradiction).
3. Sets `sentinel_frames.positions` outside `[0, 8999]` for a 9000-frame clip.
4. Uses unknown top-level keys (strict mode).
5. Bumps `buyer_spec_version` major without a migration note.

---

## 4. `residuals.jsonl` schema (producer output)

One JSON object per frame, NDJSON layout for streaming.

```json
{
  "frame": 1234,
  "time": "2026-05-05 14:30:41.133",
  "buyer_spec_sha256": "<64-hex>",
  "prd_anchor_sha256": "<64-hex>",
  "producer_module": "oyster_runner.residuals_producer@<git_sha>",
  "residuals": [
    {"id": "R01_quat_unit_norm",          "value": 0.00021, "threshold": 0.01,  "passed": true,  "unit": "dimensionless"},
    {"id": "R02_oula_quat_consistency",   "value": 0.04,    "threshold": 0.5,   "passed": true,  "unit": "deg"},
    {"id": "R03_kinematics_speed",        "value": 0.012,   "threshold": 0.05,  "passed": true,  "unit": "rel"},
    {"id": "R04_mouse_diff",              "value": 0.0,     "threshold": 1e-6,  "passed": true,  "unit": "dimensionless"},
    {"id": "R05_frame_dt_uniform",        "value": 0.4,     "threshold": 5.0,   "passed": true,  "unit": "ms"},
    {"id": "R06_intrinsics_symmetry",     "value": 0.0,     "threshold": 1e-6,  "passed": true,  "unit": "rel"},
    {"id": "R07_keycode_ascii",           "value": 0,       "threshold": 0,     "passed": true,  "unit": "count_invalid"},
    {"id": "R08_speed_upper_bound",       "value": 3.7,     "threshold": 50.0,  "passed": true,  "unit": "m_per_s"},
    {"id": "R09_depth_invalid_marker",    "value": 0,       "threshold": 0,     "passed": true,  "unit": "count_invalid"},
    {"id": "R10_pitch_range",             "value": 12.4,    "threshold": 180.0, "passed": true,  "unit": "deg"},
    {"id": "R11_quat_temporal_continuity","value": 0.08,    "threshold": 0.5,   "passed": true,  "unit": "rad"},
    {"id": "R12_video_fps_match",         "value": 0.0001,  "threshold": 0.001, "passed": true,  "unit": "rel"}
  ]
}
```

### 4.1 Field semantics

| Field | Type | Constraint |
|-------|------|------------|
| `frame` | int | Matches `action_camera.json[i].frame`. |
| `time` | string | PRD format `YYYY-MM-DD HH:MM:SS.fff`, UTC. |
| `buyer_spec_sha256` | hex64 | Same value across the whole file (immutable per dataset). |
| `prd_anchor_sha256` | hex64 | Same value across the whole file. |
| `producer_module` | string | `<dotted_path>@<git_sha>` so the verifier can detect cross-module drift. |
| `residuals[].id` | string | Must match an `id` in `docs/PINNS_RESIDUALS.md` AND in `buyer_spec.enabled_residuals`. |
| `residuals[].value` | float / int | Numeric residual. Larger absolute value = bigger violation. |
| `residuals[].threshold` | float / int | Copied from buyer_spec. Producer copies it so verifier can detect tampering. |
| `residuals[].passed` | bool | `abs(value) <= threshold` (or constraint-specific predicate, see PINNS_RESIDUALS.md). |
| `residuals[].unit` | string | Pulled from the residual catalog. Verifier asserts unit match. |

### 4.2 Why `passed` is in the file

If we only stored `value` + `threshold`, the verifier would have to recompute `passed`, which
seems harmless but allows drift if the predicate is anything other than `abs(value)<threshold`
(e.g., R07 is "count of invalid keys = 0", R09 is sentinel-aware). Storing `passed` lets the
verifier's first check be: **does my predicate evaluation match producer's?** A mismatch is a
loud bug.

---

## 5. Residual catalog — function signatures

> The full math + page-citations live in `docs/PINNS_RESIDUALS.md` (separate file derived from
> this spec). Here we lock the **function signatures** so producer and verifier implementations
> compile against the same interface.

### 5.1 Common types

```python
# Located in: oyster_runner/residuals_types.py  (TYPES ONLY — no math)
from typing import Protocol, TypedDict
from dataclasses import dataclass

class FrameDict(TypedDict, total=False):
    frame: int
    time: str
    fps: float
    route_type: int
    camera_position: list[float]                  # [x, y, z]
    camera_rotation_oula: list[float]             # [pitch, yaw, roll] degrees
    camera_rotation_quaternion: list[float]       # [x, y, z, w]
    camera_Follow_Offset_KEY: list[float]         # NB: see §5.2 — wire field is "camera_Follow Offset"
    camera_intrinsics: dict                       # {fx, fy, Cx, Cy}
    camera_speed: list[float]                     # [vx, vy, vz] m/s
    player_position: list[float]
    mouse_x: float
    mouse_y: float
    mouse_dx: float
    mouse_dy: float
    keyCode: list[int]                            # ASCII codes
    player_rotation_oula: list[float]
    player_rotation_quaternion: list[float]
    player_speed: list[float]
    metric_scale: float

class FrameWindow(TypedDict):
    """A residual that needs neighbors gets this. Always 3 frames, mid is current."""
    prev: FrameDict | None       # None if i==0
    curr: FrameDict              # always present
    next: FrameDict | None       # None if i==N-1
    fps: float

@dataclass(frozen=True)
class ResidualResult:
    id: str                       # matches buyer_spec / catalog
    value: float | int
    threshold: float | int
    passed: bool
    unit: str
    detail: str = ""              # optional human-readable note for failures

class BuyerSpec(Protocol):
    """Read-only view of the loaded buyer YAML."""
    def get_residual_config(self, residual_id: str) -> dict: ...
    def is_enabled(self, residual_id: str) -> bool: ...
    @property
    def buyer_id(self) -> str: ...
    @property
    def sha256(self) -> str: ...
```

### 5.2 The wire-name-vs-pythonic-name problem

`camera_Follow Offset` is the **wire field name** (PRD-literal, has a space and capital F).
Python identifiers cannot contain spaces. The architecture rule:

- The **wire** field name in `action_camera.json` is `"camera_Follow Offset"` exactly.
- In Python `FrameDict`, the key is the **literal wire string** accessed via `frame["camera_Follow Offset"]`. A `TypedDict` with `total=False` and the wire-literal key is allowed via `__annotations__` direct assignment, OR a wrapper class exposes `frame.camera_follow_offset_value` reading the wire key. **The wrapper must reject any read of a Python-friendly alias if the wire name is missing.**
- The schema verifier uses the wire string. The forbidden-alias check rejects `camera_follow_offset` (no space, lowercase f) loudly.

This is the only place in the entire stack where we tolerate Python-vs-wire asymmetry, and it's
documented here so future agents don't "fix" it.

### 5.3 Residual function signatures (12 functions, no implementations)

All functions live in `oyster_runner/residuals_<side>/r{NN}_*.py` where `<side>` ∈ {`producer`, `verifier`}. The two sides are **separate Python packages with no cross-imports** — they share only the types in `residuals_types.py`.

```python
# oyster_runner/residuals_<side>/r01_quat_unit_norm.py
def r01_quat_unit_norm(frame: FrameDict, spec: BuyerSpec) -> ResidualResult:
    """
    PRD anchor: PRD_FORMULAS.md § A2.
    Computes ||q|| - 1 for camera_rotation_quaternion AND player_rotation_quaternion.
    Returns the WORST of the two.
    Unit: dimensionless. Threshold: spec['threshold'] (default 0.01).
    """
    ...

# r02_oula_quat_consistency.py
def r02_oula_quat_consistency(frame: FrameDict, spec: BuyerSpec) -> ResidualResult:
    """
    PRD anchor: PRD_FORMULAS.md § A1.
    Builds quaternion from camera_rotation_oula via Hamilton formula, compares
    against camera_rotation_quaternion via shortest-arc angular distance.
    Same for player. Returns max of (camera, player) in degrees.
    Unit: deg. Threshold: spec['threshold_deg'] (default 0.5).
    """
    ...

# r03_kinematics_speed.py
def r03_kinematics_speed(window: FrameWindow, spec: BuyerSpec) -> ResidualResult:
    """
    PRD anchor: PRD_FORMULAS.md § A3.
    Computes ||(p[n+1] - p[n]) * fps - speed[n]|| / max(||speed[n]||, ε).
    Computed for camera AND player. Returns max relative error.
    Skipped (returns passed=True, value=0) when window.next is None.
    Unit: rel. Threshold: spec['threshold_rel'] (default 0.05).
    """
    ...

# r04_mouse_diff.py
def r04_mouse_diff(window: FrameWindow, spec: BuyerSpec) -> ResidualResult:
    """
    PRD anchor: PRD_FORMULAS.md § A4.
    Asserts mouse_dx[n] = mouse_x[n] - mouse_x[n-1] within ε.
    Frame 0: dx and dy must both be exactly 0.
    Returns max(|recorded_dx - computed_dx|, |recorded_dy - computed_dy|).
    Unit: dimensionless (normalized [0,1] domain).
    """
    ...

# r05_frame_dt_uniform.py
def r05_frame_dt_uniform(window: FrameWindow, spec: BuyerSpec) -> ResidualResult:
    """
    PRD anchor: PRD_FORMULAS.md § A5 + B2 (fps=30).
    Computes (time[n+1] - time[n]) - 1000/fps in milliseconds.
    Unit: ms. Threshold: spec['threshold_ms'] (default 5).
    Skipped when window.next is None.
    """
    ...

# r06_intrinsics_symmetry.py
def r06_intrinsics_symmetry(frame: FrameDict, spec: BuyerSpec) -> ResidualResult:
    """
    PRD anchor: PRD_FORMULAS.md § B15 + Verification #8.
    Computes |fx - fy| / max(|fx|, ε).
    Unit: rel. Threshold: spec['threshold_rel'] (default 1e-6).
    """
    ...

# r07_keycode_ascii.py
def r07_keycode_ascii(frame: FrameDict, spec: BuyerSpec) -> ResidualResult:
    """
    PRD anchor: PRD_FORMULAS.md § B13 + Verification #5.
    Counts how many entries in frame.keyCode fall outside the buyer's allowed_set
    (e.g., PRD_DIGEST_TABLE_5 = {32, 65..90, 48..57, ...}).
    Unit: count_invalid. Threshold: 0 (any invalid = fail).
    """
    ...

# r08_speed_upper_bound.py
def r08_speed_upper_bound(frame: FrameDict, spec: BuyerSpec) -> ResidualResult:
    """
    PRD anchor: PRD_FORMULAS.md § A3 (B10 unit) + Verification #7.
    Computes max(||camera_speed||, ||player_speed||) in m/s. Compares against
    the highest enabled cap (default flying_max_mps=50). Reports excess as value.
    Unit: m_per_s. value=0 when within bound.
    """
    ...

# r09_depth_invalid_marker.py
def r09_depth_invalid_marker(frame: FrameDict, spec: BuyerSpec) -> ResidualResult:
    """
    PRD anchor: PRD_FORMULAS.md § B6.
    NOTE: This residual reads the corresponding depth/<frame>.exr file.
    Counts pixels that are NaN, inf, negative, or in (0, valid_min) — i.e.,
    "almost-zero but not exactly zero" (which would defeat the sentinel marker).
    Unit: count_invalid. Threshold: 0.
    Producer caches the count; verifier recomputes from the EXR independently.
    Frames without a corresponding depth file (depth is 6 fps, frames are 30 fps)
    return passed=True with value=0 and detail="no depth for this frame".
    """
    ...

# r10_pitch_range.py
def r10_pitch_range(frame: FrameDict, spec: BuyerSpec) -> ResidualResult:
    """
    PRD anchor: PRD_FORMULAS.md § B7-B9.
    Returns max(|pitch|, |yaw|, |roll|) for both camera and player oula.
    Unit: deg. Threshold: spec['max_deg'] (default 180).
    """
    ...

# r11_quat_temporal_continuity.py
def r11_quat_temporal_continuity(window: FrameWindow, spec: BuyerSpec) -> ResidualResult:
    """
    PRD anchor: PRD_FORMULAS.md § A1 (continuity implied by 30fps).
    Computes shortest-arc angular distance between camera_rotation_quaternion[n]
    and [n-1]. Skipped on first frame.
    Unit: rad. Threshold: spec['max_angular_jump_rad'] (default 0.5 rad ≈ 28.6°).
    """
    ...

# r12_video_fps_match.py
def r12_video_fps_match(frame: FrameDict, spec: BuyerSpec) -> ResidualResult:
    """
    PRD anchor: PRD_FORMULAS.md § B2 + Verification #4.
    Checks frame.fps == spec.expected_fps within rel tolerance.
    Unit: rel. Threshold: spec['threshold_rel'] (default 0.001).
    """
    ...
```

### 5.4 Residual registration

Each residual module exports a manifest:

```python
# Pseudo-code, lives in each rNN_*.py
RESIDUAL_MANIFEST = {
    "id": "R01_quat_unit_norm",
    "version": "1.0.0",
    "anchor_section": "A2",
    "needs": "frame",                # or "window" for residuals taking FrameWindow
    "side": "producer",              # or "verifier"
    "depends_on_files": [],          # additional files (e.g., depth/*.exr for R09)
    "fn": r01_quat_unit_norm,
}
```

A `residuals_<side>/__init__.py` collects all `RESIDUAL_MANIFEST` and exposes a registry mapping
`id → manifest`. The buyer-spec loader uses this registry to dispatch.

---

## 6. PINNs double-check flow

```
                  ┌────────────────────────┐
                  │  docs/PRD_FORMULAS.md  │  (single anchor)
                  └────────┬───────┬───────┘
                           │       │
                  reads    │       │   reads
                           ▼       ▼
        ┌────────────────────┐   ┌────────────────────┐
        │ residuals_producer │   │ residuals_verifier │
        │  (12 modules)      │   │  (12 modules)      │
        └────────┬───────────┘   └────────────┬───────┘
                 │                            │
                 │ no imports between sides   │
                 │                            │
                 ▼                            ▼
   write-time:   computes r_p            read-time:    computes r_v
                 stores in residuals.jsonl              loads action_camera.json
                                                        and re-evaluates from raw data

                            consistency check:
                              for each frame, each enabled residual:
                                assert |r_v.value - r_p.value| < EPS_CONSISTENCY
                                assert r_v.passed == r_p.passed
                                assert r_v.unit == r_p.unit

                            sentinel check:
                              for each sentinel frame:
                                assert r_v ≈ KNOWN_CLOSED_FORM_VALUE
                                assert r_p ≈ KNOWN_CLOSED_FORM_VALUE
```

`EPS_CONSISTENCY` is per-residual and lives in `docs/PINNS_RESIDUALS.md` (typically 10× tighter
than the buyer threshold, e.g., R01 threshold 0.01 → consistency eps 1e-3). Drift greater than
consistency eps **always** fails the dataset, regardless of buyer thresholds.

### 6.1 Producer-side execution

1. Recorder writes frame `i` of `action_camera.json`.
2. For each `id` in `buyer_spec.enabled_residuals`, the producer dispatches to
   `residuals_producer.registry[id].fn(frame_or_window, spec)`.
3. Producer appends a `ResidualResult` block to `residuals.jsonl`.
4. On clip finalize, producer computes the sentinel residuals and writes `sentinels.json`.

### 6.2 Verifier-side execution

1. Open the tarball, verify `manifest.json` checksums.
2. Stream-read `action_camera.json` and `residuals.jsonl` together.
3. For each frame, for each enabled residual, dispatch to
   `residuals_verifier.registry[id].fn(frame_or_window, spec)` to get `r_v`.
4. Compare `r_v` against the producer's `r_p` from `residuals.jsonl`:
   - same `id` ✓
   - `|r_v.value - r_p.value| < eps_consistency`
   - `r_v.passed == r_p.passed`
   - `r_v.unit == r_p.unit`
5. Apply buyer's `acceptance_strategy.per_residual_quorum`.
6. Re-evaluate sentinels independently and assert they match the closed-form expected values
   from `sentinels.json` (which the producer wrote based on the same generator the verifier
   uses — the generator lives in `docs/PINNS_RESIDUALS.md` § 6 as a reference algorithm).

### 6.3 Why two passes (re-eval + cross-check)

If we *only* re-evaluated, a producer could ship `residuals.jsonl` full of zeros and we'd miss
it (we'd just trust our own re-eval). If we *only* cross-checked the producer's values, a
malicious producer could fake them. **Both** passes together prove:

- The data is internally consistent (re-eval).
- The producer's claims about the data are honest (cross-check).
- No silent codepath drift between the two sides (sentinels).

---

## 7. Verifier topology

Four independent verifiers, each with a single responsibility. **A failure in one is meaningful
because the other three are independent code paths.**

```
                     ┌──────────────────────┐
                     │  buyer_spec.yaml     │
                     │  prd_formulas.md     │
                     └─────────┬────────────┘
                               │ loaded into BuyerSpec
                               ▼
   ┌──────────────────────────────────────────────────────────────────┐
   │                          VERIFIER PIPELINE                       │
   │                                                                  │
   │  V1 schema     ───►  V2 kinematics  ──►  V3 geometry  ──►  V4 temporal
   │  (structural)        (per-frame)         (per-frame +       (multi-frame)
   │                                           multi-file)
   │                                                                  │
   │  R01, R06, R10  ───  R03, R04, R08  ───  R02, R09          ───  R05, R07, R11, R12
   │                      (also sentinel comparison happens INSIDE each V)
   │                                                                  │
   │  fail-stops on      proceed even if      proceed even if    proceed
   │  schema bust        kinematics fails     geometry fails
   └──────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
                         aggregate_report.json
                         + per-residual CSV
                         + html report
```

### 7.1 Verifier responsibilities

| Verifier | Module | Reads | Computes | Hard-fails on |
|----------|--------|-------|----------|----------------|
| **V1 schema** | `bin/verify_schema.py` | manifest, action_camera.json, buyer_spec | required fields present, no forbidden aliases, types match, R01/R06/R10 single-frame residuals | missing fields, forbidden aliases, manifest checksum mismatch |
| **V2 kinematics** | `bin/verify_kinematics.py` | action_camera.json (window), residuals.jsonl | R03 (speed=Δp·fps), R04 (mouse diff), R08 (speed cap), cross-check vs producer | sentinel mismatch on kinematic residuals |
| **V3 geometry** | `bin/verify_geometry.py` | action_camera.json, depth/*.exr, residuals.jsonl | R02 (oula↔quat), R09 (depth invalid), cross-check vs producer | sentinel mismatch on geometry residuals |
| **V4 temporal** | `bin/verify_temporal.py` | action_camera.json (full), residuals.jsonl | R05 (dt), R07 (keyCode), R11 (quat continuity), R12 (fps match), cross-check | sentinel mismatch on temporal residuals |

### 7.2 Independence rule

Each verifier:
- Imports **only** `residuals_verifier.<NN>` — never `residuals_producer.*`.
- Does **not** import any other verifier module.
- Reuses **only** the types in `oyster_runner/residuals_types.py`.
- Loads constants **only** from `prd_formulas.snapshot.md` via a thin parser. No hardcoded
  numeric magic in the verifier source.

### 7.3 Output contract

Each verifier emits a JSON report:

```json
{
  "verifier_id": "V2_kinematics",
  "verifier_version": "1.0.0",
  "buyer_spec_sha256": "...",
  "prd_anchor_sha256": "...",
  "frames_checked": 9000,
  "residual_results": {
    "R03_kinematics_speed": {"pass_rate": 0.9978, "p99_value": 0.046, "max_value": 0.083, "first_failures": [...]},
    "R04_mouse_diff": {...},
    "R08_speed_upper_bound": {...}
  },
  "consistency_check": {
    "frames_drifted": 0,
    "first_drift_frames": []
  },
  "sentinel_check": {
    "all_passed": true,
    "drift_per_sentinel": [...]
  },
  "verdict": "PASS"  // or "FAIL", with reason in details
}
```

The four reports are concatenated by `bin/verify_aggregate.py` (out of scope here) into a final
`buyer_acceptance_report.json` that applies `acceptance_strategy`.

---

## 8. Anti-circular iron laws

These rules are **enforced by CI**, not optional discipline. Each has a corresponding lint /
test that lives in `tests/anti_circular/` and runs in the gate.

### IL1 — PRD prose is not a verifier source

> Verifier modules MUST NOT import or read PRD prose files (`docs/PRD.md`, `docs/PRD_DIGEST.md`,
> `docs/PRD_EN.md`). They may read **only** `docs/PRD_FORMULAS.md` (which contains math + table
> entries, no field-name prose).

**Why:** PRD prose is exactly where Claude's PDF-reading bias lives. If the verifier reads prose,
it inherits the same bias the producer has.

**CI check:** static AST grep over `bin/verify_*.py` and `oyster_runner/residuals_verifier/**`
asserting no string literals match `r"PRD\.md|PRD_DIGEST\.md|PRD_EN\.md|PRD_AUDIT.*\.md"`.

### IL2 — Field names load from buyer_spec, not from code

> No verifier or residual function may contain a string literal listing PRD field names.
> Required field lists must be loaded from `buyer_spec.required_action_camera_fields`.

**Why:** if the field list is in code, fixing one typo across producer+verifier becomes a single
PR — i.e., a single human's misreading shipping intact. Putting it in YAML forces a config diff
that a non-author can review.

**CI check:** ruff custom rule + AST scan that flags string literals matching the PRD field
naming pattern (`r"^[a-z_]+_(rotation|position|speed|intrinsics|Follow Offset)$"`) inside
verifier modules.

### IL3 — Producer and verifier residual modules cannot import each other

> `residuals_producer/*` MUST NOT import anything from `residuals_verifier/*`, and vice versa.
> Both may only import `residuals_types`, `residuals_constants` (a thin loader of
> `prd_formulas.snapshot.md`), and standard libraries.

**Why:** the whole point of two implementations is they fail differently. A shared helper
function leaks shared bugs.

**CI check:** import graph linter:
```
forbid: residuals_producer.* -> residuals_verifier.*
forbid: residuals_verifier.* -> residuals_producer.*
forbid: residuals_producer.* -> bin.recorder_consumer_lite      # producer module is the user
forbid: residuals_verifier.* -> bin.verify_*                    # verifier modules are the users
```

### IL4 — Two independent humans implement the two sides

> The first commit landing `residuals_producer/rNN_*.py` and the first commit landing
> `residuals_verifier/rNN_*.py` for the same `NN` must be authored by **different git authors**.

**Why:** even with structural separation, one person reading PRD_FORMULAS.md ambiguously could
write both sides the same wrong way.

**CI check:** `tools/check_residual_authorship.py` walks `git log --follow` for both files of
each residual NN and asserts the union of authors has size ≥2.

### IL5 — No alias maps in verifier code

> `KNOWN_FIELD_ALIASES`-style structures are FORBIDDEN in verifier modules. Aliases are buyer
> business: if a buyer accepts both `oula` and `euler`, that buyer's YAML names both — and the
> schema check still fails the data, then a downstream renamer normalizes. Verifier source
> never silently accepts an alias.

**Why:** alias maps are the exact failure mode that allowed v0.19.0 ship.

**CI check:** grep for `KNOWN_FIELD_ALIASES`, `ALIAS_MAP`, or any dict literal mapping
`"euler" -> "oula"` (or vice versa) in `bin/verify_*.py` / `residuals_verifier/*`. Hard fail.

### IL6 — Sentinel frames cannot be regenerated by the verifier

> `sentinels.json` is produced by the producer via the canonical generator described in
> `PINNS_RESIDUALS.md` § 6. The verifier MUST recompute the **expected residuals** from the
> sentinel **frame data** independently — but MUST NOT call the producer's generator.

**Why:** if the verifier called the producer's sentinel generator, sentinel drift would be
masked by symmetric bugs.

**CI check:** import graph linter — verifier modules cannot import the sentinel generator
function. Producer writes sentinel frames into `sentinels.json` plus into the regular
`action_camera.json` at the configured indices.

### IL7 — buyer_spec.yaml is the only place buyer-specific values live

> No buyer ID, threshold value, or "for buyer X we relax Y" branch is allowed in producer or
> verifier code. All variation is config-driven.

**Why:** branch logic in code becomes invisible technical debt and a vector for one buyer's
relaxation accidentally applying to all buyers.

**CI check:** grep for `if buyer_id ==` and `if spec.buyer_id` patterns. Hard fail.

---

## 9. Adversarial test harness (antibody tests)

The test suite must include `tests/adversarial/` with at least the five canonical injections
listed below. Each injection produces a corrupted dataset; the test asserts that the verifier
pipeline (V1+V2+V3+V4) catches it with **≥95% probability** (over random seeds).

### 9.1 Required injections

| # | Injection | What changes | Expected verdict | Caught by |
|---|-----------|--------------|------------------|-----------|
| A1 | **Wrong field name** | Producer emits `camera_rotation_euler` instead of `camera_rotation_oula` | FAIL | V1 (forbidden_aliases) |
| A2 | **Wrong quaternion order** | Producer writes `[w, x, y, z]` instead of `[x, y, z, w]` | FAIL | V3 (R02 oula↔quat consistency drifts), V1 (R01 still passes — quaternions are unit norm — but R02 drifts because rotation around the wrong axis) |
| A3 | **Wrong ASCII mapping** | Producer emits `keyCode = [119]` (lowercase 'w') instead of `[87]` ('W') for W key | FAIL | V4 (R07 if buyer's allowed_set is uppercase-only) plus consistency-check failure (producer's residuals.jsonl says "passed" but verifier disagrees) |
| A4 | **Wrong fps** | Producer records at 60 fps but writes `fps=30` in JSON | FAIL | V4 (R12 fails: actual frame dt is 16.7 ms but stated fps gives expected 33.3 ms; R05 fails) |
| A5 | **Wrong unit** | Producer reports speed in cm/s instead of m/s (10× too large) | FAIL | V2 (R08 speed cap), V2 (R03 speed=Δp·fps mismatch by 100×) |

### 9.2 Bonus injections (not required for v1 but encouraged)

- A6 — depth invalid marker swapped from 0.0 to NaN (R09 unit_invalid)
- A7 — fx ≠ fy by 0.5% (R06)
- A8 — sentinel frame at index 1000 mutated by 0.1° in pitch (sentinel check)
- A9 — `residuals.jsonl` truncated mid-file (consistency_check.frames_drifted spikes)
- A10 — buyer_spec.snapshot.yaml hash doesn't match manifest.json's `buyer_spec.sha256` (manifest tamper detection)

### 9.3 Harness mechanics

```
tests/adversarial/
├── conftest.py                    # fixture: clean_dataset (good tarball)
├── injectors/
│   ├── inject_a1_wrong_field_name.py
│   ├── inject_a2_wrong_quat_order.py
│   ├── inject_a3_wrong_ascii.py
│   ├── inject_a4_wrong_fps.py
│   ├── inject_a5_wrong_unit.py
│   └── ...
├── test_a1_caught.py              # assert verifier returns FAIL on injected tarball
├── test_a2_caught.py
├── ...
└── test_aggregate_catch_rate.py   # over 100 random seeds, ≥95% catch
```

`inject_*.py` modules:
- Take a clean tarball.
- Produce a corrupted tarball.
- Document **which residuals** they expect to fire.

`test_aN_caught.py`:
- Calls each injector on a freshly built clean dataset.
- Runs V1+V2+V3+V4.
- Asserts `aggregate_report.verdict == "FAIL"`.
- Asserts the **expected** residual is the one that flipped (not just *any* failure).
  - This protects against "we caught it for the wrong reason" — e.g., we wanted R12 to fire
    on A4 but actually V1 schema crashed because we corrupted file size; that's a different
    bug class and should be a different test.

`test_aggregate_catch_rate.py`:
- Runs all injectors with N=100 different sentinel seeds.
- Asserts catch_rate ≥ 0.95.

### 9.4 Mutation testing (deeper)

Once the antibody suite is green, run `mutmut` (Python mutation tester) against
`residuals_verifier/*` and assert mutation kill rate ≥ 90%. This is the deepest defense:
even if our antibody tests miss a class of bugs, mutation testing finds dead code paths.

---

## 10. CI gates (GitHub Actions topology)

```
.github/workflows/pinns_gate.yml

job: lint_anti_circular         # IL1-IL7 enforcement
  ↓ depends_on: nothing (cheap, fail fast)
job: residual_unit_tests        # each rNN function tested in isolation
  ↓ depends_on: lint_anti_circular
  matrix: side ∈ {producer, verifier}
job: sentinel_close_form        # closed-form sentinel values match catalog
  ↓ depends_on: residual_unit_tests
job: schema_lint                # buyer-specs/*.yaml all parse + are valid
  ↓ depends_on: lint_anti_circular
job: build_clean_tarball        # produce a known-good tarball with sample data
  ↓ depends_on: residual_unit_tests, sentinel_close_form
job: verify_clean_tarball       # V1+V2+V3+V4 on clean tarball must PASS
  ↓ depends_on: build_clean_tarball, schema_lint
job: adversarial_a1_a5          # 5 required injections each caught
  ↓ depends_on: verify_clean_tarball
  matrix: injection ∈ {A1, A2, A3, A4, A5}
job: adversarial_aggregate      # 95% catch rate over random seeds
  ↓ depends_on: adversarial_a1_a5
job: mutation_test              # mutmut kill rate ≥90%
  ↓ depends_on: adversarial_a1_a5
  if: github.event_name == 'push' && github.ref == 'refs/heads/main'   # nightly only
job: cross_buyer_smoke          # for each buyer-spec, build + verify
  ↓ depends_on: verify_clean_tarball
  matrix: buyer_id ∈ {discovered from buyer-specs/*.yaml}
```

### 10.1 Failure-to-layer mapping (debugging aid)

When CI is red, the layer pinpoints the bug:

| CI failure | Implication |
|------------|-------------|
| `lint_anti_circular` red | Architectural rule broken — fix the imports, not the code logic |
| `residual_unit_tests` red on producer side only | producer impl wrong, but verifier is fine → reach for `residuals_producer/rNN`  |
| `residual_unit_tests` red on verifier side only | verifier impl wrong → reach for `residuals_verifier/rNN` |
| `residual_unit_tests` red on both sides | Either both implementers misread `prd_formulas.md` (alarm! escalate to Howard), OR `prd_formulas.md` itself has a bug |
| `sentinel_close_form` red | Catalog drift — `PINNS_RESIDUALS.md` § 6 doesn't match what the formula module produces |
| `verify_clean_tarball` red | Producer + verifier disagree on a clean dataset — cross-side drift, deepest bug |
| `adversarial_aN` red | Specific class of producer bug not caught — beef up that residual or add a sibling residual |
| `adversarial_aggregate` red | Combined catch rate too low — test stochastic flakiness or weak residual coverage |
| `mutation_test` red | Verifier has redundant/dead checks — refactor toward unique tests |

### 10.2 Required PR template

```
## PINNs checklist
- [ ] If this PR adds a new residual NN, both `residuals_producer/rNN_*.py` and
      `residuals_verifier/rNN_*.py` are added in **separate commits by separate authors**.
- [ ] If this PR changes `docs/PRD_FORMULAS.md`, the diff cites a PDF page or screenshot.
- [ ] If this PR changes a buyer-spec, the buyer email is CC'd in the PR description.
- [ ] If this PR changes the schema (added/removed required field), I updated all buyer
      specs that opt-in to that field (or explicitly opted out and noted why).
- [ ] No `KNOWN_FIELD_ALIASES`-style maps added.
- [ ] Adversarial test for the new failure mode added under `tests/adversarial/`.
```

---

## 11. Multi-buyer extension model

### 11.1 Adding a new buyer

The **only** changes required are:

1. Create `buyer-specs/<new_buyer_id>.yaml` (copy from a reference YAML).
2. Adjust thresholds and `enabled_residuals` per buyer's negotiated SLA.
3. Add the buyer to `cross_buyer_smoke` CI matrix (auto-discovered if matrix uses
   `buyer-specs/*.yaml` glob).

**No code changes.** No producer changes. No verifier changes.

### 11.2 Buyer disagrees about a field meaning

Resolution path:

1. The disagreement is logged in the buyer-spec under `buyer.contact_notes`.
2. If the disagreement is over a tolerance: bump the buyer's threshold in YAML.
3. If the disagreement is over a field's existence/name: add it as a separate residual
   under `enabled_residuals` (e.g., `R13_buyerX_extra_field_check`) — and add the residual
   module if it's truly new physics.
4. If the disagreement is over a fundamental PRD constant (e.g., one buyer thinks roll range
   is `[-90, 90]` not `[-180, 180]`): **escalate**. This is a PRD anchor change. Update
   `docs/PRD_FORMULAS.md` and ripple to all buyers explicitly (each buyer-spec must opt-in
   to the new range or pin the old one).

### 11.3 Versioning

- `prd_formulas.md` is versioned by git tag `prd-anchor-vX.Y.Z`.
- Each buyer-spec has a `buyer_spec_version` field (semver).
- A dataset's `manifest.json` records both versions, so a 2027-vintage dataset built against
  `prd-anchor-v1.0.0` can still be verified using the snapshot embedded inside it, even if
  the workspace has moved on to `prd-anchor-v2.0.0`.

### 11.4 What if a buyer wants a NEW field outside PRD scope?

Two options:

- **Add to PRD anchor** if the field is universal (applies to all buyers): document with a
  PDF citation or a substitute primary source, ship as a new `BNN` constant.
- **Buyer-private field**: add under `extras.custom_fields` in the buyer-spec, and add a
  `R{NN}_buyer_<buyer_id>` residual that's only enabled for that buyer. Mark the residual as
  `private: true` in its manifest so it's not eligible for the cross-buyer smoke test.

The boundary: residuals starting with `R{01..NN}` are universal; residuals named
`R_<buyer_id>_<topic>` are private. Only universal residuals are subject to IL3 (independent
authorship) — private ones can be authored by anyone, since by definition they only fire on
that one buyer's data.

---

## 12. Migration plan from v0.19.0 / v0.20.0

### 12.1 Current state inventory

| File | Status under PINNs | Migration step |
|------|--------------------|----------------|
| `bin/verify_action_camera.py` | Tolerates `oula`/`euler` aliases (IL5 violation), hardcoded constants (IL1/IL2 violation) | Decompose into V2+V3+V4 verifiers; remove alias map; load constants from `prd_formulas.snapshot.md` |
| `bin/verify_prd_schema.py` | Owns alias map `_EULER_ALIASES` (IL5 violation), required-fields tuple in code (IL2 violation) | Move required-fields to buyer-spec; remove `_EULER_ALIASES`; rename to `bin/verify_schema.py` |
| `bin/verify_round_trip.py` | Imports from `verify_action_camera` directly (IL3 violation: it's a verifier importing another verifier's helpers) | Re-implement helpers locally OR move shared helpers into `residuals_verifier/_math.py` (allowed because it's only math, not field knowledge) |
| `bin/verify_visual_diff.py` | DIAGNOSTIC tool only — has `KNOWN_FIELD_ALIASES` for diff readability | KEEP but RENAME to `bin/diagnose_field_diff.py` and gate behind `--diagnose` flag. NOT part of CI gate. |
| `bin/lint_v3_prd_grounded.py` | 24-criterion lint, mostly stub | Subsume relevant checks into V1 schema verifier; deprecate this file |
| `bin/recorder_consumer_lite.py` | Producer with hardcoded fields | Add residual computation hooks; load field list from buyer-spec; keep recording engine as-is |
| `bin/sample_tarball_builder.py` | Producer with hardcoded sample data | Refactor to take buyer-spec on CLI; generate sentinels per buyer |

### 12.2 Migration phases

**Phase 0 — anchor freeze (Week 0)**
- Lock `docs/PRD_FORMULAS.md` content (already done in this branch).
- Write `docs/PINNS_RESIDUALS.md` (full residual catalog with closed-form sentinels). Owner: Howard or Architect.
- Author **two** humans/agents to land the first reference buyer-spec `buyer-specs/lark_wm_2026q2.yaml`.

**Phase 1 — types + registry (Week 1)**
- Land `oyster_runner/residuals_types.py`.
- Land `oyster_runner/residuals_constants.py` (loader for `prd_formulas.snapshot.md`).
- Land empty `residuals_producer/` and `residuals_verifier/` package skeletons with __init__ registries.
- Land IL1-IL7 lint suite under `tests/anti_circular/` (run on PRs immediately, before any
  residual is written, so the tooling is in place when residuals start landing).

**Phase 2 — residual implementation (Weeks 2-3)**
- For each `NN` in 01..12:
  - Engineer A writes `residuals_producer/rNN_*.py`.
  - Engineer B (different person/agent) writes `residuals_verifier/rNN_*.py`.
  - Both reference `prd_formulas.md` independently.
  - Both submit a unit test against the closed-form sentinel.
  - PR to merge requires both files green and authorship rule passing.

**Phase 3 — verifier decomposition (Week 3)**
- Build V1, V2, V3, V4 wired to the residual registries.
- Old `bin/verify_action_camera.py` keeps running in parallel (gate-only, not blocking) for
  one cycle to compare.
- Deprecation banner: `verify_action_camera.py` prints "DEPRECATED: use verify_schema.py /
  verify_kinematics.py / verify_geometry.py / verify_temporal.py" and exits with the same
  exit code its successors collectively produced.

**Phase 4 — adversarial harness (Week 4)**
- Land `tests/adversarial/` with A1-A5 injectors.
- Land mutation testing pipeline.
- Block merging to main if catch rate < 95%.

**Phase 5 — old verifier sunset (Week 5)**
- Remove `_EULER_ALIASES` from `verify_prd_schema.py`.
- Remove `KNOWN_FIELD_ALIASES` from `verify_visual_diff.py` (or move under `--diagnose` flag).
- Delete `bin/lint_v3_prd_grounded.py` (subsumed).
- Tag `pinns-v1.0.0` and update buyer-spec docs accordingly.

**Phase 6 — buyer rollout (Week 6+)**
- Onboard second buyer (different YAML, no code changes — proof point of IL7).
- Run full pipeline on real Minecraft clips, not just synthetic samples.
- Monitor `consistency_check.frames_drifted` for drift in production.

### 12.3 Sunset criteria for v0.19.0 / v0.20.0 verifiers

The old verifiers (`verify_action_camera.py` v0.19.0/v0.20.0) ship in deprecated mode for one
release cycle, then are removed when:

- `verify_clean_tarball` job has been green for 14 consecutive days on `main`.
- All buyer-specs have at least one production tarball verified end-to-end.
- The adversarial suite catch-rate is ≥95% for ≥30 consecutive runs.
- Howard signs off in `docs/MIGRATION_PINNS_SIGNOFF.md`.

Until then, both verifier stacks run in CI; only the new one blocks merge.

---

## 13. Failure modes this architecture does NOT solve

Honesty section. The following are **not** addressed and remain risks:

- **R-and-only-R bugs.** If a residual that should exist is simply missing from the catalog,
  no amount of cross-checking will catch it. Mitigation: the antibody suite acts as a
  changing detection function — if a known producer bug class isn't in any residual's
  coverage, an antibody test would fail and force you to add the residual.

- **Buyer-spec author error.** If the buyer's YAML is wrong (e.g., disables R02 by accident),
  the data shipped won't be checked. Mitigation: every buyer-spec PR requires reviewer signoff,
  and `tests/buyer_spec_sanity.py` flags suspicious patterns (all residuals disabled, threshold
  10× looser than peer buyers, etc.).

- **Anchor file misreading.** If `docs/PRD_FORMULAS.md` itself has a wrong formula (Hamilton
  quat with wrong sign convention), every implementer reads the same wrong thing. Mitigation:
  closed-form sentinels in `PINNS_RESIDUALS.md` § 6 must include manually-verifiable cases
  (e.g., "yaw 90° → q = (0, sin(45°), 0, cos(45°))" — a value Howard can spot-check by hand
  without running any code).

- **Residual function side-effect.** If a residual reads any file that depends on producer
  state (e.g., a cache of last-frame timestamps), the cross-side independence breaks.
  Mitigation: residual functions take only `frame`/`window` + `spec` and read whitelisted
  files via `RESIDUAL_MANIFEST.depends_on_files`. Anything else is a lint error.

- **Numerical precision differences.** Producer on Windows + verifier on macOS may differ in
  the 1e-12 range of `math.sin`. Mitigation: `EPS_CONSISTENCY` is set 10-100× larger than
  expected platform float drift; cross-check uses `math.isclose(rel_tol=1e-9, abs_tol=eps)`.

These are documented so future maintainers don't think the system is bulletproof.

---

## 14. Open questions for Howard's review

1. **Sentinel placement strategy.** Is `[0, 100, 1000, 4500, 8000, 8999]` the right default
   set, or should sentinels be Poisson-distributed to avoid producer learning their positions?

2. **Buyer YAML hosting.** Live in this repo or in a separate `buyer-contracts/` repo? Latter
   is more political (each buyer is a contract artifact).

3. **R09 (depth invalid) producer cost.** Counting invalid pixels at write-time scans every
   EXR; that's 6 fps × 5 min = 1800 file reads per dataset. Acceptable, or compute lazily?

4. **`camera_Follow Offset` literal.** Is there any chance the buyer would accept us renaming
   it to `camera_follow_offset` (no space, lowercase f) at the wire level? If yes, this entire
   asymmetry block (§5.2) goes away. Recommend asking the buyer once explicitly.

5. **`metric_scale` default.** Producer can default to 1.0 if absent, but should the verifier
   require the field be present? Current schema marks it as recommended; PRD page is silent.

6. **Cross-buyer leakage test.** A buyer with very loose thresholds (e.g., development buyer)
   shouldn't be the source of a regression that ships to a strict buyer. Should we run all
   strict buyers' verifiers against the development buyer's tarballs as a leakage test?

---

## Appendix A — Required new files (delivery checklist)

```
buyer-specs/
└── lark_wm_2026q2.yaml                              # reference buyer-spec (Phase 0)

docs/
├── PRD_FORMULAS.md                                  # ANCHOR (already exists)
├── PINNS_RESIDUALS.md                               # full catalog with closed-form sentinels (Phase 0)
├── ARCH_PINNS_BUYER_SPEC.md                         # this file
└── MIGRATION_PINNS_SIGNOFF.md                       # placeholder, Howard signs at Phase 5

oyster_runner/
├── residuals_types.py                               # Phase 1
├── residuals_constants.py                           # Phase 1: loader for prd_formulas.snapshot.md
├── residuals_producer/
│   ├── __init__.py                                  # registry
│   ├── r01_quat_unit_norm.py
│   ├── r02_oula_quat_consistency.py
│   ├── ...
│   └── r12_video_fps_match.py
└── residuals_verifier/
    ├── __init__.py                                  # registry
    ├── r01_quat_unit_norm.py                        # different author than producer side
    ├── ...
    └── r12_video_fps_match.py

bin/
├── verify_schema.py                                 # V1 (replaces verify_prd_schema.py)
├── verify_kinematics.py                             # V2
├── verify_geometry.py                               # V3
├── verify_temporal.py                               # V4
├── verify_aggregate.py                              # combines V1-V4 + applies acceptance_strategy
├── validate_buyer_spec.py                           # YAML validator
├── diagnose_field_diff.py                           # renamed verify_visual_diff.py, gated by --diagnose
└── recorder_consumer_lite.py                        # patched to write residuals.jsonl + sentinels.json

tests/
├── anti_circular/
│   ├── test_il1_no_prd_prose.py
│   ├── test_il2_no_field_literals.py
│   ├── test_il3_no_cross_imports.py
│   ├── test_il4_authorship.py
│   ├── test_il5_no_alias_maps.py
│   ├── test_il6_no_sentinel_generator_in_verifier.py
│   └── test_il7_no_buyer_branching.py
├── adversarial/
│   ├── conftest.py
│   ├── injectors/
│   └── test_*.py
├── unit/
│   ├── residuals_producer/
│   │   └── test_rNN_*.py                            # one per residual
│   └── residuals_verifier/
│       └── test_rNN_*.py                            # one per residual
└── integration/
    └── test_clean_tarball_pinns.py

.github/workflows/
└── pinns_gate.yml                                   # CI gate, Phase 4
```

---

## Appendix B — ASCII flow summary

```
                                ┌───────────────────────┐
                                │ docs/PRD_FORMULAS.md  │
                                │      (anchor)         │
                                └──────────┬────────────┘
                                           │
                            ┌──────────────┴───────────────┐
                            │                              │
                            ▼                              ▼
           ┌─────────────────────────┐        ┌──────────────────────────┐
           │ residuals_producer/     │        │ residuals_verifier/      │
           │   rNN_*.py (×12)        │        │   rNN_*.py (×12)         │
           │   author A              │        │   author B               │
           └────────────┬────────────┘        └─────────────┬────────────┘
                        │                                   │
   ┌──────────┐         │                                   │
   │ buyer    │ reads   │                                   │ reads
   │ spec     ├─────────┴───────────────────────────────────┘
   │ yaml     │
   └────┬─────┘
        │
        ▼
   producer pipeline               verifier pipeline
   ┌────────────────┐              ┌────────────────────────────────────┐
   │ recorder ─►    │              │ V1 schema  ─► V2 kinematics ─►     │
   │ residuals_p ─► │ tarball ────►│ V3 geometry ─► V4 temporal  ─►     │
   │ sentinels ─►   │              │ aggregate ─► acceptance_strategy   │
   │ manifest      │               │                                    │
   └────────────────┘              └────────────────┬───────────────────┘
                                                    │
                                                    ▼
                                            buyer_acceptance_report.json
                                                    │
                                                    ▼
                                                PASS / FAIL
```

---

End of spec. Length: ~1,200 lines.
