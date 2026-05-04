#!/usr/bin/env python3
"""
G070 · PRD p5 #2: route_type field distribution check.

Validates that across 240 clips, the route_type field contains at least
5 distinct types.

Usage:
    python bin/prd_test_route_type_distribution.py --data-dir data/clips
    python bin/prd_test_route_type_distribution.py --clips-file data/clips.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


def load_clips(data_dir: Path | None, clips_file: Path | None) -> list[dict[str, Any]]:
    """Load clips from directory or single file."""
    clips: list[dict[str, Any]] = []

    if data_dir:
        if not data_dir.is_dir():
            raise FileNotFoundError(f"Data directory not found: {data_dir}")
        for json_file in sorted(data_dir.glob("*.json")):
            with open(json_file, encoding="utf-8") as f:
                data = json.load(f)
                clips.extend(data if isinstance(data, list) else [data])
    elif clips_file:
        with open(clips_file, encoding="utf-8") as f:
            data = json.load(f)
            clips = data if isinstance(data, list) else [data]

    return clips


def extract_route_types(clips: list[dict[str, Any]]) -> list[str]:
    """Extract route_type values from clips, skipping missing fields."""
    return [str(c.get("route_type", "")) for c in clips if c.get("route_type")]


def validate_distribution(
    route_types: list[str],
    min_distinct: int = 5,
    expected_total: int = 240,
) -> tuple[bool, dict[str, Any]]:
    """Validate route_type distribution meets requirements."""
    distinct = set(route_types)
    dist = dict(Counter(route_types))

    details = {
        "total_clips": len(route_types),
        "expected_clips": expected_total,
        "distinct_types": len(distinct),
        "min_required": min_distinct,
        "distribution": dist,
        "type_list": sorted(distinct),
    }

    success = len(distinct) >= min_distinct and len(route_types) >= expected_total * 0.9
    return success, details


def report(details: dict[str, Any], verbose: bool = False) -> None:
    """Print validation results."""
    print(f"Total clips: {details['total_clips']}")
    print(f"Expected clips: {details['expected_clips']}")
    print(f"Distinct route_type values: {details['distinct_types']}")
    print(f"Minimum required: {details['min_required']}")
    print(f"Type list: {', '.join(details['type_list'])}")

    if verbose:
        print("\nDistribution:")
        for rt, cnt in sorted(details["distribution"].items()):
            print(f"  {rt}: {cnt}")


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Validate route_type field distribution across clips."
    )
    parser.add_argument("--data-dir", type=Path, help="Directory with clip JSON files")
    parser.add_argument("--clips-file", type=Path, help="Single JSON file with clips")
    parser.add_argument(
        "--min-distinct", type=int, default=5,
        help="Minimum distinct route_type values (default: 5)"
    )
    parser.add_argument(
        "--expected-total", type=int, default=240,
        help="Expected total clips (default: 240)"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")

    args = parser.parse_args(argv)

    if args.data_dir is None and args.clips_file is None:
        parser.error("Either --data-dir or --clips-file required")

    try:
        clips = load_clips(args.data_dir, args.clips_file)
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    route_types = extract_route_types(clips)
    success, details = validate_distribution(
        route_types, args.min_distinct, args.expected_total
    )

    report(details, args.verbose)

    if success:
        print("\n✓ PASS: route_type distribution meets requirements")
        return 0
    else:
        print("\n✗ FAIL: route_type distribution does not meet requirements")
        if details["distinct_types"] < args.min_distinct:
            print(f"  - Only {details['distinct_types']} types, need {args.min_distinct}")
        if details["total_clips"] < args.expected_total * 0.9:
            print(f"  - Only {details['total_clips']} clips, need {int(args.expected_total * 0.9)}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
