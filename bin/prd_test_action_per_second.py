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


def load_actions_from_file(filepath: Path) -> list[float]:
    """
    Load action rates from a JSON or text file.

    Supports three formats:
    1. JSON list of numbers: [1.0, 2.0, 3.0]
    2. action_camera.json: List of action records with timestamps
    3. Plain text: one value per line

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
            if data and isinstance(data[0], dict) and "timestamp" in data[0]:
                return _extract_action_rates_from_action_camera(data)
        raise ValueError("JSON file must contain a list of numbers or action_camera.json format")

    # Plain text: one value per line
    return [float(line.strip()) for line in content.splitlines() if line.strip()]


def _extract_action_rates_from_action_camera(records: list[dict]) -> list[float]:
    """
    Extract action rates from action_camera.json format.

    Calculates actions-per-second by measuring time deltas between consecutive
    action records. Each record represents one action frame.

    Args:
        records: List of action records with 'timestamp' field.

    Returns:
        List of action rates (actions per second) calculated from time deltas.
    """
    if len(records) < 2:
        raise ValueError("action_camera.json must contain at least 2 records to calculate action rates")

    # Sort by timestamp to ensure correct order
    sorted_records = sorted(records, key=lambda r: r.get("timestamp", 0))

    action_rates = []
    for i in range(1, len(sorted_records)):
        prev_ts = sorted_records[i - 1].get("timestamp", 0)
        curr_ts = sorted_records[i].get("timestamp", 0)
        delta_t = curr_ts - prev_ts

        if delta_t > 0:
            # 1 action per delta_t seconds = actions per second
            action_rates.append(1.0 / delta_t)

    if not action_rates:
        raise ValueError("Could not calculate action rates from action_camera.json")

    return action_rates


def main(argv: list[str] | None = None) -> int:
    """
    Main entry point for the actions-per-second quality test.

    Args:
        argv: Command line arguments (defaults to sys.argv).

    Returns:
        Exit code: 0 if quality is acceptable, 1 otherwise.
    """
    parser = argparse.ArgumentParser(
        description="Test median actions-per-second against PRD quality thresholds."
    )
    parser.add_argument(
        "-i", "--input",
        help="Input file containing action rates (JSON or text, one per line)",
        type=Path
    )
    parser.add_argument(
        "-a", "--actions",
        nargs="+",
        type=float,
        help="Action rates directly on command line"
    )
    parser.add_argument(
        "-j", "--json-output",
        action="store_true",
        help="Output results as JSON"
    )

    args = parser.parse_args(argv)

    # Get action rates from input source
    if args.input:
        try:
            actions = load_actions_from_file(args.input)
        except Exception as e:
            if args.json_output:
                print(json.dumps({"error": str(e)}))
            else:
                print(f"Error: {e}", file=sys.stderr)
            return 2
    elif args.actions:
        actions = args.actions
    else:
        parser.print_help()
        return 1

    # Analyze quality
    try:
        result = analyze_capture_quality(actions)
    except ValueError as e:
        if args.json_output:
            print(json.dumps({"error": str(e)}))
        else:
            print(f"Error: {e}", file=sys.stderr)
        return 2

    # Output results
    if args.json_output:
        print(json.dumps(result, indent=2))
    else:
        print(f"Median actions per second: {result['median_actions_per_second']}")
        print(f"Range: [{result['min_actions_per_second']}, {result['max_actions_per_second']}]")
        print(f"Sample count: {result['sample_count']}")
        print(f"Quality status: {result['quality_status']}")

        if not result['in_range']:
            print(f"\nFAILED: Median {result['median_actions_per_second']} is outside acceptable range [{MIN_ACTIONS_PER_SECOND}, {MAX_ACTIONS_PER_SECOND}]")
            return 1
        else:
            print(f"\nPASSED: Median {result['median_actions_per_second']} is within acceptable range")
            return 0

    return 0 if result['in_range'] else 1


if __name__ == "__main__":
    sys.exit(main())
