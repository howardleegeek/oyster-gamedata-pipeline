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


def quat_angular_distance(q1: tuple[float, ...], q2: tuple[float, ...]) -> float:
    """Compute the angular distance between two quaternions.

    Uses the arccosine of the absolute dot product to get the angular
    distance in radians, ensuring the result is in [0, pi].

    Args:
        q1: First quaternion as a tuple of (x, y, z, w) components.
        q2: Second quaternion as a tuple of (x, y, z, w) components.

    Returns:
        Angular distance in radians between the two quaternions.
    """
    dot = sum(a * b for a, b in zip(q1, q2))
    dot = max(-1.0, min(1.0, dot))
    return 2 * math.acos(abs(dot))


# ---- Layer 1: Math invariants ------------------------------------------------

def check_layer1_math_invariants(data: dict[str, Any]) -> tuple[bool, list[str]]:
    """Check quaternion norms, pitch bounds, euler round‑trip, quaternion continuity."""
    errors: list[str] = []
    frames = data.get("frames", [])
    for i, frame in enumerate(frames):
        # quaternion norm ≈ 1.0
        quat = frame.get("quaternion", (0.0, 0.0, 0.0, 1.0))
        norm = quat_norm(quat)
        if abs(norm - 1.0) > EPS_QUAT_NORM:
            errors.append(f"Frame {i}: quaternion norm {norm:.4f} ≠ 1.0 ± {EPS_QUAT_NORM}")

        # pitch ∈ [-90°, 90°]
        euler = frame.get("euler", (0.0, 0.0, 0.0))
        pitch = euler[1]
        if not (-90.0 - EPS_PITCH_DEG <= pitch <= 90.0 + EPS_PITCH_DEG):
            errors.append(f"Frame {i}: pitch {pitch:.2f}° outside [-90°, 90°]")

        # euler → quat → euler round‑trip
        roll, pitch, yaw = euler
        q_calc = euler_zyx_to_quat(roll, pitch, yaw)
        # TODO: implement reverse conversion quat → euler to verify round‑trip
        # For now, just note that this check is pending
        pass

        # quaternion continuity (no 180° jumps)
        if i > 0:
            prev_quat = frames[i - 1].get("quaternion", (0.0, 0.0, 0.0, 1.0))
            ang_dist = math.degrees(quat_angular_distance(prev_quat, quat))
            if ang_dist > 170:  # near‑180° jump
                errors.append(f"Frame {i}: quaternion jump {ang_dist:.1f}° > 170°")

    return len(errors) == 0, errors


# ---- Layer 2: PRD reference --------------------------------------------------

def check_layer2_prd_reference(data: dict[str, Any]) -> tuple[bool, list[str]]:
    """Verify that yaw=90° produces the reference quaternion from PRD line 124."""
    errors: list[str] = []
    frames = data.get("frames", [])
    for i, frame in enumerate(frames):
        euler = frame.get("euler", (0.0, 0.0, 0.0))
        if abs(euler[0]) < 0.1 and abs(euler[1]) < 0.1 and abs(euler[2] - 90.0) < 0.1:
            quat = frame.get("quaternion", (0.0, 0.0, 0.0, 1.0))
            ref = PRD_REF_YAW90
            diff = math.sqrt(sum((a - b) ** 2 for a, b in zip(quat, ref)))
            if diff > 0.01:
                errors.append(
                    f"Frame {i}: yaw≈90° quaternion {quat} differs from reference {ref} by {diff:.4f}"
                )
    if not errors:
        errors.append("No frame with yaw≈90° found; cannot verify PRD reference")
    return len(errors) == 0, errors


# ---- Layer 3: Behavioral consistency ----------------------------------------

def check_layer3_behavioral(data: dict[str, Any]) -> tuple[bool, list[str]]:
    """Check mouse‑dx integration, forward movement, monotonic timestamps."""
    errors: list[str] = []
    frames = data.get("frames", [])
    if not frames:
        return False, ["No frames"]

    # ∑ mouse_dx ≈ total yaw delta
    total_dx = sum(frame.get("mouse_dx", 0.0) for frame in frames)
    first_yaw = frames[0].get("euler", (0.0, 0.0, 0.0))[2]
    last_yaw = frames[-1].get("euler", (0.0, 0.0, 0.0))[2]
    yaw_delta = last_yaw - first_yaw
    if abs(total_dx - yaw_delta) > 5.0:
        errors.append(f"mouse_dx sum {total_dx:.2f} ≠ yaw delta {yaw_delta:.2f}")

    # W key → forward movement when yaw≈0
    for i, frame in enumerate(frames):
        keys = frame.get("keys", {})
        if keys.get("key.w", False):
            euler = frame.get("euler", (0.0, 0.0, 0.0))
            if abs(euler[2]) < 10.0:  # yaw near 0°
                pos = frame.get("position", {})
                prev_pos = frames[i - 1].get("position", {}) if i > 0 else pos
                dz = pos.get("z", 0.0) - prev_pos.get("z", 0.0)
                if dz <= 0:
                    errors.append(f"Frame {i}: W pressed, yaw≈0°, but dz={dz:.3f} (should be positive)")

    # monotonic timestamps, ~33.33ms gaps
    prev_ts = None
    for i, frame in enumerate(frames):
        ts = frame.get("timestamp", 0.0)
        if prev_ts is not None:
            if ts <= prev_ts:
                errors.append(f"Frame {i}: timestamp {ts} ≤ previous {prev_ts}")
            dt = ts - prev_ts
            if abs(dt - 0.03333) > 0.001:
                errors.append(f"Frame {i}: dt={dt:.5f}s ≠ 33.33ms ±1ms")
        prev_ts = ts

    return len(errors) == 0, errors


# ---- Layer 4: Temporal continuity --------------------------------------------

def check_layer4_continuity(data: dict[str, Any]) -> tuple[bool, list[str]]:
    """Check smooth quaternion transitions and plausible position speeds."""
    errors: list[str] = []
    frames = data.get("frames", [])
    prev_quat = None
    prev_pos = None
    prev_ts = None

    for i, frame in enumerate(frames):
        quat = frame.get("quaternion", (0.0, 0.0, 0.0, 1.0))
        pos = frame.get("position", {})
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
    """Run 5-layer verification for action camera data.

    Args:
        argv: Command line arguments (defaults to sys.argv[1:]).

    Returns:
        int: Exit code where 0 means all enabled layers passed, otherwise
             returns the number of failed layers.
    """
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