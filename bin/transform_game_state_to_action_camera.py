#!/usr/bin/env python3
"""Transform Fabric mod's `game_state.jsonl` into PRD-compliant
`action_camera.json` with 20 literal-named fields.

The Fabric mod records 18 fields per tick (tick, x/y/z, yaw/pitch, look_xyz,
velocity_xyz, on_ground/sneaking/sprinting, dimension, game_mode). The PRD
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
PRD-acceptable defaults (mouse_x/y from look_vec normalization, keyCode=0,
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
MC_TICKS_PER_SECOND = 20.0        # MC native TPS; convert tick→video frame
VIDEO_FPS = 30.0                  # PRD-mandated video fps
MC_BLOCKS_TO_METERS = 1.0         # 1 block = 1 meter in MC
MC_GRAVITY_MPS2 = 32.0            # PRD §4.2 (MC physics: 32 m/s² not Earth's 9.8)
DEFAULT_FOV_DEG = 70.0            # MC default field of view

# Camera intrinsics for 1920×1080 @ 70° FOV — derive fx, fy, Cx, Cy
# fx = fy = (W/2) / tan(fov/2);  Cx = W/2; Cy = H/2
_W, _H = 1920, 1080
_FOV_RAD = math.radians(DEFAULT_FOV_DEG)
_FX = (_W / 2.0) / math.tan(_FOV_RAD / 2.0)
DEFAULT_INTRINSICS = {
    "fx": round(_FX, 3),
    "fy": round(_FX, 3),
    "Cx": _W / 2.0,           # literal capital — PRD iron-law
    "Cy": _H / 2.0,
}


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

    # mouse_x/y normalized from look-vector — yaw→[0,1] mouse_x, pitch→[0,1] mouse_y
    mouse_x = (yaw % 360.0) / 360.0
    mouse_y = (pitch + 90.0) / 180.0

    # Time = tick / 20 (MC TPS)
    time_s = frame_idx / VIDEO_FPS

    return {
        "frame": frame_idx,
        "time": round(time_s, 6),
        "fps": VIDEO_FPS,
        "route_type": route_type,
        "mouse_x": round(mouse_x, 6),
        "mouse_y": round(mouse_y, 6),
        "mouse_dx": 0.0,   # filled if inputs.jsonl present
        "mouse_dy": 0.0,
        "keyCode": 0,      # filled if inputs.jsonl present
        "camera_position": [cam_x, cam_y, cam_z],
        "camera_rotation_oula": cam_rot_oula,
        "camera_rotation_quaternion": cam_quat,
        "camera_Follow Offset": [0.0, 0.0, 0.0],   # literal SPACE + capital F
        "camera_intrinsics": DEFAULT_INTRINSICS,
        "camera_speed": [vx, vy, vz],   # m/s
        "player_position": [x, y, z],
        "player_rotation_oula": player_rot_oula,
        "player_rotation_quaternion": player_quat,
        "player_speed": [vx, vy, vz],   # in MC, head and body move together
        "metric_scale": metric_scale,
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
            for key in ("x", "y", "z", "yaw_deg", "pitch_deg",
                        "look_x", "look_y", "look_z",
                        "velocity_x", "velocity_y", "velocity_z"):
                a = ticks[pos - 1].get(key, 0)
                b = ticks[pos].get(key, 0)
                row[key] = a + alpha * (b - a)
            row["timestamp_ms"] = target_t
            output.append(row)
    return output


def merge_inputs(action_camera_rows: list[dict], inputs_path: Path) -> int:
    """Overlay mouse_dx/dy + keyCode from inputs.jsonl onto action_camera rows.

    Returns count of rows updated. inputs.jsonl format expected:
      {"timestamp_ns": ..., "event": "mouse_move", "dx": ..., "dy": ...}
      {"timestamp_ns": ..., "event": "key_press", "vk_code": ...}
    """
    if not inputs_path.exists():
        return 0
    events = []
    with inputs_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if not events:
        return 0
    # Sort by timestamp, then for each action_camera row find nearest mouse_move
    events.sort(key=lambda e: e.get("timestamp_ns", 0))
    updated = 0
    for row in action_camera_rows:
        t_target_ns = int(row["time"] * 1e9)
        # binary search nearest mouse_move
        nearest = None
        nearest_dt = float("inf")
        for ev in events:
            t = ev.get("timestamp_ns", 0)
            if abs(t - t_target_ns) < nearest_dt:
                nearest_dt = abs(t - t_target_ns)
                nearest = ev
        if nearest:
            row["mouse_dx"] = float(nearest.get("dx", 0.0))
            row["mouse_dy"] = float(nearest.get("dy", 0.0))
            row["keyCode"] = int(nearest.get("vk_code", 0))
            updated += 1
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
        for line in f:
            line = line.strip()
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
    rows = [transform_tick_to_action_camera_row(t, i) for i, t in enumerate(resampled)]

    # Optional: merge inputs.jsonl
    inputs_path = session / "inputs.jsonl"
    if inputs_path.exists():
        n = merge_inputs(rows, inputs_path)
        print(f"[transform] merged {n} input events")

    # Write action_camera.json
    out_path = session / "action_camera.json"
    out_path.write_text(json.dumps(rows, indent=2))
    print(f"[transform] wrote {out_path} ({len(rows)} rows, {out_path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
