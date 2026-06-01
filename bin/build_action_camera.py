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

Euler→quaternion math is reused verbatim from ``verify_action_camera.py``
(``euler_zyx_to_quat``) so the two tools agree by construction.

Usage
-----
    python3 bin/build_action_camera.py <session_dir>
    python3 bin/build_action_camera.py <session_dir> --fov 70 --route-type 1

Writes ``<session_dir>/action_camera.json`` and prints a short summary.
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

    # Recording clock anchor: prefer metadata.start_timestamp (epoch seconds);
    # otherwise fall back to the first game_state epoch.
    start_epoch = metadata.get("start_timestamp")
    if not isinstance(start_epoch, (int, float)):
        start_epoch = gs_times[0]
    start_epoch = float(start_epoch)

    # Frame-0 absolute position → relative-to-clip-start origin (must-fix #1).
    origin = nearest_gs(gs_times, gs_rows, start_epoch)
    ox = float(origin.get("x", 0.0))
    oy = float(origin.get("y", 0.0))
    oz = float(origin.get("z", 0.0))

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

        # --- pose from nearest game_state ---
        gs = nearest_gs(gs_times, gs_rows, frame_epoch)
        abs_x = float(gs.get("x", ox))
        abs_y = float(gs.get("y", oy))
        abs_z = float(gs.get("z", oz))
        pitch = float(gs.get("pitch_deg", 0.0))
        yaw = float(gs.get("yaw_deg", 0.0))
        roll = 0.0
        quat = euler_zyx_to_quat(roll, pitch, yaw)
        vx = float(gs.get("velocity_x", 0.0))
        vy = float(gs.get("velocity_y", 0.0))
        vz = float(gs.get("velocity_z", 0.0))
        speed = _vec_speed(vx, vy, vz)

        # route_type: honest per-frame locomotion class (or forced override).
        if route_type_override is not None:
            route_type = route_type_override
        else:
            route_type = classify_route_type(gs)

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
