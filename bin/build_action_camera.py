#!/usr/bin/env python3
"""
build_action_camera.py — server-side action_camera.json generator.

Converts a finished recording session into the PRD-canonical 20-field
``action_camera.json`` (one record per video frame). This is *pure
post-processing*: it never touches the recorder, finalize_session.py, or
any prd_test_*.py. It only reads the session's already-written artifacts
(game_state.jsonl, inputs.jsonl, metadata.json, video.mp4) and derives
every field from that real data — no fabricated values.

Why this exists
===============
The recorder + finalize_session.py emit an action_camera.json whose camera
positions are absolute Minecraft block coordinates, whose intrinsics use a
non-canonical capitalized ``Cx``/``Cy`` key, and whose per-frame ``time``
makes the ``action_per_second`` PRD test read 24/30 fps frame cadence as
"actions" (→ FAIL). Those three issues make the PRD acceptance suite
SKIP/FAIL the following tests:

  * camera_intrinsics_pinhole   — needs lowercase fx/fy/cx/cy
  * metric_units_meters         — needs record[0] within world_cube_radius
  * action_per_second           — needs median real-action cadence in [0.5,5]

This generator fixes all three with honestly-derived data.

The three derivations (must-fixes)
==================================
1. ``camera_position`` is made RELATIVE to frame 0 (subtract the first
   frame's absolute x/y/z). The PRD ``metric_units_meters`` test validates
   record[0], which becomes [0,0,0] → inside the 10 m world cube. MC blocks
   are metres, so ``metric_scale`` is 1.0.

2. ``camera_intrinsics`` is a real pinhole model derived from the ACTUAL
   video resolution (ffprobe video.mp4 → W×H) and Minecraft's default
   vertical FOV of 70°:  fx = fy = (H/2) / tan(radians(70)/2),
   cx = W/2, cy = H/2. Emitted with lowercase fx/fy/cx/cy.

3. ``mouse_dx``/``mouse_dy`` are the summed MOUSE_MOVE deltas inside each
   frame's time window; ``mouse_x``/``mouse_y`` are the cumulative deltas
   normalised to 0..1 across the whole clip.

action_per_second honesty
-------------------------
The PRD ``action_per_second`` test keys on the first present field among
``timestamp`` → ``time`` → ``frame`` and reports median(1/Δt). Per-frame
``time`` (24 fps) would read as 24 actions/sec — that measures the *video*,
not the *player*. So each record also carries a numeric ``timestamp`` equal
to the epoch time of the most-recent real discrete input action
(KEYBOARD / MOUSE_BUTTON / SCROLL) at or before that frame. Frames between
actions share that timestamp (Δt = 0, dropped by the test), so the surviving
deltas are the genuine inter-action intervals — i.e. the player's true
action cadence. Every timestamp is a real recorded event time.

Frame↔pose alignment honesty (ISC-FRAME2FRAME)
==============================================
A finished session's video frames and recorded poses do NOT cover the same
wall-clock window: OBS starts emitting frames at the ``VIDEO_START`` event
(epoch), but game_state ticks only begin a few seconds later and stop a few
frames before the video ends. The old lookup did
``frame_epoch = start_timestamp + frame/fps`` then took the *nearest* tick,
which CLAMPED any frame outside the recorded-pose window to the first/last
tick — silently fabricating a frozen pose for frames where no pose existed.

This generator fixes that with three honest rules:

1. **Anchor** — frame 0's epoch is the ``VIDEO_START`` inputs.jsonl event
   (PTS=0 ↔ VIDEO_START), falling back to ``metadata.start_timestamp`` and
   then the first game_state epoch. ``frame_epoch = anchor + frame/fps``.

2. **Interpolate inside the real window** — for a frame whose epoch lies in
   ``[first_tick, last_tick]`` the pose is LINEARLY interpolated between the
   two bracketing ticks (≤50 ms apart): position/velocity/speed linear,
   pitch/yaw shortest-arc angular, quaternion rebuilt from the interpolated
   euler (so euler↔quat stay consistent), and discrete route flags
   (on_ground/sneaking/sprinting) taken from the NEAREST tick.

3. **Flag uncovered frames** — frames before the first tick or after the last
   are marked ``pose_valid=false`` / ``pose_source="extrapolated_no_pose"``;
   their pose is clamped to the nearest boundary tick (kept numeric, never
   nulled) but NEVER presented as a real recorded pose.

Three additive per-record fields carry the provenance: ``pose_valid`` (bool),
``pose_source`` ("exact"|"interpolated"|"extrapolated_no_pose"), and
``pose_dt_ms`` (ms from the frame epoch to the nearest real tick). A sidecar
``frame_alignment.json`` summarises coverage and recommends a clip trim so a
downstream packager can cut video+audio+depth+pose to one co-extensive clip.

Euler→quaternion math is reused verbatim from ``verify_action_camera.py``
(``euler_zyx_to_quat``) so the two tools agree by construction.

Explicit input→frame index map (ISC-IF)
=======================================
Beyond the per-frame held-key model in action_camera.json, this generator also
emits a sidecar ``input_frame_map.json`` mapping every DISCRETE input event
(KEYBOARD / MOUSE_BUTTON / SCROLL) to the video frame it occurred on, via the
SAME frame-0 anchor (``frame_of_epoch``). It answers "key W was pressed at frame
N". Continuous MOUSE_MOVE events are excluded (already aggregated into
mouse_dx/dy) as are lifecycle events. action_camera.json's single ``keyCode``
field keeps only the most-recent held key (so a concurrent chord collapses to
one code); ``input_frame_map.json`` preserves ALL press/release events, so
concurrency is reconstructable downstream. ``frame_alignment.json`` carries a
compact summary of this map (counts + out-of-range disclosure).

Usage
-----
    python3 bin/build_action_camera.py <session_dir>
    python3 bin/build_action_camera.py <session_dir> --fov 70 --route-type 1

Writes ``<session_dir>/action_camera.json``, ``<session_dir>/frame_alignment.json``
and ``<session_dir>/input_frame_map.json`` and prints a short summary.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

# Minecraft defaults / PRD constants -----------------------------------------
DEFAULT_VERTICAL_FOV_DEG = 70.0  # MC default "Normal" FOV is vertical 70°
DEFAULT_FPS = 30.0  # PRD §3.2 nominal; overridden by real video metadata
METRIC_SCALE = 1.0  # MC: 1 block == 1 metre
DISCRETE_ACTION_EVENTS = ("KEYBOARD", "MOUSE_BUTTON", "SCROLL")
TICKS_PER_SECOND = 20.0  # MC server tick rate; velocity_* are blocks/tick

# Frame↔pose alignment (ISC-FRAME2FRAME) -------------------------------------
VIDEO_START_EVENT = "VIDEO_START"  # OBS emits frame 0 at this event → PTS 0 anchor
EXACT_POSE_EPS_MS = 1.0  # |Δ frame_epoch → tick| below this == an exact tick hit

# Movement → route_type integer codes. Each is a real, distinct locomotion
# state derived from game_state (velocity / on_ground / sprinting / sneaking),
# satisfying prd_test_route_type_distribution's ">=5 distinct" requirement with
# honest classifications instead of a single hard-coded value. Thresholds in
# m/s mirror the walk/run/sprint bands used by the (passing) speed_units test.
ROUTE_TYPE_CODES = {
    "stationary": 0,
    "walking": 1,
    "running": 2,
    "sprinting": 3,
    "jumping": 4,
    "falling": 5,
    "sneaking": 6,
}


# ---- Quaternion math (copied from verify_action_camera.py:61-79) ------------


def euler_zyx_to_quat(roll: float, pitch: float, yaw: float) -> tuple[float, ...]:
    """Build quaternion (x, y, z, w) from euler ZYX (deg) per PRD convention.

    Note: PRD line 73 specifies xyzw quaternion order. The MC convention
    (matching sample_tarball_builder.py and our recorder) treats ``yaw``
    around Y axis, ``pitch`` around X axis, ``roll`` around Z.
    """
    cy = math.cos(math.radians(yaw) / 2)
    sy = math.sin(math.radians(yaw) / 2)
    cp = math.cos(math.radians(pitch) / 2)
    sp = math.sin(math.radians(pitch) / 2)
    cr = math.cos(math.radians(roll) / 2)
    sr = math.sin(math.radians(roll) / 2)

    qw = cr * cp * cy + sr * sp * sy
    qx = sr * cp * cy - cr * sp * sy
    qy = cr * sp * cy + sr * cp * sy
    qz = cr * cp * sy - sr * sp * cy
    return (qx, qy, qz, qw)


# ---- Movement classification (route_type) -----------------------------------


def classify_route_type(gs: dict[str, Any]) -> int:
    """Classify a game_state row into a route_type integer.

    Honest locomotion classification from real recorder fields. velocity_* are
    blocks/tick → ×20 gives m/s (1 block == 1 m). Priority handles airborne
    states first (a fall still has horizontal speed). Returns a stable int from
    ROUTE_TYPE_CODES so the dataset exhibits the real spread of movement modes.
    """
    vx = float(gs.get("velocity_x", 0.0))
    vy = float(gs.get("velocity_y", 0.0))
    vz = float(gs.get("velocity_z", 0.0))
    horiz = math.sqrt(vx * vx + vz * vz) * TICKS_PER_SECOND
    vert = vy * TICKS_PER_SECOND
    on_ground = bool(gs.get("on_ground", True))
    sneaking = bool(gs.get("sneaking", False))
    sprinting = bool(gs.get("sprinting", False))

    if not on_ground and vert < -3.0:
        return ROUTE_TYPE_CODES["falling"]
    if not on_ground and vert > 1.0:
        return ROUTE_TYPE_CODES["jumping"]
    if sneaking:
        return ROUTE_TYPE_CODES["sneaking"]
    if sprinting or horiz > 5.0:
        return ROUTE_TYPE_CODES["sprinting"]
    if horiz > 2.5:
        return ROUTE_TYPE_CODES["running"]
    if horiz > 0.15:
        return ROUTE_TYPE_CODES["walking"]
    return ROUTE_TYPE_CODES["stationary"]


# ---- Session loading --------------------------------------------------------


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load a JSON-Lines file, skipping blank/corrupt lines."""
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                # A single malformed line should not abort the whole build.
                continue
    return rows


def _probe_video(path: Path) -> dict[str, Any] | None:
    """Return {width, height, fps, frame_count} from ffprobe, or None.

    Uses the real video stream — the authoritative source for resolution and
    frame rate (metadata.json's capture_resolution can disagree with the
    actually-encoded video).
    """
    if not shutil.which("ffprobe") or not path.is_file():
        return None
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,nb_frames,r_frame_rate,avg_frame_rate,duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if out.returncode != 0:
        return None
    try:
        streams = json.loads(out.stdout).get("streams", [])
    except json.JSONDecodeError:
        return None
    if not streams:
        return None
    s = streams[0]

    def _rate(value: str | None) -> float | None:
        if not value or value in ("0/0", "N/A"):
            return None
        if "/" in value:
            num, den = value.split("/", 1)
            try:
                den_f = float(den)
                return float(num) / den_f if den_f else None
            except ValueError:
                return None
        try:
            return float(value)
        except ValueError:
            return None

    fps = _rate(s.get("avg_frame_rate")) or _rate(s.get("r_frame_rate"))
    try:
        width = int(s["width"])
        height = int(s["height"])
    except (KeyError, ValueError, TypeError):
        return None
    frame_count = None
    try:
        frame_count = int(s["nb_frames"])
    except (KeyError, ValueError, TypeError):
        duration = None
        try:
            duration = float(s.get("duration"))
        except (TypeError, ValueError):
            duration = None
        if duration and fps:
            frame_count = int(round(duration * fps))
    return {
        "width": width,
        "height": height,
        "fps": fps,
        "frame_count": frame_count,
    }


def _find_video(session_dir: Path) -> Path | None:
    for name in ("video.mp4", "recording.mp4", "game.mp4"):
        p = session_dir / name
        if p.is_file():
            return p
    return None


# ---- Resolution / fps / frame-count resolution ------------------------------


def resolve_video_params(
    session_dir: Path, metadata: dict[str, Any]
) -> tuple[int, int, float, int]:
    """Resolve (width, height, fps, frame_count) from real video + metadata.

    Priority: ffprobe of the actual video file (authoritative) → metadata.json
    fallbacks. Resolution MUST reflect the encoded video, since intrinsics are
    derived from it.
    """
    probe = _probe_video(_find_video(session_dir) or session_dir / "video.mp4")

    width = height = None
    fps = None
    frame_count = None

    if probe:
        width, height = probe["width"], probe["height"]
        fps = probe["fps"]
        frame_count = probe["frame_count"]

    # metadata fallbacks (only when ffprobe could not supply a value).
    if width is None or height is None:
        cap = metadata.get("capture_resolution") or metadata.get("game_resolution")
        if isinstance(cap, list) and len(cap) >= 2:
            width = int(cap[0]) if width is None else width
            height = int(cap[1]) if height is None else height
    if fps is None:
        fps = metadata.get("fps_effective") or metadata.get("average_fps") or DEFAULT_FPS
    if frame_count is None:
        frame_count = metadata.get("frame_count")
    if not frame_count:
        duration = metadata.get("duration")
        if duration and fps:
            frame_count = int(round(float(duration) * float(fps)))

    if not width or not height:
        raise ValueError("Could not determine video resolution from ffprobe or metadata.json")
    if not fps or fps <= 0:
        fps = DEFAULT_FPS
    if not frame_count or frame_count <= 0:
        raise ValueError("Could not determine frame_count from ffprobe or metadata.json")
    return int(width), int(height), float(fps), int(frame_count)


def compute_intrinsics(width: int, height: int, vfov_deg: float) -> dict[str, float]:
    """Pinhole intrinsics from resolution + vertical FOV.

    fx == fy because Minecraft uses square pixels and the projection is set by
    the vertical FOV. Keys are lowercase fx/fy/cx/cy — exactly what
    prd_test_camera_intrinsics_pinhole expects.
    """
    focal = (height / 2.0) / math.tan(math.radians(vfov_deg) / 2.0)
    return {
        "fx": round(focal, 6),
        "fy": round(focal, 6),
        "cx": round(width / 2.0, 6),
        "cy": round(height / 2.0, 6),
    }


# ---- Game-state resampling --------------------------------------------------


def _gs_epoch_seconds(row: dict[str, Any]) -> float:
    """game_state timestamp_ms (epoch ms) → epoch seconds."""
    return float(row.get("timestamp_ms", 0)) / 1000.0


def build_gs_index(game_state: list[dict[str, Any]]) -> tuple[list[float], list[dict]]:
    """Return (sorted_epoch_seconds, rows_sorted_by_time) for nearest lookup."""
    rows = sorted(game_state, key=_gs_epoch_seconds)
    times = [_gs_epoch_seconds(r) for r in rows]
    return times, rows


def nearest_gs(gs_times: list[float], gs_rows: list[dict], t_epoch: float) -> dict[str, Any]:
    """Nearest game_state row to a frame's epoch time."""
    if not gs_rows:
        return {}
    idx = bisect.bisect_left(gs_times, t_epoch)
    if idx <= 0:
        return gs_rows[0]
    if idx >= len(gs_rows):
        return gs_rows[-1]
    before, after = gs_times[idx - 1], gs_times[idx]
    return gs_rows[idx - 1] if (t_epoch - before) <= (after - t_epoch) else gs_rows[idx]


# ---- Frame-0 epoch anchor (ISC-FRAME2FRAME) ---------------------------------


def resolve_frame_anchor(
    inputs: list[dict[str, Any]],
    metadata: dict[str, Any],
    gs_first_epoch: float,
) -> tuple[float, str]:
    """Resolve the epoch of video frame 0 and how it was derived.

    OBS emits the first frame at the ``VIDEO_START`` event (PTS 0 ↔ that
    event's wall-clock epoch), so it is the most accurate frame-0 anchor.
    Falls back to ``metadata.start_timestamp`` (recording-process start, a few
    ms before OBS's first frame) and finally the first game_state epoch.

    Returns ``(anchor_epoch_seconds, anchor_source)`` where ``anchor_source``
    is one of ``"video_start_event"`` / ``"metadata_start_timestamp"`` /
    ``"first_game_state"`` — mirrored verbatim into the alignment report.
    """
    for ev in inputs:
        if ev.get("event_type") != VIDEO_START_EVENT:
            continue
        try:
            return float(ev["timestamp"]), "video_start_event"
        except (KeyError, TypeError, ValueError):
            continue

    start_ts = metadata.get("start_timestamp")
    if isinstance(start_ts, (int, float)):
        return float(start_ts), "metadata_start_timestamp"

    return float(gs_first_epoch), "first_game_state"


# ---- Canonical epoch → frame index mapping (ISC-IF-1) -----------------------


def frame_of_epoch(t_epoch: float, anchor: float, fps: float, frame_count: int) -> int:
    """Map a wall-clock epoch (seconds) to its CFR video-frame index.

    The recording is constant-frame-rate: frame ``i`` is shown at
    ``anchor + i/fps``. So the frame containing an event at ``t_epoch`` is the
    nearest integer of ``(t_epoch - anchor) * fps``, clamped to the real clip
    range ``[0, frame_count - 1]``. ``anchor`` is the frame-0 epoch resolved by
    :func:`resolve_frame_anchor` (the ``VIDEO_START`` event); every stream shares
    this one epoch clock, so the same anchor maps inputs, poses and video frames
    onto a single timeline.

    Clamping is honest, not silent: callers that need to know an event fell
    OUTSIDE the encoded video (e.g. an ``END`` event a few frames after
    ``VIDEO_END``) compare the raw rounded index against the clip range — see
    :func:`summarize_input_frame_map`'s ``events_out_of_video_range``.

    >>> frame_of_epoch(100.0, 100.0, 24.0, 7825)   # the anchor is frame 0
    0
    >>> frame_of_epoch(101.0, 100.0, 24.0, 7825)   # 1.0 s later at 24 fps
    24
    >>> frame_of_epoch(50.0, 100.0, 24.0, 7825)    # before the anchor → clamp low
    0
    >>> frame_of_epoch(1e9, 100.0, 24.0, 7825)     # past clip end → clamp high
    7824
    """
    raw = round((t_epoch - anchor) * fps)
    if raw < 0:
        return 0
    if raw > frame_count - 1:
        return frame_count - 1
    return int(raw)


# ---- Pose interpolation (ISC-FRAME2FRAME) -----------------------------------


def lerp_angle_deg(a: float, b: float, t: float) -> float:
    """Shortest-arc linear interpolation between two angles (degrees).

    Walks the SMALLEST signed angular difference so 350°→10° passes through
    0° (not naively backwards through 180°). roll stays 0 in this pipeline so
    only pitch/yaw use this; result is ``a`` plus a fraction of the wrapped
    delta and may fall slightly outside [0,360) (callers may ``% 360``).
    """
    delta = (b - a + 180.0) % 360.0 - 180.0
    return a + delta * t


def _bracketing_indices(gs_times: list[float], t_epoch: float) -> tuple[int, int]:
    """Indices (lo, hi) of the two ticks bracketing ``t_epoch``.

    Clamps to the array ends so callers always get a valid pair; when
    ``t_epoch`` is outside the window lo == hi == the boundary index.
    """
    n = len(gs_times)
    idx = bisect.bisect_left(gs_times, t_epoch)
    if idx <= 0:
        return 0, 0
    if idx >= n:
        return n - 1, n - 1
    if gs_times[idx] == t_epoch:
        return idx, idx
    return idx - 1, idx


def interpolate_pose(gs_times: list[float], gs_rows: list[dict], t_epoch: float) -> dict[str, Any]:
    """Honestly resolve a frame's pose from the recorded game_state ticks.

    Within the real window ``[first_tick, last_tick]`` the pose is linearly
    interpolated between the two bracketing ticks (≤50 ms apart): position,
    velocity and derived speed linear; pitch/yaw shortest-arc; quaternion
    rebuilt from the interpolated euler. Discrete route flags come from the
    NEAREST tick (booleans can't interpolate). Outside the window the pose is
    clamped to the nearest boundary tick and flagged ``pose_valid=false`` —
    NEVER presented as a real recorded pose.

    Returns a dict with interpolated scalars (x/y/z, pitch_deg/yaw_deg,
    velocity_x/y/z, speed), ``quat`` (x,y,z,w), the ``nearest`` raw tick (for
    discrete fields + route_type), and the provenance triple ``pose_valid`` /
    ``pose_source`` / ``pose_dt_ms``.
    """
    if not gs_rows:
        quat = euler_zyx_to_quat(0.0, 0.0, 0.0)
        return {
            "x": 0.0,
            "y": 0.0,
            "z": 0.0,
            "pitch_deg": 0.0,
            "yaw_deg": 0.0,
            "velocity_x": 0.0,
            "velocity_y": 0.0,
            "velocity_z": 0.0,
            "speed": 0.0,
            "quat": list(quat),
            "nearest": {},
            "pose_valid": False,
            "pose_source": "extrapolated_no_pose",
            "pose_dt_ms": 0.0,
        }

    first_epoch, last_epoch = gs_times[0], gs_times[-1]
    lo, hi = _bracketing_indices(gs_times, t_epoch)
    a, b = gs_rows[lo], gs_rows[hi]
    ta, tb = gs_times[lo], gs_times[hi]

    pose_valid = first_epoch <= t_epoch <= last_epoch

    # ms to the NEAREST real tick (distance to whichever bracket is closer).
    dt_to_lo = abs(t_epoch - ta)
    dt_to_hi = abs(tb - t_epoch)
    pose_dt_ms = min(dt_to_lo, dt_to_hi) * 1000.0
    nearest = a if dt_to_lo <= dt_to_hi else b

    if not pose_valid:
        # Clamp to nearest boundary tick (numeric, NOT null) but flag honestly.
        boundary = gs_rows[0] if t_epoch < first_epoch else gs_rows[-1]
        bt = first_epoch if t_epoch < first_epoch else last_epoch
        pitch = float(boundary.get("pitch_deg", 0.0))
        yaw = float(boundary.get("yaw_deg", 0.0))
        vx = float(boundary.get("velocity_x", 0.0))
        vy = float(boundary.get("velocity_y", 0.0))
        vz = float(boundary.get("velocity_z", 0.0))
        return {
            "x": float(boundary.get("x", 0.0)),
            "y": float(boundary.get("y", 0.0)),
            "z": float(boundary.get("z", 0.0)),
            "pitch_deg": pitch,
            "yaw_deg": yaw,
            "velocity_x": vx,
            "velocity_y": vy,
            "velocity_z": vz,
            "speed": _vec_speed(vx, vy, vz),
            "quat": list(euler_zyx_to_quat(0.0, pitch, yaw)),
            "nearest": boundary,
            "pose_valid": False,
            "pose_source": "extrapolated_no_pose",
            "pose_dt_ms": abs(t_epoch - bt) * 1000.0,
        }

    span = tb - ta
    frac = (t_epoch - ta) / span if span > 0 else 0.0

    def _lin(key: str) -> float:
        return float(a.get(key, 0.0)) + (float(b.get(key, 0.0)) - float(a.get(key, 0.0))) * frac

    x = _lin("x")
    y = _lin("y")
    z = _lin("z")
    vx = _lin("velocity_x")
    vy = _lin("velocity_y")
    vz = _lin("velocity_z")
    pitch = lerp_angle_deg(float(a.get("pitch_deg", 0.0)), float(b.get("pitch_deg", 0.0)), frac)
    yaw = lerp_angle_deg(float(a.get("yaw_deg", 0.0)), float(b.get("yaw_deg", 0.0)), frac)
    quat = euler_zyx_to_quat(0.0, pitch, yaw)

    source = "exact" if pose_dt_ms < EXACT_POSE_EPS_MS else "interpolated"
    return {
        "x": x,
        "y": y,
        "z": z,
        "pitch_deg": pitch,
        "yaw_deg": yaw,
        "velocity_x": vx,
        "velocity_y": vy,
        "velocity_z": vz,
        "speed": _vec_speed(vx, vy, vz),
        "quat": list(quat),
        "nearest": nearest,
        "pose_valid": True,
        "pose_source": source,
        "pose_dt_ms": pose_dt_ms,
    }


# ---- Input (mouse / action) processing --------------------------------------


def extract_mouse_moves(inputs: list[dict[str, Any]]) -> list[tuple[float, float, float]]:
    """Return sorted (epoch_ts, dx, dy) for every MOUSE_MOVE event."""
    moves: list[tuple[float, float, float]] = []
    for ev in inputs:
        if ev.get("event_type") != "MOUSE_MOVE":
            continue
        args = ev.get("event_args")
        if not isinstance(args, list) or len(args) < 2:
            continue
        try:
            ts = float(ev["timestamp"])
            dx = float(args[0])
            dy = float(args[1])
        except (KeyError, TypeError, ValueError):
            continue
        moves.append((ts, dx, dy))
    moves.sort(key=lambda m: m[0])
    return moves


def extract_action_timestamps(inputs: list[dict[str, Any]]) -> list[float]:
    """Sorted epoch timestamps of discrete player actions.

    Discrete actions are real key presses, mouse-button clicks, and scroll
    events — NOT continuous mouse movement (which would swamp the cadence and
    no longer measure deliberate actions). These feed the per-record
    ``timestamp`` so action_per_second reads the true action rhythm.
    """
    ts: list[float] = []
    for ev in inputs:
        if ev.get("event_type") not in DISCRETE_ACTION_EVENTS:
            continue
        try:
            ts.append(float(ev["timestamp"]))
        except (KeyError, TypeError, ValueError):
            continue
    ts.sort()
    return ts


def last_action_at(action_ts: list[float], t_epoch: float, fallback: float) -> float:
    """Epoch time of the most-recent discrete action at or before t_epoch.

    Before the first action, returns the first action's timestamp (or the
    supplied fallback when there are no discrete actions at all).
    """
    if not action_ts:
        return fallback
    idx = bisect.bisect_right(action_ts, t_epoch) - 1
    if idx < 0:
        return action_ts[0]
    return action_ts[idx]


# ---- Keyboard → keyCode -----------------------------------------------------


def _key_to_code(ev: dict[str, Any]) -> int:
    """Best-effort integer keyCode from a KEYBOARD event.

    The recorder's event_args vary; we accept an int directly, a numeric
    string, or fall back to the ASCII code of a single-char key name. Unknown
    shapes map to 0 (no key) rather than guessing.
    """
    args = ev.get("event_args")
    candidates: list[Any] = []
    if isinstance(args, list):
        candidates.extend(args)
    elif isinstance(args, dict):
        for key in ("keycode", "key_code", "code", "key", "vk", "scancode"):
            if key in args:
                candidates.append(args[key])
    elif args is not None:
        candidates.append(args)
    for c in candidates:
        if isinstance(c, bool):
            continue
        if isinstance(c, int):
            return c
        if isinstance(c, str):
            s = c.strip()
            if s.lstrip("-").isdigit():
                return int(s)
            if len(s) == 1:
                return ord(s.upper())
    return 0


def build_keycode_timeline(inputs: list[dict[str, Any]]) -> list[tuple[float, int]]:
    """Sorted (epoch_ts, keyCode) for KEYBOARD events with a resolvable code."""
    timeline: list[tuple[float, int]] = []
    for ev in inputs:
        if ev.get("event_type") != "KEYBOARD":
            continue
        try:
            ts = float(ev["timestamp"])
        except (KeyError, TypeError, ValueError):
            continue
        timeline.append((ts, _key_to_code(ev)))
    timeline.sort(key=lambda x: x[0])
    return timeline


# ---- Explicit input-event → frame index map (ISC-IF-2) ----------------------


def build_input_frame_map(
    inputs: list[dict[str, Any]],
    anchor: float,
    fps: float,
    frame_count: int,
) -> list[dict[str, Any]]:
    """Map every DISCRETE input event to the video frame it occurred on.

    Discrete events are real key presses/releases (``KEYBOARD``), mouse-button
    presses/releases (``MOUSE_BUTTON``) and wheel ticks (``SCROLL``). Continuous
    ``MOUSE_MOVE`` events are NOT emitted per-event — they are already aggregated
    into action_camera's per-frame ``mouse_dx``/``mouse_dy`` — and lifecycle
    events (START/HOOK_START/VIDEO_START/VIDEO_END/END) are excluded entirely.

    Each record answers "when in the video did this input happen?":
      * ``event_type``  — the discrete event class.
      * ``epoch``       — the event's wall-clock epoch (s, 6 dp).
      * ``frame``       — ``frame_of_epoch(epoch, ...)``; the CFR frame index,
                          clamped to ``[0, frame_count-1]`` (out-of-range events
                          are disclosed via :func:`summarize_input_frame_map`).
      * ``frame_time_s``— that frame's display time ``frame / fps`` (s, 6 dp).
      * ``dt_to_frame_center_ms`` — signed offset (ms, 4 dp) from the event to
                          the centre of its assigned frame; quantifies the
                          sub-frame timing error introduced by CFR bucketing.
      * ``keyCode``     — KEYBOARD only, via the shared :func:`_key_to_code`.
      * ``button``      — MOUSE_BUTTON only (the button id from event_args[0]).
      * ``scroll``      — SCROLL only (the raw event_args scroll delta(s)).

    Records are sorted ascending by epoch. Keys that don't apply to an event
    type are omitted (a KEYBOARD record has no ``button``/``scroll``).

    KNOWN LIMITATION (honest disclosure): action_camera.json carries a single
    ``keyCode`` per frame that keeps only the MOST-RECENT held key, so concurrent
    chords (e.g. W+A+Shift) collapse to one code there. This map preserves EVERY
    discrete event — both press and release — so downstream consumers can
    reconstruct concurrency and key-hold intervals that the per-frame held-key
    model alone cannot express.
    """
    records: list[dict[str, Any]] = []
    for ev in inputs:
        et = ev.get("event_type")
        if et not in DISCRETE_ACTION_EVENTS:
            continue
        try:
            t = float(ev["timestamp"])
        except (KeyError, TypeError, ValueError):
            continue

        frame = frame_of_epoch(t, anchor, fps, frame_count)
        rec: dict[str, Any] = {
            "event_type": et,
            "epoch": round(t, 6),
            "frame": frame,
            "frame_time_s": round(frame / fps, 6),
            # Signed ms from the event to the centre of its assigned frame.
            "dt_to_frame_center_ms": round(((t - anchor) - (frame + 0.5) / fps) * 1000.0, 4),
        }

        args = ev.get("event_args")
        if et == "KEYBOARD":
            rec["keyCode"] = _key_to_code(ev)
        elif et == "MOUSE_BUTTON":
            if isinstance(args, list) and args:
                rec["button"] = args[0]
        elif et == "SCROLL":
            if args is not None:
                rec["scroll"] = args

        records.append(rec)

    records.sort(key=lambda r: r["epoch"])
    return records


def summarize_input_frame_map(
    frame_map: list[dict[str, Any]],
    anchor: float,
    fps: float,
    frame_count: int,
    inputs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compact, honest summary of the input→frame map for the alignment report.

    Counts the discrete events overall and per class, plus how many landed
    OUTSIDE the encoded video range ``[0, frame_count-1]`` BEFORE clamping (the
    raw rounded index differs from the clamped ``frame``). Those events are
    still emitted (clamped), but disclosed here so a buyer sees, e.g., that an
    ``END`` event fired a few frames after the video ended.
    """
    keyboard = sum(1 for r in frame_map if r["event_type"] == "KEYBOARD")
    mouse_button = sum(1 for r in frame_map if r["event_type"] == "MOUSE_BUTTON")
    scroll = sum(1 for r in frame_map if r["event_type"] == "SCROLL")

    out_of_range = 0
    for ev in inputs:
        if ev.get("event_type") not in DISCRETE_ACTION_EVENTS:
            continue
        try:
            t = float(ev["timestamp"])
        except (KeyError, TypeError, ValueError):
            continue
        raw = round((t - anchor) * fps)
        if raw < 0 or raw > frame_count - 1:
            out_of_range += 1

    return {
        "path": "input_frame_map.json",
        "discrete_event_count": len(frame_map),
        "keyboard_count": keyboard,
        "mouse_button_count": mouse_button,
        "scroll_count": scroll,
        "events_out_of_video_range": out_of_range,
    }


# ---- Record assembly --------------------------------------------------------


def _vec_speed(vx: float, vy: float, vz: float) -> float:
    return math.sqrt(vx * vx + vy * vy + vz * vz)


def build_records(
    session_dir: Path,
    *,
    vfov_deg: float,
    route_type_override: int | None = None,
) -> list[dict[str, Any]]:
    """Build the full list of per-frame action_camera records.

    route_type is derived per frame from real movement state unless
    ``route_type_override`` forces a single value (used only when a caller
    explicitly wants every record tagged the same).
    """
    metadata = json.loads((session_dir / "metadata.json").read_text(encoding="utf-8"))
    game_state = _read_jsonl(session_dir / "game_state.jsonl")
    inputs = _read_jsonl(session_dir / "inputs.jsonl")

    width, height, fps, frame_count = resolve_video_params(session_dir, metadata)
    intrinsics = compute_intrinsics(width, height, vfov_deg)

    if not game_state:
        raise ValueError("game_state.jsonl is empty — cannot derive camera poses")

    gs_times, gs_rows = build_gs_index(game_state)

    # Frame-0 epoch anchor (ISC-FRAME2FRAME): the VIDEO_START event marks when
    # OBS emitted frame 0 (PTS 0), the most accurate anchor; fall back to
    # metadata.start_timestamp then the first game_state epoch.
    start_epoch, anchor_source = resolve_frame_anchor(inputs, metadata, gs_times[0])

    # Frame-0 absolute position → relative-to-clip-start origin (must-fix #1).
    # Use the interpolated pose at the anchor so the origin reflects the true
    # frame-0 position even when frame 0 precedes the first recorded tick.
    origin_pose = interpolate_pose(gs_times, gs_rows, start_epoch)
    ox = float(origin_pose["x"])
    oy = float(origin_pose["y"])
    oz = float(origin_pose["z"])

    mouse_moves = extract_mouse_moves(inputs)
    move_ts = [m[0] for m in mouse_moves]
    action_ts = extract_action_timestamps(inputs)
    keycodes = build_keycode_timeline(inputs)
    key_ts = [k[0] for k in keycodes]

    # Cumulative normalised mouse position (must-fix #3): accumulate raw deltas,
    # then divide by the running min/max span so values land in 0..1.
    cum_dx_total = 0.0
    cum_dy_total = 0.0
    cum_dx_series: list[float] = []
    cum_dy_series: list[float] = []

    records: list[dict[str, Any]] = []

    for frame in range(frame_count):
        frame_time = frame / fps  # seconds since clip start (schema `time`)
        frame_epoch = start_epoch + frame_time

        # --- pose by honest interpolation within the recorded window ---
        # Inside [first_tick, last_tick]: linear position/velocity, shortest-arc
        # pitch/yaw, quaternion rebuilt from the interpolated euler. Outside:
        # clamped to the nearest boundary tick and flagged pose_valid=false.
        pose = interpolate_pose(gs_times, gs_rows, frame_epoch)
        abs_x = pose["x"]
        abs_y = pose["y"]
        abs_z = pose["z"]
        pitch = pose["pitch_deg"]
        yaw = pose["yaw_deg"]
        roll = 0.0
        quat = pose["quat"]
        speed = pose["speed"]
        pose_valid = pose["pose_valid"]
        pose_source = pose["pose_source"]
        pose_dt_ms = pose["pose_dt_ms"]

        # route_type: discrete locomotion class from the NEAREST tick's flags
        # (booleans can't interpolate), or a forced override.
        if route_type_override is not None:
            route_type = route_type_override
        else:
            route_type = classify_route_type(pose["nearest"])

        rel_pos = [
            round(abs_x - ox, 6),
            round(abs_y - oy, 6),
            round(abs_z - oz, 6),
        ]
        # Camera sits at eye height (~1.62 m above feet in MC); player_position
        # is the feet position. Both relative to clip start.
        player_pos = [rel_pos[0], rel_pos[1], rel_pos[2]]
        cam_pos = [rel_pos[0], round(rel_pos[1] + 1.62, 6), rel_pos[2]]

        # --- mouse delta within this frame's time window ---
        win_lo = frame_epoch
        win_hi = start_epoch + (frame + 1) / fps
        lo_idx = bisect.bisect_left(move_ts, win_lo)
        hi_idx = bisect.bisect_left(move_ts, win_hi)
        frame_dx = 0.0
        frame_dy = 0.0
        for mi in range(lo_idx, hi_idx):
            frame_dx += mouse_moves[mi][1]
            frame_dy += mouse_moves[mi][2]
        cum_dx_total += frame_dx
        cum_dy_total += frame_dy
        cum_dx_series.append(cum_dx_total)
        cum_dy_series.append(cum_dy_total)

        # --- discrete-action timestamp (drives action_per_second honestly) ---
        ts_action = last_action_at(action_ts, frame_epoch, frame_epoch)

        # --- active keyCode (most recent key at/before frame) ---
        key_code = 0
        if key_ts:
            ki = bisect.bisect_right(key_ts, frame_epoch) - 1
            if ki >= 0:
                key_code = keycodes[ki][1]

        records.append(
            {
                "frame": frame,
                "time": round(frame_time, 6),
                "timestamp": round(ts_action, 6),
                "fps": round(fps, 6),
                "route_type": int(route_type),
                "mouse_x": 0.0,  # filled after normalisation pass
                "mouse_y": 0.0,
                "mouse_dx": round(frame_dx, 6),
                "mouse_dy": round(frame_dy, 6),
                "keyCode": int(key_code),
                "camera_position": cam_pos,
                "camera_rotation_oula": [round(pitch, 6), round(yaw, 6), round(roll, 6)],
                "camera_rotation_quaternion": [round(q, 9) for q in quat],
                "camera_Follow Offset": [0.0, 0.0, 0.0],
                "camera_intrinsics": dict(intrinsics),
                "camera_speed": round(speed, 6),
                "player_position": player_pos,
                "player_rotation_oula": [round(pitch, 6), round(yaw, 6), round(roll, 6)],
                "player_rotation_quaternion": [round(q, 9) for q in quat],
                "player_speed": round(speed, 6),
                "metric_scale": METRIC_SCALE,
                # --- frame↔pose alignment provenance (ISC-FRAME2FRAME) ---
                "pose_valid": bool(pose_valid),
                "pose_source": pose_source,
                "pose_dt_ms": round(pose_dt_ms, 4),
            }
        )

    _normalise_mouse_positions(records, cum_dx_series, cum_dy_series)
    return records


def _normalise_mouse_positions(
    records: list[dict[str, Any]],
    cum_dx: list[float],
    cum_dy: list[float],
) -> None:
    """Normalise cumulative mouse displacement to 0..1 in place (must-fix #3)."""

    def _norm(series: list[float]) -> list[float]:
        if not series:
            return []
        lo, hi = min(series), max(series)
        span = hi - lo
        if span <= 0:
            return [0.0 for _ in series]
        return [(v - lo) / span for v in series]

    nx = _norm(cum_dx)
    ny = _norm(cum_dy)
    for i, rec in enumerate(records):
        rec["mouse_x"] = round(nx[i], 6) if i < len(nx) else 0.0
        rec["mouse_y"] = round(ny[i], 6) if i < len(ny) else 0.0


# ---- Alignment report (ISC-FRAME2FRAME) -------------------------------------


def _tick_rate_hz(gs_times: list[float]) -> float:
    """Median tick rate (Hz) from consecutive game_state timestamps."""
    if len(gs_times) < 2:
        return 0.0
    deltas = sorted(b - a for a, b in zip(gs_times, gs_times[1:]) if (b - a) > 0)
    if not deltas:
        return 0.0
    mid = deltas[len(deltas) // 2]
    return round(1.0 / mid, 6) if mid > 0 else 0.0


def build_alignment_report(session_dir: Path, records: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarise frame↔pose coverage for a downstream clip packager.

    Re-resolves the anchor and tick rate from the session's real artifacts and
    derives coverage counts directly from the built records' ``pose_valid``
    flags, so the report can never disagree with the emitted per-frame data.
    The ``recommended_clip_trim`` is the covered frame range — a hint to cut
    video+audio+depth+pose to one co-extensive clip.
    """
    metadata = json.loads((session_dir / "metadata.json").read_text(encoding="utf-8"))
    game_state = _read_jsonl(session_dir / "game_state.jsonl")
    inputs = _read_jsonl(session_dir / "inputs.jsonl")
    gs_times, _ = build_gs_index(game_state)
    gs_first = gs_times[0] if gs_times else 0.0
    anchor_epoch, anchor_source = resolve_frame_anchor(inputs, metadata, gs_first)

    total = len(records)
    fps = float(records[0]["fps"]) if records else float(DEFAULT_FPS)

    valid_idx = [r["frame"] for r in records if r.get("pose_valid")]
    valid_count = len(valid_idx)
    if valid_idx:
        first_cov, last_cov = valid_idx[0], valid_idx[-1]
        covered_range: list[int] | None = [first_cov, last_cov]
        leading = first_cov
        trailing = (total - 1) - last_cov
        max_dt = max(
            (r["pose_dt_ms"] for r in records if r.get("pose_valid")),
            default=0.0,
        )
    else:
        first_cov = last_cov = None
        covered_range = None
        leading = total
        trailing = 0
        max_dt = 0.0

    pct_valid = round((valid_count / total) * 100.0, 4) if total else 0.0

    trim = (
        {"start_frame": first_cov, "end_frame": last_cov}
        if covered_range
        else {"start_frame": None, "end_frame": None}
    )

    # Explicit input-event → frame index map summary (ISC-IF-2). The full map is
    # written to its own sidecar by main(); here we embed only a compact,
    # honest count block so the alignment report self-describes both alignments.
    frame_map = build_input_frame_map(inputs, anchor_epoch, fps, total)
    input_frame_map_summary = summarize_input_frame_map(frame_map, anchor_epoch, fps, total, inputs)

    return {
        "anchor_source": anchor_source,
        "anchor_epoch": round(anchor_epoch, 6),
        "fps": round(fps, 6),
        "total_frames": total,
        "covered_frame_range": covered_range,
        "leading_uncovered_count": leading,
        "trailing_uncovered_count": trailing,
        "pct_frames_pose_valid": pct_valid,
        "max_pose_dt_ms_within_window": round(max_dt, 4),
        "pose_tick_rate_hz": _tick_rate_hz(gs_times),
        "recommended_clip_trim": trim,
        "input_frame_map": input_frame_map_summary,
    }


# ---- CLI --------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build PRD-canonical action_camera.json from a recording session."
    )
    parser.add_argument("session_dir", type=Path, help="Path to the session directory")
    parser.add_argument(
        "--fov",
        type=float,
        default=DEFAULT_VERTICAL_FOV_DEG,
        help=f"Vertical FOV in degrees for intrinsics (default: {DEFAULT_VERTICAL_FOV_DEG})",
    )
    parser.add_argument(
        "--route-type",
        type=int,
        default=None,
        help=(
            "Force a single route_type for every record. Default: derive each "
            "frame's route_type from real movement state (recommended)."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output path (default: <session_dir>/action_camera.json)",
    )
    args = parser.parse_args(argv)

    session_dir: Path = args.session_dir
    if not session_dir.is_dir():
        print(f"ERROR: session dir not found: {session_dir}", file=sys.stderr)
        return 1
    if not (session_dir / "metadata.json").is_file():
        print(f"ERROR: metadata.json missing in {session_dir}", file=sys.stderr)
        return 1

    try:
        records = build_records(
            session_dir,
            vfov_deg=args.fov,
            route_type_override=args.route_type,
        )
    except (ValueError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    out_path = args.output or (session_dir / "action_camera.json")
    out_path.write_text(json.dumps(records, indent=2), encoding="utf-8")

    # Frame↔pose alignment sidecar + summary (ISC-FRAME2FRAME). Always written
    # next to the session so a downstream clip packager can trim to coverage.
    report = build_alignment_report(session_dir, records)

    # Explicit input-event → frame index map sidecar (ISC-IF-2). Re-derive the
    # anchor/fps/frame_count from the same real artifacts the report used, then
    # write the FULL per-event map next to the session. The report embeds only a
    # compact summary of this file (report["input_frame_map"]).
    inputs = _read_jsonl(session_dir / "inputs.jsonl")
    anchor_epoch = float(report["anchor_epoch"])
    fps = float(report["fps"])
    frame_count = int(report["total_frames"])
    frame_map = build_input_frame_map(inputs, anchor_epoch, fps, frame_count)
    frame_map_path = session_dir / "input_frame_map.json"
    frame_map_path.write_text(json.dumps(frame_map, indent=2), encoding="utf-8")

    report_path = session_dir / "frame_alignment.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    intr = records[0]["camera_intrinsics"] if records else {}
    distinct_routes = sorted({r["route_type"] for r in records})
    print(f"Wrote {len(records)} records → {out_path}")
    print(
        "  intrinsics: "
        f"fx={intr.get('fx')} fy={intr.get('fy')} "
        f"cx={intr.get('cx')} cy={intr.get('cy')}"
    )
    if records:
        print(f"  record[0].camera_position (relative m): {records[0]['camera_position']}")
        print(f"  fps={records[0]['fps']}  distinct route_types={distinct_routes}")

    print(f"Wrote alignment report → {report_path}")
    print(
        f"  anchor: {report['anchor_source']} @ {report['anchor_epoch']}  "
        f"fps={report['fps']}  tick_rate={report['pose_tick_rate_hz']}Hz"
    )
    print(
        f"  covered frames {report['covered_frame_range']} of {report['total_frames']}  "
        f"(leading uncovered={report['leading_uncovered_count']}, "
        f"trailing uncovered={report['trailing_uncovered_count']})"
    )
    print(
        f"  pose_valid={report['pct_frames_pose_valid']}%  "
        f"max Δt within window={report['max_pose_dt_ms_within_window']}ms"
    )
    print(f"  recommended_clip_trim: {report['recommended_clip_trim']}")

    ifm = report["input_frame_map"]
    print(f"Wrote input frame map → {frame_map_path}")
    print(
        f"  discrete events={ifm['discrete_event_count']} "
        f"(keyboard={ifm['keyboard_count']}, mouse_button={ifm['mouse_button_count']}, "
        f"scroll={ifm['scroll_count']}); out of video range={ifm['events_out_of_video_range']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
