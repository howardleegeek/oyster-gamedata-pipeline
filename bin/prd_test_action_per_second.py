#!/usr/bin/env python3
"""
G073 · bin/prd_test_action_per_second.py

PRD p6 #6: Validate median actions-per-second is within 0.5 to 5.0 range.
Out-of-band capture values are flagged as low-quality.

This module provides utilities to analyze action rates and flag
captures that fall outside the acceptable quality thresholds.
"""

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any


MIN_ACTIONS_PER_SECOND = 0.5
MAX_ACTIONS_PER_SECOND = 5.0


def calculate_median_actions_per_second(actions: list[float]) -> float:
    """
    Calculate the median actions-per-second from a list of action rates.

    Args:
        actions: List of action rates (actions per second).

    Returns:
        Median value of the action rates.

    Raises:
        ValueError: If actions list is empty.
    """
    if not actions:
        raise ValueError("Actions list cannot be empty")
    return float(statistics.median(actions))


def is_quality_acceptable(median_aps: float) -> bool:
    """
    Check if the median actions-per-second falls within acceptable range.

    Args:
        median_aps: Median actions-per-second value to validate.

    Returns:
        True if within [0.5, 5.0] range, False otherwise.
    """
    return MIN_ACTIONS_PER_SECOND <= median_aps <= MAX_ACTIONS_PER_SECOND


def analyze_capture_quality(actions: list[float]) -> dict[str, Any]:
    """
    Analyze capture quality based on action rates.

    Args:
        actions: List of action rates (actions per second).

    Returns:
        Dictionary with median, min, max, and quality assessment.
    """
    median_aps = calculate_median_actions_per_second(actions)
    is_acceptable = is_quality_acceptable(median_aps)

    return {
        "median_actions_per_second": round(median_aps, 4),
        "min_actions_per_second": round(min(actions), 4),
        "max_actions_per_second": round(max(actions), 4),
        "sample_count": len(actions),
        "quality_status": "acceptable" if is_acceptable else "low-quality",
        "in_range": is_acceptable,
    }


def _has_timestamp_field(records: list[dict]) -> bool:
    """Check if a list of records has a timestamp-like field."""
    if not records:
        return False
    first = records[0]
    if not isinstance(first, dict):
        return False
    return any(k in first for k in ("timestamp", "time", "frame"))


def _is_camera_data_dict(data: dict) -> bool:
    """Check if a dict looks like camera data (not action records)."""
    camera_keys = {"camera_position", "intrinsics", "world_cube_radius"}
    return bool(camera_keys & set(data.keys()))


def _extract_action_rates_from_action_camera(records: list[dict]) -> list[float]:
    """
    Extract action rates from action_camera.json format.

    Calculates actions-per-second by measuring time deltas between consecutive
    action records. Each record represents one action frame.

    Args:
        records: List of action records with 'timestamp' or 'time' field.

    Returns:
        List of action rates (actions per second) calculated from time deltas.
    """
    if len(records) < 2:
        raise ValueError("action_camera.json must contain at least 2 records to calculate action rates")

    # Detect which timestamp field is used
    first = records[0]
    if "timestamp" in first:
        ts_key = "timestamp"
    elif "time" in first:
        ts_key = "time"
    elif "frame" in first:
        ts_key = "frame"
    else:
        raise ValueError("No timestamp field found in action_camera records")

    # Sort by timestamp to ensure correct order
    sorted_records = sorted(records, key=lambda r: r.get(ts_key, 0))

    action_rates = []
    for i in range(1, len(sorted_records)):
        prev_ts = sorted_records[i - 1].get(ts_key, 0)
        curr_ts = sorted_records[i].get(ts_key, 0)
        delta_t = curr_ts - prev_ts

        if delta_t > 0:
            # 1 action per delta_t seconds = actions per second
            action_rates.append(1.0 / delta_t)

    if not action_rates:
        raise ValueError("Could not calculate action rates from action_camera.json")

    return action_rates


def load_actions_from_file(filepath: Path) -> list[float]:
    """
    Load action rates from a JSON or text file.

    Supports four formats:
    1. JSON list of numbers: [1.0, 2.0, 3.0]
    2. action_camera.json: List of action records with timestamps
    3. Camera data dict: {"camera_position": ..., "world_cube_radius": ...} → returns []
    4. Plain text: one value per line

    Args:
        filepath: Path to the input file.

    Returns:
        List of action rates.

    Raises:
        FileNotFoundError: If file does not exist.
        ValueError: If file format is invalid.
    """
    if not filepath.exists():
        raise FileNotFoundError(f"File not found: {filepath}")

    content = filepath.read_text().strip()

    if filepath.suffix == ".json":
        data = json.loads(content)
        if isinstance(data, list):
            # Check if it's a list of numbers (direct format)
            if data and isinstance(data[0], (int, float)):
                return [float(x) for x in data]
            # Check if it's action_camera.json format (list of objects with timestamps)
            if data and isinstance(data[0], dict) and _has_timestamp_field(data):
                return _extract_action_rates_from_action_camera(data)
        elif isinstance(data, dict):
            # Camera data dict — not actionable, return empty
            if _is_camera_data_dict(data):
                return []
        raise ValueError("JSON file must contain a list of numbers or action_camera.json format")

    # Plain text: one value per line
    return [float(line.strip()) for line in content.splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    """
    Main entry point for the actions-per-second quality test.

    Args:
        argv: Command line arguments (defaults to sys.argv).

    Returns:
        Exit code: 0 if quality is acceptable, 1 otherwise.
    """
    parser = argparse.ArgumentParser(
        description="Validate median actions-per-second is within 0.5 to 5.0 range"
    )
    parser.add_argument(
        "-a", "--actions", nargs="+", type=float,
        help="Direct list of action rates to validate"
    )
    parser.add_argument(
        "-i", "--input", type=Path,
        help="Path to JSON or text file containing action rates"
    )
    parser.add_argument(
        "-j", "--json", action="store_true",
        help="Output results as JSON"
    )

    # Check for no args before parse (parser.error exits with code 2)
    check_argv = argv if argv is not None else sys.argv[1:]
    if not check_argv:
        print("Error: Either --actions or --input is required", file=sys.stderr)
        return 1

    args = parser.parse_args(check_argv)

    if not args.actions and not args.input:
        print("Error: Either --actions or --input is required", file=sys.stderr)
        return 1

    try:
        if args.actions:
            actions = args.actions
        else:
            actions = load_actions_from_file(args.input)

        if not actions:
            print("No actions to analyze")
            return 2

        result = analyze_capture_quality(actions)

        if args.json:
            print(json.dumps(result))
        else:
            print(f"Median actions/sec: {result['median_actions_per_second']}")
            print(f"Range: [{result['min_actions_per_second']}, {result['max_actions_per_second']}]")
            print(f"Sample count: {result['sample_count']}")
            print(f"Quality: {result['quality_status']}")
            print(f"Status: {'PASSED' if result['in_range'] else 'FAILED'}")

        return 0 if result["in_range"] else 1

    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
