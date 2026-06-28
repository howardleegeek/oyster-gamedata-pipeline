#!/usr/bin/env python3
"""Transform Fabric mod's `game_state.jsonl` into PRD-compliant
`action_camera.json` with 20 literal-named fields.

The Fabric mod records 19 fields per tick (tick, x/y/z, yaw/pitch, look_xyz,
velocity_xyz, on_ground/sneaking/sprinting, paused, dimension, game_mode). The PRD
buyer-pipeline requires 20 named fields per row of action_camera.json:

  frame, time, fps, route_type,
  mouse_x, mouse_y, mouse_dx, mouse_dy,
  keyCode,
  camera_position, camera_rotation_oula, camera_rotation_quaternion,
  camera_Follow Offset (literal space + capital F), camera_intrinsics,
  camera_speed,
  player_position, player_rotation_oula, player_rotation_quaternion,
  player_speed,
  metric_scale.

This script does the structural transformation. Inputs.jsonl (Win32
keyboard/mouse) is merged when present; missing fields are filled with
PRD-acceptable defaults (mouse_x/y from metadata-normalized absolute cursor
coordinates when present, otherwise yaw/pitch fallback, keyCode=0,
camera_intrinsics defaults from 1920x1080 @ 70° FOV, etc.).

Usage:
  python3 bin/transform_game_state_to_action_camera.py <session_dir>

Reads <session_dir>/game_state.jsonl (and optionally inputs.jsonl) and
writes <session_dir>/action_camera.json with the 20 PRD fields.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

# ─── PRD constants ────────────────────────────────────────────────────────
MC_TICKS_PER_SECOND = 20.0  # MC native TPS; convert tick→video frame
VIDEO_FPS = 30.0  # PRD-mandated video fps
MC_BLOCKS_TO_METERS = 1.0  # 1 block = 1 meter in MC
MC_GRAVITY_MPS2 = 32.0  # PRD §4.2 (MC physics: 32 m/s² not Earth's 9.8)
DEFAULT_FOV_DEG = 70.0  # MC default field of view

# Camera intrinsics for 1920×1080 @ 70° FOV — derive fx, fy, Cx, Cy
# fx = fy = (W/2) / tan(fov/2);  Cx = W/2; Cy = H/2
_W, _H = 1920, 1080
DEFAULT_SCREEN_WIDTH = _W
DEFAULT_SCREEN_HEIGHT = _H
_FOV_RAD = math.radians(DEFAULT_FOV_DEG)
_FX = (_W / 2.0) / math.tan(_FOV_RAD / 2.0)
DEFAULT_INTRINSICS = {
    "fx": round(_FX, 3),
    "fy": round(_FX, 3),
    "Cx": _W / 2.0,  # literal capital — PRD iron-law
    "Cy": _H / 2.0,
}

_ABS_UNIX_SECONDS_FLOOR = 1_000_000_000.0
_ABS_UNIX_MILLISECONDS_FLOOR = 1_000_000_000_000.0
_ABS_UNIX_NANOSECONDS_FLOOR = 1_000_000_000_000_000_000.0


def _first_number(data: dict, keys: tuple[str, ...]) -> float | None:
    """Return the first numeric field value found under any of ``keys``."""
    for key in keys:
        value = data.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _event_time_relative_seconds(ev: dict, session_start_unix: float | None) -> float | None:
    """Normalize supported input timestamp shapes to session-relative seconds."""

    ts = ev.get("timestamp")
    if isinstance(ts, (int, float)):
        ts_s = float(ts)
        if session_start_unix is not None and ts_s >= _ABS_UNIX_SECONDS_FLOOR:
            return ts_s - session_start_unix
        return ts_s

    ts_ms = ev.get("timestamp_ms")
    if isinstance(ts_ms, (int, float)):
        ts_s = float(ts_ms) / 1000.0
        if session_start_unix is not None and float(ts_ms) >= _ABS_UNIX_MILLISECONDS_FLOOR:
            return ts_s - session_start_unix
        return ts_s

    ts_ns = ev.get("timestamp_ns")
    if isinstance(ts_ns, (int, float)):
        ts_s = float(ts_ns) / 1_000_000_000.0
        if session_start_unix is not None and float(ts_ns) >= _ABS_UNIX_NANOSECONDS_FLOOR:
            return ts_s - session_start_unix
        return ts_s

    return None


def _extract_mouse_delta(ev: dict) -> tuple[float, float] | None:
    """Extract relative mouse deltas from canonical and raw-input event shapes."""

    dx = ev.get("mouse_dx")
    dy = ev.get("mouse_dy")
    if dx is None or dy is None:
        dx = ev.get("dx")
        dy = ev.get("dy")
    if dx is None or dy is None:
        ea = ev.get("event_args")
        if isinstance(ea, list) and len(ea) >= 2:
            dx = ea[0]
            dy = ea[1]
    if isinstance(dx, (int, float)) and isinstance(dy, (int, float)):
        return float(dx), float(dy)
    return None


def _truthy_bool(value: Any) -> bool:
    """Coerce recorder JSON bool-ish values without treating arbitrary text as true."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return False


def _unit_interval(value: float) -> float:
    return max(0.0, min(1.0, value))


def _normalize_pixel_coordinate(value: float, extent: int | float) -> float:
    return _unit_interval(value / max(float(extent), 1.0))


def _parse_resolution_value(value: Any) -> tuple[int, int] | None:
    """Parse common metadata resolution shapes into ``(width, height)``."""
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        width, height = value[0], value[1]
    elif isinstance(value, dict):
        width = value.get("width", value.get("w"))
        height = value.get("height", value.get("h"))
    elif isinstance(value, str):
        parts = value.lower().replace(" ", "").split("x", 1)
        if len(parts) != 2:
            return None
        width, height = parts
    else:
        return None

    try:
        parsed_width = int(float(width))
        parsed_height = int(float(height))
    except (TypeError, ValueError):
        return None
    if parsed_width <= 0 or parsed_height <= 0:
        return None
    return parsed_width, parsed_height


def load_game_resolution(session: Path) -> tuple[int, int]:
    """Read metadata.json.game_resolution, falling back to 1920x1080."""
    metadata_path = session / "metadata.json"
    if not metadata_path.is_file():
        return DEFAULT_SCREEN_WIDTH, DEFAULT_SCREEN_HEIGHT
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DEFAULT_SCREEN_WIDTH, DEFAULT_SCREEN_HEIGHT
    if not isinstance(metadata, dict):
        return DEFAULT_SCREEN_WIDTH, DEFAULT_SCREEN_HEIGHT

    for key in ("game_resolution", "capture_resolution", "screen_resolution", "resolution"):
        parsed = _parse_resolution_value(metadata.get(key))
        if parsed is not None:
            return parsed
    return DEFAULT_SCREEN_WIDTH, DEFAULT_SCREEN_HEIGHT


def euler_to_quat_xyzw(yaw_deg: float, pitch_deg: float, roll_deg: float = 0.0) -> list[float]:
    """Convert MC's (yaw, pitch, roll) to quaternion in [x, y, z, w] order.

    MC convention: yaw=0 facing south, positive yaw turns clockwise (left-hand).
    Outputs unit quaternion. roll defaults to 0 since MC doesn't roll camera.
    """
    yaw_r = math.radians(yaw_deg)
    pitch_r = math.radians(pitch_deg)
    roll_r = math.radians(roll_deg)
    cy, sy = math.cos(yaw_r * 0.5), math.sin(yaw_r * 0.5)
    cp, sp = math.cos(pitch_r * 0.5), math.sin(pitch_r * 0.5)
    cr, sr = math.cos(roll_r * 0.5), math.sin(roll_r * 0.5)
    qw = cr * cp * cy + sr * sp * sy
    qx = sr * cp * cy - cr * sp * sy
    qy = cr * sp * cy + sr * cp * sy
    qz = cr * cp * sy - sr * sp * cy
    return [qx, qy, qz, qw]


def transform_tick_to_action_camera_row(
    tick_data: dict,
    frame_idx: int,
    eye_offset_y: float = 1.62,
    metric_scale: float = MC_BLOCKS_TO_METERS,
    route_type: int = 1,
    screen_width: int = DEFAULT_SCREEN_WIDTH,
    screen_height: int = DEFAULT_SCREEN_HEIGHT,
) -> dict:
    """Convert one game_state.jsonl tick into a PRD-20-field action_camera row.

    Args:
        tick_data: a single tick from game_state.jsonl
        frame_idx: video frame index (0..N-1)
        eye_offset_y: MC player eye is 1.62 blocks above feet
        metric_scale: PRD metric_scale, default 1.0 for MC
        route_type: PRD route_type ∈ {1, 2, 3}

    Returns dict with 20 PRD literal-named fields.
    """
    x = float(tick_data.get("x", 0.0))
    y = float(tick_data.get("y", 0.0))
    z = float(tick_data.get("z", 0.0))
    yaw = float(tick_data.get("yaw_deg", 0.0)) % 360.0  # wrap to [0, 360)
    pitch = max(-90.0, min(90.0, float(tick_data.get("pitch_deg", 0.0))))  # clamp
    vx = float(tick_data.get("velocity_x", 0.0)) * MC_TICKS_PER_SECOND  # tick→sec
    vy = float(tick_data.get("velocity_y", 0.0)) * MC_TICKS_PER_SECOND
    vz = float(tick_data.get("velocity_z", 0.0)) * MC_TICKS_PER_SECOND
    paused = _truthy_bool(tick_data.get("paused", tick_data.get("is_paused", False)))

    # Camera position = player position + eye offset (only Y)
    cam_x, cam_y, cam_z = x, y + eye_offset_y, z

    # Camera rotation as oula (literal 拼音, NOT euler) — PRD D10 convention:
    # oula[0] = pitch ∈ [-90, 90], oula[1] = yaw ∈ [-180, 180], oula[2] = roll ∈ [-180, 180]
    # Wrap yaw to [-180, 180] (signed) instead of [0, 360) so D10 audit passes.
    yaw_signed = yaw if yaw <= 180.0 else yaw - 360.0
    cam_rot_oula = [pitch, yaw_signed, 0.0]
    player_rot_oula = list(cam_rot_oula)  # in MC, head and body share rotation

    # Quaternions (unit, xyzw order)
    cam_quat = euler_to_quat_xyzw(yaw, pitch, 0.0)
    player_quat = list(cam_quat)

    # Prefer absolute cursor pixel coordinates from game_state.jsonl when the
    # recorder/mod provides them. PRD D5 requires normalized [0,1] values.
    # Older Minecraft captures have no cursor fields, so keep the yaw/pitch
    # fallback for backward-compatible real-session audits.
    raw_mouse_x = _first_number(tick_data, ("mouse_x", "mouseX"))
    raw_mouse_y = _first_number(tick_data, ("mouse_y", "mouseY"))
    if raw_mouse_x is not None:
        mouse_x = _normalize_pixel_coordinate(raw_mouse_x, screen_width)
    else:
        mouse_x = _unit_interval((yaw % 360.0) / 360.0)
    if raw_mouse_y is not None:
        mouse_y = _normalize_pixel_coordinate(raw_mouse_y, screen_height)
    else:
        mouse_y = _unit_interval((pitch + 90.0) / 180.0)

    # Time = tick / 20 (MC TPS)
    time_s = frame_idx / VIDEO_FPS

    return {
        "frame": frame_idx,
        "time": round(time_s, 6),
        "fps": VIDEO_FPS,
        "route_type": route_type,
        "mouse_x": round(mouse_x, 6),
        "mouse_y": round(mouse_y, 6),
        "mouse_dx": 0.0,  # filled if inputs.jsonl present
        "mouse_dy": 0.0,
        "keyCode": 0,  # filled if inputs.jsonl present
        "camera_position": [cam_x, cam_y, cam_z],
        "camera_rotation_oula": cam_rot_oula,
        "camera_rotation_quaternion": cam_quat,
        "camera_Follow Offset": [0.0, 0.0, 0.0],  # literal SPACE + capital F
        "camera_intrinsics": DEFAULT_INTRINSICS,
        "camera_speed": [vx, vy, vz],  # m/s
        "player_position": [x, y, z],
        "player_rotation_oula": player_rot_oula,
        "player_rotation_quaternion": player_quat,
        "player_speed": [vx, vy, vz],  # in MC, head and body move together
        "metric_scale": metric_scale,
        "_paused": paused,
    }


def resample_to_video_grid(ticks: list[dict], target_count: int = 9000) -> list[dict]:
    """Resample variable-rate game_state ticks onto a uniform 30 fps grid.

    PRD requires exactly 9000 frames (5 min × 30 fps). Game ticks are 20 TPS
    in MC, so 5 min = 6000 ticks. We linearly interpolate position fields,
    nearest-neighbor for discrete fields, on a uniform 30 fps grid.

    Args:
        ticks: list of game_state.jsonl entries (sorted by timestamp_ms)
        target_count: number of output frames (PRD default 9000)
    """
    if not ticks:
        return []
    if len(ticks) < 2:
        return ticks * target_count

    t_min = ticks[0]["timestamp_ms"]
    t_max = ticks[-1]["timestamp_ms"]
    span_ms = t_max - t_min
    if span_ms <= 0:
        return ticks[:target_count] or [ticks[0]] * target_count

    # Build sorted tick array by timestamp
    tick_times = [t["timestamp_ms"] for t in ticks]

    output = []
    for i in range(target_count):
        target_t = t_min + (span_ms * i / max(1, target_count - 1))
        # Binary search for nearest tick
        import bisect

        pos = bisect.bisect_left(tick_times, target_t)
        if pos == 0:
            output.append(dict(ticks[0]))
        elif pos >= len(ticks):
            output.append(dict(ticks[-1]))
        else:
            t_before = tick_times[pos - 1]
            t_after = tick_times[pos]
            # Lerp continuous fields
            alpha = (target_t - t_before) / (t_after - t_before) if t_after > t_before else 0
            row = dict(ticks[pos - 1])  # discrete fields from earlier tick (nearest-neighbor)
            for key in (
                "x",
                "y",
                "z",
                "yaw_deg",
                "pitch_deg",
                "look_x",
                "look_y",
                "look_z",
                "velocity_x",
                "velocity_y",
                "velocity_z",
            ):
                a = ticks[pos - 1].get(key, 0)
                b = ticks[pos].get(key, 0)
                row[key] = a + alpha * (b - a)
            row["timestamp_ms"] = target_t
            output.append(row)
    return output


def merge_inputs(
    action_camera_rows: list[dict], inputs_path: Path, session_start_unix: float = None
) -> int:
    """Overlay mouse_dx/dy + keyCode from inputs.jsonl onto action_camera rows.

    Bug-fix 2026-05-17 (precision audit P2): previous implementation had TWO
    bugs that caused mouse_dx to be all-zero in action_camera.json output:
      1. Field name: looked for nearest.get("dx") but recorder writes
         {event_type: "MOUSE_MOVE", event_args: [dx, dy]}. Now handles BOTH
         the denormalized-form (top-level mouse_dx/mouse_dy after canonical
         pipeline step4) AND the raw-form (event_args list).
      2. Time reference frame: action_camera row["time"] is 0..300s relative
         to session start; events have absolute Unix epoch timestamps. They
         must be brought to the same reference frame before nearest-lookup.
         Use session_start_unix (game_state[0].timestamp_ms / 1e3) as the
         anchor.

    Returns count of rows updated.

    inputs.jsonl format (recorder, post-denormalize):
      {"timestamp": <unix>, "timestamp_ns": <unix*1e9>, "event_type": "MOUSE_MOVE",
       "event_args": [dx, dy], "mouse_dx": <dx>, "mouse_dy": <dy>}
      {"timestamp_ms": <relative_ms>, "event_type": "mouse_raw_delta", "dx": <dx>, "dy": <dy>}
      {"timestamp": <unix>, "event_type": "KEYBOARD", "vk_code": 87, "pressed": true}
    """
    if not inputs_path.exists():
        return 0
    events = []
    with inputs_path.open(encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if not events:
        return 0

    # Establish session_start anchor if not provided. Falls back to first
    # input event timestamp (less accurate — may include pre-game lifecycle).
    if session_start_unix is None:
        # Use first absolute-timestamp gameplay event (skip lifecycle markers).
        # Relative timestamp_ms events are already session-relative.
        gameplay = [
            e
            for e in events
            if e.get("event_type") in ("MOUSE_MOVE", "KEYBOARD", "MOUSE_BUTTON", "mouse_raw_delta")
        ]
        absolute_starts = []
        for e in gameplay:
            ts = e.get("timestamp")
            if isinstance(ts, (int, float)) and float(ts) >= _ABS_UNIX_SECONDS_FLOOR:
                absolute_starts.append(float(ts))
            ts_ms = e.get("timestamp_ms")
            if isinstance(ts_ms, (int, float)) and float(ts_ms) >= _ABS_UNIX_MILLISECONDS_FLOOR:
                absolute_starts.append(float(ts_ms) / 1000.0)
            ts_ns = e.get("timestamp_ns")
            if isinstance(ts_ns, (int, float)) and float(ts_ns) >= _ABS_UNIX_NANOSECONDS_FLOOR:
                absolute_starts.append(float(ts_ns) / 1_000_000_000.0)
        if absolute_starts:
            session_start_unix = min(absolute_starts)
        elif gameplay:
            session_start_unix = None
        else:
            return 0

    # Index mouse-delta events by their session-relative time (seconds)
    mouse_events_by_t = []  # list of (t_rel_seconds, dx, dy)
    keyboard_events_by_t = []  # list of (t_rel_seconds, vk_code, pressed)
    for ev in events:
        et = ev.get("event_type")
        t_rel = _event_time_relative_seconds(ev, session_start_unix)
        if t_rel is None:
            continue
        if t_rel < 0:
            continue  # skip pre-session events
        if et in ("MOUSE_MOVE", "mouse_raw_delta"):
            delta = _extract_mouse_delta(ev)
            if delta is not None:
                mouse_events_by_t.append((t_rel, delta[0], delta[1]))
        elif et == "KEYBOARD":
            vk = ev.get("vk_code")
            pressed = ev.get("pressed")
            if vk is None:
                ea = ev.get("event_args")
                if isinstance(ea, list) and len(ea) >= 2:
                    vk = ea[0]
                    pressed = ea[1]
            if isinstance(vk, int) and pressed:
                keyboard_events_by_t.append((t_rel, vk, True))

    if not mouse_events_by_t and not keyboard_events_by_t:
        return 0

    # Sort once
    mouse_events_by_t.sort(key=lambda x: x[0])
    keyboard_events_by_t.sort(key=lambda x: x[0])

    # For each action_camera row, accumulate mouse_dx/dy in the 33ms window
    # (one frame at 30fps) and pick the most recent keyboard press if any.
    frame_dur = 1.0 / 30.0
    updated = 0
    m_idx = 0
    k_idx = 0
    for row in action_camera_rows:
        t = row.get("time", 0.0)
        if not isinstance(t, (int, float)):
            continue
        t_start = t
        t_end = t + frame_dur

        # Sum mouse_dx/dy in [t_start, t_end)
        sum_dx = 0.0
        sum_dy = 0.0
        # Advance m_idx forward past events earlier than t_start
        while m_idx < len(mouse_events_by_t) and mouse_events_by_t[m_idx][0] < t_start:
            m_idx += 1
        # Sum events in [t_start, t_end)
        j = m_idx
        while j < len(mouse_events_by_t) and mouse_events_by_t[j][0] < t_end:
            sum_dx += mouse_events_by_t[j][1]
            sum_dy += mouse_events_by_t[j][2]
            j += 1

        if sum_dx != 0.0 or sum_dy != 0.0:
            row["mouse_dx"] = sum_dx
            row["mouse_dy"] = sum_dy
            updated += 1

        # Find most recent keyboard press within [t_start, t_end)
        while k_idx < len(keyboard_events_by_t) and keyboard_events_by_t[k_idx][0] < t_start:
            k_idx += 1
        j = k_idx
        last_vk = 0
        while j < len(keyboard_events_by_t) and keyboard_events_by_t[j][0] < t_end:
            last_vk = keyboard_events_by_t[j][1]
            j += 1
        if last_vk:
            row["keyCode"] = last_vk

    return updated


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: transform_game_state_to_action_camera.py <session_dir>", file=sys.stderr)
        return 2
    session = Path(argv[1])
    gs_path = session / "game_state.jsonl"
    if not gs_path.is_file():
        print(f"FATAL: {gs_path} not found", file=sys.stderr)
        return 2

    ticks = []
    with gs_path.open(encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            try:
                ticks.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(f"WARN: skipping malformed line: {exc}", file=sys.stderr)
    if not ticks:
        print("FATAL: no game_state ticks", file=sys.stderr)
        return 2
    print(f"[transform] loaded {len(ticks)} ticks")

    # Resample onto 30 fps grid (9000 frames for 5-min recording)
    target = 9000
    if len(ticks) < 100:
        target = len(ticks)  # don't synthetically inflate short sessions
    resampled = resample_to_video_grid(ticks, target)
    print(f"[transform] resampled to {len(resampled)} rows ({target} target)")

    # Convert each tick → action_camera row
    screen_width, screen_height = load_game_resolution(session)
    print(
        f"[transform] using game_resolution {screen_width}x{screen_height} for mouse normalization"
    )
    rows = [
        transform_tick_to_action_camera_row(
            t,
            i,
            screen_width=screen_width,
            screen_height=screen_height,
        )
        for i, t in enumerate(resampled)
    ]

    # Optional: merge inputs.jsonl
    inputs_path = session / "inputs.jsonl"
    if inputs_path.exists():
        # Anchor the input-time reference to game_state[0] so action_camera
        # row times (0..300s) align with input event timestamps (Unix epoch).
        # Bug-fix 2026-05-17 — without this anchor, mouse_dx was zero in every
        # action_camera row because the nearest-event lookup compared
        # time=0..300 (relative) against timestamp=~1778e9 (absolute Unix).
        session_start_unix = ticks[0].get("timestamp_ms", 0) / 1000.0
        n = merge_inputs(rows, inputs_path, session_start_unix=session_start_unix)
        print(
            f"[transform] merged {n} input events (anchored to game_state t0={session_start_unix:.3f})"
        )

    # Write action_camera.json
    out_path = session / "action_camera.json"
    out_path.write_text(json.dumps(rows, indent=2))
    print(f"[transform] wrote {out_path} ({len(rows)} rows, {out_path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
