#!/usr/bin/env python3
"""
Semantic validator for action camera records.
Validates buyer-spec v1 compliance.
"""

import json
import math
import sys


def validate_action_camera_semantics(records: list[dict]) -> dict:
    """
    Validate semantic compliance of action camera records.

    Args:
        records: List of action camera records

    Returns:
        Dict with validation results
    """
    if not records:
        return {
            "iter_count": 0,
            "stationary_pct": 0.0,
            "stationary_within_10pct": False,
            "wasd_distribution": {
                "W": 0.0,
                "A": 0.0,
                "S": 0.0,
                "D": 0.0,
                "other": 0.0,
                "empty": 0.0,
            },
            "wasd_within_tolerance": False,
            "route_type_distribution": {"1": 0.0, "2": 0.0, "3": 0.0},
            "frame_continuous": False,
            "rotation_in_range": False,
            "quaternion_unit_norm": False,
            "fx_equals_fy": False,
            "summary_pass": False,
            "issues": ["No records provided"],
        }

    # Initialize counters
    iter_count = len(records)
    stationary_count = 0
    wasd_counts = {"W": 0, "A": 0, "S": 0, "D": 0, "other": 0, "empty": 0}
    route_type_counts = {"1": 0, "2": 0, "3": 0}
    issues = []

    # Check frame continuity
    frame_continuous = True
    frames = []

    # Check other validations
    rotation_in_range = True
    quaternion_unit_norm = True
    fx_equals_fy = True

    for i, record in enumerate(records):
        # Collect frame numbers for continuity check
        frame = record.get("frame")
        if frame is not None:
            frames.append(frame)

        # Check stationary (player_speed magnitude == 0)
        player_speed = record.get("player_speed", {})
        vx = player_speed.get("vx", 0)
        vy = player_speed.get("vy", 0)
        vz = player_speed.get("vz", 0)
        speed_magnitude = math.sqrt(vx * vx + vy * vy + vz * vz)
        if abs(speed_magnitude) < 1e-9:
            stationary_count += 1

        # Check WASD distribution
        key_code = record.get("keyCode", "")
        if key_code == "W":
            wasd_counts["W"] += 1
        elif key_code == "A":
            wasd_counts["A"] += 1
        elif key_code == "S":
            wasd_counts["S"] += 1
        elif key_code == "D":
            wasd_counts["D"] += 1
        elif key_code == "":
            wasd_counts["empty"] += 1
        else:
            wasd_counts["other"] += 1

        # Check route type
        route_type = str(record.get("route_type", ""))
        if route_type in route_type_counts:
            route_type_counts[route_type] += 1

        # Check rotation (oula values in [-180, 180])
        oula = record.get("oula", {})
        for axis in ["x", "y", "z"]:
            value = oula.get(axis, 0)
            if value < -180 or value > 180:
                rotation_in_range = False
                if f"Rotation {axis}={value} out of range [-180, 180]" not in issues:
                    issues.append(f"Rotation {axis}={value} out of range [-180, 180]")

        # Check quaternion unit norm
        quaternion = record.get("quaternion", [])
        if len(quaternion) == 4:
            q0, q1, q2, q3 = quaternion
            norm_sq = q0 * q0 + q1 * q1 + q2 * q2 + q3 * q3
            if abs(norm_sq - 1.0) >= 1e-3:
                quaternion_unit_norm = False
                if (
                    f"Quaternion norm squared={norm_sq:.6f} not unit (diff={abs(norm_sq-1.0):.6f})"
                    not in issues
                ):
                    issues.append(
                        f"Quaternion norm squared={norm_sq:.6f} not unit (diff={abs(norm_sq-1.0):.6f})"
                    )

        # Check camera intrinsics fx == fy
        camera_intrinsics = record.get("camera_intrinsics", {})
        fx = camera_intrinsics.get("fx", 0)
        fy = camera_intrinsics.get("fy", 0)
        if abs(fx - fy) > 1e-9:
            fx_equals_fy = False
            if f"Camera intrinsics fx={fx} != fy={fy}" not in issues:
                issues.append(f"Camera intrinsics fx={fx} != fy={fy}")

    # Calculate percentages
    stationary_pct = (stationary_count / iter_count * 100) if iter_count > 0 else 0.0

    # Check stationary within 10%
    stationary_within_10pct = stationary_pct <= 10.0
    if not stationary_within_10pct:
        issues.append(f"Stationary percentage {stationary_pct:.1f}% exceeds 10% limit")

    # Calculate WASD distribution percentages
    wasd_distribution = {}
    total_wasd = sum(wasd_counts.values())
    for key in wasd_counts:
        wasd_distribution[key] = wasd_counts[key] / total_wasd if total_wasd > 0 else 0.0

    # Check WASD within tolerance
    w_pct = wasd_distribution.get("W", 0.0)
    a_pct = wasd_distribution.get("A", 0.0)
    s_pct = wasd_distribution.get("S", 0.0)
    d_pct = wasd_distribution.get("D", 0.0)

    wasd_within_tolerance = (
        0.30 <= w_pct <= 0.50
        and 0.10 <= a_pct <= 0.30
        and 0.10 <= s_pct <= 0.30
        and 0.10 <= d_pct <= 0.30
    )

    if not wasd_within_tolerance:
        issues.append(
            f"WASD distribution out of tolerance: W={w_pct:.2f}, A={a_pct:.2f}, S={s_pct:.2f}, D={d_pct:.2f}"
        )

    # Calculate route type distribution
    route_type_distribution = {}
    total_routes = sum(route_type_counts.values())
    for key in route_type_counts:
        route_type_distribution[key] = (
            route_type_counts[key] / total_routes if total_routes > 0 else 0.0
        )

    # Check frame continuity
    if len(frames) > 1:
        frames_sorted = sorted(frames)
        for i in range(len(frames_sorted) - 1):
            if frames_sorted[i + 1] - frames_sorted[i] != 1:
                frame_continuous = False
                issues.append(f"Frame gap detected: {frames_sorted[i]} -> {frames_sorted[i + 1]}")
                break

    # Determine overall pass
    summary_pass = (
        stationary_within_10pct
        and wasd_within_tolerance
        and frame_continuous
        and rotation_in_range
        and quaternion_unit_norm
        and fx_equals_fy
    )

    return {
        "iter_count": iter_count,
        "stationary_pct": round(stationary_pct, 1),
        "stationary_within_10pct": stationary_within_10pct,
        "wasd_distribution": {k: round(v, 2) for k, v in wasd_distribution.items()},
        "wasd_within_tolerance": wasd_within_tolerance,
        "route_type_distribution": {k: round(v, 2) for k, v in route_type_distribution.items()},
        "frame_continuous": frame_continuous,
        "rotation_in_range": rotation_in_range,
        "quaternion_unit_norm": quaternion_unit_norm,
        "fx_equals_fy": fx_equals_fy,
        "summary_pass": summary_pass,
        "issues": issues,
    }


def main() -> None:
    """CLI entry point for the semantic validator.

    Parses command-line arguments, loads JSON file, validates action camera
    records, and prints validation results.

    Args:
        None. Reads input file path from sys.argv[1].

    Returns:
        None. Exits with code 0 on success, 1 on validation failure.

    Raises:
        FileNotFoundError: If input file doesn't exist.
        json.JSONDecodeError: If input file is not valid JSON.
    """
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <action_camera.json>")
        sys.exit(1)

    input_file = sys.argv[1]

    try:
        with open(input_file) as f:
            records = json.load(f)

        if not isinstance(records, list):
            print("Error: Input JSON must be a list of records")
            sys.exit(1)

        result = validate_action_camera_semantics(records)
        print(json.dumps(result, indent=2))

        # Exit with 0 if summary_pass is True, else 1
        sys.exit(0 if result["summary_pass"] else 1)

    except FileNotFoundError:
        print(f"Error: File not found: {input_file}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
