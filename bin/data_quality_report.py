#!/usr/bin/env python3
"""
Data Quality Report: Analyze buyer directory for action_camera.json metrics.
"""

import argparse
import json
import math
import os
import sys
from collections import Counter


def magnitude(vec: list) -> float:
    """Calculate magnitude of a 3D vector."""
    if not vec or len(vec) < 3:
        return 0.0
    return math.sqrt(vec[0]**2 + vec[1]**2 + vec[2]**2)


def report(buyer_dir: str) -> dict:
    """
    Analyze action_camera.json and return metric dict.

    Args:
        buyer_dir: Path to buyer directory containing action_camera.json

    Returns:
        Dictionary with computed metrics
    """
    json_path = os.path.join(buyer_dir, "action_camera.json")

    if not os.path.exists(json_path):
        raise FileNotFoundError(f"action_camera.json not found in {buyer_dir}")

    with open(json_path, "r") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("action_camera.json must contain a list of records")

    total_records = len(data)

    # Stationary percentage (player_speed magnitude == 0)
    stationary_count = 0
    for record in data:
        player_speed = record.get("player_speed", [0, 0, 0])
        if magnitude(player_speed) == 0:
            stationary_count += 1

    pct_stationary = (stationary_count / total_records * 100) if total_records > 0 else 0

    # WASD distribution per keyCode
    wasd_counter = Counter()
    for record in data:
        keys = record.get("keys", {})
        for key_name in ["w", "a", "s", "d"]:
            if keys.get(key_name, False):
                wasd_counter[key_name] += 1

    wasd_distribution = dict(wasd_counter)

    # Route type distribution
    route_counter = Counter()
    for record in data:
        route_type = record.get("route_type", "unknown")
        route_counter[route_type] += 1

    route_type_distribution = dict(route_counter)

    # Frame continuity (no gaps)
    frame_ids = [r.get("frame_id") for r in data if "frame_id" in r]
    frame_ids_sorted = sorted(frame_ids)
    has_gaps = False
    if len(frame_ids_sorted) > 1:
        for i in range(1, len(frame_ids_sorted)):
            if frame_ids_sorted[i] - frame_ids_sorted[i-1] > 1:
                has_gaps = True
                break

    frame_continuity = "PASS" if not has_gaps else "FAIL"

    # Camera speed mean magnitude
    camera_speeds = []
    for record in data:
        camera_speed = record.get("camera_speed", [0, 0, 0])
        camera_speeds.append(magnitude(camera_speed))

    camera_speed_mean = sum(camera_speeds) / len(camera_speeds) if camera_speeds else 0.0

    # Determine overall PASS/FAIL
    # PASS if frame continuity is good (no gaps)
    overall_status = "PASS" if frame_continuity == "PASS" else "FAIL"

    return {
        "total_records": total_records,
        "pct_stationary": round(pct_stationary, 2),
        "wasd_distribution": wasd_distribution,
        "route_type_distribution": route_type_distribution,
        "frame_continuity": frame_continuity,
        "camera_speed_mean_magnitude": round(camera_speed_mean, 4),
        "status": overall_status
    }


def main():
    parser = argparse.ArgumentParser(
        description="Analyze buyer directory for data quality metrics"
    )
    parser.add_argument(
        "--buyer-dir",
        required=True,
        help="Path to buyer directory containing action_camera.json"
    )

    args = parser.parse_args()

    try:
        metrics = report(args.buyer_dir)
        print(json.dumps(metrics, indent=2))

        if metrics["status"] == "PASS":
            sys.exit(0)
        else:
            sys.exit(1)

    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
