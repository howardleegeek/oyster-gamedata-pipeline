#!/usr/bin/env python3
"""
G071 · PRD p6 #4: WASD Action Balance Test

Validates that no single WASD key exceeds 60% usage in long captures.
Designed for game replay/capture analysis.

Exit codes:
    0 - All keys balanced within threshold
    1 - Threshold violation detected
    2 - Invalid input / internal error
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)


class KeyStats(NamedTuple):
    """Statistics for a single key."""
    key: str
    count: int
    percentage: float


class BalanceResult(NamedTuple):
    """Result of balance analysis."""
    total: int
    stats: list[KeyStats]
    violations: list[KeyStats]


def parse_keypress_file(path: Path) -> dict[str, int]:
    """
    Parse a keypress capture file and return key counts.

    Supports CSV (key,count) and JSON ({"W": N, "A": N, ...}) formats.
    """
    content = path.read_text(encoding="utf-8").strip()
    counts: dict[str, int] = {"W": 0, "A": 0, "S": 0, "D": 0}

    # Try JSON first
    if content.startswith("{") or content.startswith("["):
        try:
            data = json.loads(content)
            if isinstance(data, list):
                for record in data:
                    if isinstance(record, dict) and "key" in record:
                        key = str(record["key"]).upper()
                        if key in counts:
                            counts[key] += 1
                return counts
            elif isinstance(data, dict):
                return {
                    k.upper(): v for k, v in data.items()
                    if k.upper() in counts
                }
        except (json.JSONDecodeError, ValueError) as exc:
            logger.debug("JSON parse failed for %s: %s", path, exc)

    # Try CSV
    try:
        reader = csv.DictReader(content.splitlines())
        for row in reader:
            key = row.get("key", "").upper()
            if key in counts:
                counts[key] += 1
        return counts
    except csv.Error as exc:
        logger.debug("CSV parse failed for %s: %s", path, exc)

    raise ValueError(f"Unsupported file format: {path}")


def analyze_balance(counts: dict[str, int], threshold: float = 60.0) -> BalanceResult:
    """
    Analyze WASD key balance against threshold.

    Args:
        counts: Dictionary of key counts (W, A, S, D)
        threshold: Maximum allowed percentage for any single key

    Returns:
        BalanceResult with statistics and any violations
    """
    total = sum(counts.values())

    if total == 0:
        return BalanceResult(total=0, stats=[], violations=[])

    stats: list[KeyStats] = []
    violations: list[KeyStats] = []

    for key in "WASD":
        count = counts.get(key, 0)
        percentage = (count / total) * 100.0
        stat = KeyStats(key=key, count=count, percentage=percentage)
        stats.append(stat)

        if percentage > threshold:
            violations.append(stat)

    return BalanceResult(total=total, stats=stats, violations=violations)


def main(argv: list[str] | None = None) -> int:
    """
    Main entry point for WASD balance test.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:])

    Returns:
        Exit code: 0=pass, 1=violation, 2=error
    """
    parser = argparse.ArgumentParser(
        description="Test WASD key balance in capture files"
    )
    parser.add_argument(
        "input", type=Path,
        help="Input file (CSV or JSON format)"
    )
    parser.add_argument(
        "-t", "--threshold", type=float, default=60.0,
        help="Maximum allowed percentage for any key (default: 60.0)"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Print detailed statistics"
    )

    args = parser.parse_args(argv)

    if not args.input.exists():
        print(f"Error: Input file not found: {args.input}", file=sys.stderr)
        return 2

    try:
        counts = parse_keypress_file(args.input)
        result = analyze_balance(counts, args.threshold)

        if args.verbose:
            print(f"Total keypresses: {result.total}")
            for stat in result.stats:
                print(f"  {stat.key}: {stat.count} ({stat.percentage:.1f}%)")

        if result.violations:
            print("FAIL: Key balance threshold exceeded:", file=sys.stderr)
            for v in result.violations:
                print(f"  {v.key}: {v.percentage:.1f}% > {args.threshold}%", file=sys.stderr)
            return 1

        print("PASS: All keys within balance threshold")
        return 0

    except (ValueError, json.JSONDecodeError, csv.Error) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
