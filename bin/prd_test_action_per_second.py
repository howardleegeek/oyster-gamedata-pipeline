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
            return [float(x) for x in data]
        raise ValueError("JSON file must contain a list of numbers")

    # Plain text: one value per line
    return [float(line.strip()) for line in content.splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    """
    Main entry point for the actions-per-second quality tester.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).

    Returns:
        Exit code: 0 for acceptable quality, 1 for low-quality, 2 for errors.
    """
    parser = argparse.ArgumentParser(
        description="Test median actions-per-second against PRD quality thresholds."
    )
    parser.add_argument(
        "-i", "--input",
        type=Path,
        help="Input file containing action rates (JSON or text, one per line)",
    )
    parser.add_argument(
        "-a", "--actions",
        type=float,
        nargs="+",
        help="Action rates directly on command line",
    )
    parser.add_argument(
        "-j", "--json-output",
        action="store_true",
        help="Output results as JSON",
    )

    args = parser.parse_args(argv)

    try:
        if args.input:
            actions = load_actions_from_file(args.input)
        elif args.actions:
            actions = args.actions
        else:
            parser.error("Either --input or --actions must be provided")

        if not actions:
            print("Error: No action rates provided", file=sys.stderr)
            return 2

        result = analyze_capture_quality(actions)

        if args.json_output:
            print(json.dumps(result, indent=2))
        else:
            print(f"Median APS: {result['median_actions_per_second']}")
            print(
                f"Range: [{result['min_actions_per_second']}, {result['max_actions_per_second']}]"
            )
            print(f"Samples: {result['sample_count']}")
            print(f"Quality: {result['quality_status']}")

        return 0 if result["in_range"] else 1

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
