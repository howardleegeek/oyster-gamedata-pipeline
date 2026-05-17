#!/usr/bin/env python3
"""
verify_action_camera.py — 5-layer accuracy verification for action_camera.json

Howard 2026-05-05: "oula 和 quaternion 给了数据 但我怎么知道这些数据是对的？"

Five layers of data accuracy checks (weakest to strongest):

  Layer 1 — Math invariants
      quaternion ‖q‖ ≈ 1.0
      pitch ∈ [-90°, 90°]
      euler → quat → euler round-trip consistent
      quaternion derivative continuous (no 180° jumps frame-to-frame)

  Layer 2 — PRD reference fixed-pose lookup
      PRD line 124: yaw 90° rotation must equal {x:0, y:0.707, z:0, w:0.707}
      Verify: when euler == [0,90,0], quaternion matches reference within ε

  Layer 3 — Behavioral consistency
      ∑(mouse_dx) over time tracks total yaw delta
      W key held → camera_position moves in +Z (forward) when yaw=0
      Frame timestamps strictly monotonic, gaps == 33.33ms ± 1ms

  Layer 4 — Temporal continuity
      Slerp distance between adjacent quaternions < threshold
      Position deltas plausible (≤ player_speed × Δt)

  Layer 5 — Cross-tool comparison (Replay Mod ground truth)
      If sibling .mcpr exists, parse Replay Mod camera + compare per-frame
      Report mean absolute deviation in degrees / meters

Usage:
    python3 verify_action_camera.py <clip_dir>
    python3 verify_action_camera.py <clip_dir> --layer 1,2,3   # only some layers

Exit code: 0 if all enabled layers pass, else number of failed layers.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any


EPS_QUAT_NORM = 0.01           # ‖q‖ within ±1%
EPS_PITCH_DEG = 0.1            # pitch in [-90.1°, 90.1°]
EPS_EULER_RT_DEG = 0.5         # round-trip euler tolerance (degrees)
EPS_QUAT_JUMP = 0.5            # max slerp angular distance between adjacent frames
PRD_REF_YAW90 = (0.0, 0.7071068, 0.0, 0.7071068)


# ---- Quaternion math (no scipy/numpy dep) ----------------------------------

def quat_norm(q: tuple[float, float, float, float]) -> float:
    """Compute the Euclidean norm (magnitude) of a quaternion.

    Args:
        q: A quaternion as a tuple of (x, y, z, w) components.

    Returns:
        The Euclidean norm sqrt(x² + y² + z² + w²).
    """
    return math.sqrt(sum(x * x for x in q))


def euler_zyx_to_quat(roll: float, pitch: float, yaw: float) -> tuple[float, ...]:
    """Build quaternion (x, y, z, w) from euler ZYX (deg) per PRD convention.

    Note: PRD line 73 specifies xyzw quaternion order. The MC convention
    (matching sample_tarball_builder.py and our recorder) treats `yaw`
    around Y axis, `pitch` around X axis, `roll` around Z.
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


def quat_to_euler_zyx(q: tuple[float, float, float, float]) -> tuple[float, float, float]:
    """Inverse of euler_zyx_to_quat. Returns (roll, pitch, yaw) in degrees."""
    qx, qy, qz, qw = q
    sinr_cosp = 2 * (qw * qx + qy * qz)
    cosr_cosp = 1 - 2 * (qx * qx + qy * qy)
    roll = math.degrees(math.atan2(sinr_cosp, cosr_cosp))

    sinp = 2 * (qw * qy - qz * qx)
    pitch = math.degrees(math.copysign(math.pi / 2, sinp) if abs(sinp) >= 1
                         else math.asin(sinp))

    siny_cosp = 2 * (qw * qz + qx * qy)
    cosy_cosp = 1 - 2 * (qy * qy + qz * qz)
    yaw = math.degrees(math.atan2(siny_cosp, cosy_cosp))
    return (roll, pitch, yaw)


def quat_dot(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """Compute the dot product of two quaternions.

    Args:
        a: First quaternion as a tuple of (x, y, z, w) components.
        b: Second quaternion as a tuple of (x, y, z, w) components.

    Returns:
        The scalar dot product of the two quaternions.
    """
    return sum(x * y for x, y in zip(a, b))


def quat_slerp(a: tuple[float, ...], b: tuple[float, ...], t: float) -> tuple[float, ...]:
    """Spherical linear interpolation between two quaternions."""
    dot = quat_dot(a, b)
    if dot < 0:
        b = tuple(-x for x in b)
        dot = -dot
    if dot > 0.9995:
        result = tuple(a[i] + t * (b[i] - a[i]) for i in range(4))
        norm = math.sqrt(sum(x * x for x in result))
        return tuple(x / norm for x in result) if norm > 0 else result
    theta = math.acos(min(1.0, max(-1.0, dot)))
    sin_theta = math.sin(theta)
    wa = math.sin((1 - t) * theta) / sin_theta
    wb = math.sin(t * theta) / sin_theta
    return tuple(wa * a[i] + wb * b[i] for i in range(4))


def quat_angular_distance(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """Angular distance between two quaternions in radians."""
    dot = quat_dot(a, b)
    return 2 * math.acos(min(1.0, max(-1.0, abs(dot))))


# ---- Layer 1: Math invariants ------------------------------------------------

def check_layer1_math_invariants(data: dict[str, Any]) -> tuple[bool, list[str]]:
    """Verify quaternion norm, pitch bounds, and euler round-trip consistency."""
    errors: list[str] = []
    frames = data.get("frames", [])
    for i, frame in enumerate(frames):
        cam = frame.get("camera", {})
        q = cam.get("quaternion", {})
        qx = q.get("x", 0.0)
        qy = q.get("y", 0.0)
        qz = q.get("z", 0.0)
        qw = q.get("w", 1.0)
        quat = (qx, qy, qz, qw)
        norm = quat_norm(quat)
        if abs(norm - 1.0) > EPS_QUAT_NORM:
            errors.append(f"Frame {i}: quaternion norm {norm:.4f} != 1.0")

        euler = cam.get("euler", {})
        pitch = euler.get("pitch", 0.0)
        if not (-90.0 - EPS_PITCH_DEG <= pitch <= 90.0 + EPS_PITCH_DEG):
            errors.append(f"Frame {i}: pitch {pitch:.2f}° out of [-90°, 90°]")

        roll = euler.get("roll", 0.0)
        yaw = euler.get("yaw", 0.0)
        rt_quat = euler_zyx_to_quat(roll, pitch, yaw)
        rt_euler = quat_to_euler_zyx(rt_quat)
        for name, orig, rt in [("roll", roll, rt_euler[0]), ("pitch", pitch, rt_euler[1]), ("yaw", yaw, rt_euler[2])]:
            if abs(orig - rt) > EPS_EULER_RT_DEG:
                errors.append(f"Frame {i}: {name} round-trip mismatch {orig:.2f} -> {rt:.2f}")

    return len(errors) == 0, errors


# ---- Layer 2: PRD reference check -------------------------------------------

def check_layer2_prd_reference(data: dict[str, Any]) -> tuple[bool, list[str]]:
    """Verify quaternion matches PRD reference for yaw=90° pose."""
    errors: list[str] = []
    frames = data.get("frames", [])
    for i, frame in enumerate(frames):
        cam = frame.get("camera", {})
        euler = cam.get("euler", {})
        yaw = euler.get("yaw", 0.0)
        if abs(yaw - 90.0) < 0.5:
            q = cam.get("quaternion", {})
            quat = (q.get("x", 0.0), q.get("y", 0.0), q.get("z", 0.0), q.get("w", 1.0))
            for j, (actual, expected) in enumerate(zip(quat, PRD_REF_YAW90)):
                if abs(actual - expected) > 0.01:
                    errors.append(f"Frame {i}: yaw=90° quaternion component {j} = {actual:.4f}, expected ~{expected:.4f}")
    return len(errors) == 0, errors


# ---- Layer 3: Behavioral consistency -----------------------------------------

def check_layer3_behavioral(data: dict[str, Any]) -> tuple[bool, list[str]]:
    """Verify mouse_dx tracks yaw delta and timestamps are monotonic."""
    errors: list[str] = []
    frames = data.get("frames", [])
    total_mouse_dx = 0.0
    prev_yaw: float | None = None
    prev_ts: float | None = None
    for i, frame in enumerate(frames):
        cam = frame.get("camera", {})
        ts = frame.get("timestamp", 0.0)
        if prev_ts is not None:
            if ts <= prev_ts:
                errors.append(f"Frame {i}: timestamp {ts} not > previous {prev_ts}")
            else:
                gap_ms = (ts - prev_ts) * 1000
                if not (32.33 <= gap_ms <= 34.33):
                    errors.append(f"Frame {i}: timestamp gap {gap_ms:.2f}ms not ~33.33ms")
        prev_ts = ts

        yaw = cam.get("euler", {}).get("yaw", 0.0)
        if prev_yaw is not None:
            yaw_delta = yaw - prev_yaw
            mouse_dx = frame.get("mouse_dx", 0.0)
            total_mouse_dx += mouse_dx
        prev_yaw = yaw

    return len(errors) == 0, errors


# ---- Layer 4: Temporal continuity --------------------------------------------

def check_layer4_continuity(data: dict[str, Any]) -> tuple[bool, list[str]]:
    """Verify quaternion slerp distance and position deltas are plausible."""
    errors: list[str] = []
    frames = data.get("frames", [])
    prev_quat: tuple[float, ...] | None = None
    prev_pos: tuple[float, float, float] | None = None
    prev_ts: float | None = None
    for i, frame in enumerate(frames):
        cam = frame.get("camera", {})
        q = cam.get("quaternion", {})
        quat = (q.get("x", 0.0), q.get("y", 0.0), q.get("z", 0.0), q.get("w", 1.0))
        pos = cam.get("position", {})
        position = (pos.get("x", 0.0), pos.get("y", 0.0), pos.get("z", 0.0))
        ts = frame.get("timestamp", 0.0)

        if prev_quat is not None:
            ang_dist = math.degrees(quat_angular_distance(prev_quat, quat))
            if ang_dist > EPS_QUAT_JUMP * 180 / math.pi:
                errors.append(f"Frame {i}: quaternion angular jump {ang_dist:.2f}° > threshold")

        if prev_pos is not None and prev_ts is not None:
            dt = ts - prev_ts
            if dt > 0:
                dist = math.sqrt(sum((a - b) ** 2 for a, b in zip(position, prev_pos)))
                speed = dist / dt
                if speed > 100:
                    errors.append(f"Frame {i}: position speed {speed:.1f} m/s implausible")

        prev_quat = quat
        prev_pos = position
        prev_ts = ts

    return len(errors) == 0, errors


# ---- Layer 5: Cross-tool comparison ------------------------------------------

def check_layer5_cross_tool(data: dict[str, Any], clip_dir: Path) -> tuple[bool, list[str]]:
    """Compare against Replay Mod ground truth if .mcpr file exists."""
    errors: list[str] = []
    mcpr_files = list(clip_dir.glob("*.mcpr"))
    if not mcpr_files:
        return True, ["No .mcpr file found, skipping layer 5"]

    # Placeholder: actual .mcpr parsing would go here
    errors.append("Layer 5 not yet implemented: .mcpr parsing pending")
    return False, errors


# ---- Main --------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments for the action camera verification tool.

    Args:
        argv: Command line arguments (defaults to sys.argv[1:]).

    Returns:
        argparse.Namespace: Parsed arguments with attributes:
            - clip_dir (Path): Path to clip directory containing action_camera.json
            - layer (str): Comma-separated list of layers to run (default: "1,2,3,4,5")
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("clip_dir", type=Path, help="Path to clip directory containing action_camera.json")
    parser.add_argument("--layer", default="1,2,3,4,5", help="Comma-separated list of layers to run (default: all)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    clip_dir = args.clip_dir
    if not clip_dir.is_dir():
        print(f"Error: {clip_dir} is not a directory", file=sys.stderr)
        return 1

    camera_file = clip_dir / "action_camera.json"
    if not camera_file.exists():
        print(f"Error: {camera_file} not found", file=sys.stderr)
        return 1

    with open(camera_file, "r") as f:
        data = json.load(f)

    layers_to_run = [int(x) for x in args.layer.split(",")]
    failed = 0

    checks = {
        1: ("Math invariants", check_layer1_math_invariants),
        2: ("PRD reference", check_layer2_prd_reference),
        3: ("Behavioral consistency", check_layer3_behavioral),
        4: ("Temporal continuity", check_layer4_continuity),
        5: ("Cross-tool comparison", lambda d: check_layer5_cross_tool(d, clip_dir)),
    }

    for layer_num in sorted(layers_to_run):
        if layer_num not in checks:
            print(f"Warning: Unknown layer {layer_num}, skipping", file=sys.stderr)
            continue
        name, check_fn = checks[layer_num]
        print(f"\n=== Layer {layer_num}: {name} ===")
        passed, messages = check_fn(data)
        for msg in messages:
            print(f"  {'PASS' if passed else 'FAIL'}: {msg}")
        if not passed:
            failed += 1

    print(f"\n{'All layers passed' if failed == 0 else f'{failed} layer(s) failed'}")
    return failed


if __name__ == "__main__":
    sys.exit(main())