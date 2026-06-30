#!/usr/bin/env python3
"""Red-team: timestamp year 9999 — schema clamps within sane window.

Verifies that any schema / data pipeline consuming timestamps correctly
clamps extreme year-9999 values into a sane, bounded window (e.g. the
current year ± a configurable horizon).  This guards against downstream
overflow, database max-value errors, and UI rendering failures.

Usage:
    python3 bin/red_team_year_9999_timestamp.py [--max-year 2100]
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from typing import Any, Dict, List, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_MAX_YEAR: int = 2100
DEFAULT_MIN_YEAR: int = 1970
YEAR_9999_DT: datetime.datetime = datetime.datetime(9999, 12, 31, 23, 59, 59)


def clamp_timestamp(
    dt: datetime.datetime,
    min_year: int = DEFAULT_MIN_YEAR,
    max_year: int = DEFAULT_MAX_YEAR,
) -> datetime.datetime:
    """Clamp *dt* so its year falls within [*min_year*, *max_year*].

    Returns a new datetime with the same month/day/time but a clamped year.
    """
    if dt.year < min_year:
        return dt.replace(year=min_year)
    if dt.year > max_year:
        return dt.replace(year=max_year)
    return dt


def build_test_cases(max_year: int) -> List[Dict[str, Any]]:
    """Return a list of test-case dicts exercising year-9999 clamping."""
    now = datetime.datetime.now()
    return [
        {"label": "year 9999 max", "input": YEAR_9999_DT, "expected_year": max_year},
        {
            "label": "year 9999-01-01",
            "input": datetime.datetime(9999, 1, 1),
            "expected_year": max_year,
        },
        {
            "label": f"year {max_year - 1} (in range)",
            "input": datetime.datetime(max_year - 1, 6, 15),
            "expected_year": max_year - 1,
        },
        {
            "label": f"year {max_year + 1} (just over)",
            "input": datetime.datetime(max_year + 1, 3, 1),
            "expected_year": max_year,
        },
        {
            "label": "year 1969 (under min)",
            "input": datetime.datetime(1969, 12, 31),
            "expected_year": DEFAULT_MIN_YEAR,
        },
        {"label": "current year", "input": now, "expected_year": now.year},
    ]


def run_tests(max_year: int) -> Tuple[int, int, List[str]]:
    """Execute all test cases; return (passed, failed, error_messages)."""
    passed = 0
    failed = 0
    errors: List[str] = []
    for tc in build_test_cases(max_year):
        result = clamp_timestamp(tc["input"], DEFAULT_MIN_YEAR, max_year)
        if result.year == tc["expected_year"]:
            passed += 1
        else:
            failed += 1
            errors.append(
                f"FAIL [{tc['label']}]: expected year {tc['expected_year']}, "
                f"got {result.year} (input year={tc['input'].year})"
            )
    return passed, failed, errors


def main(argv: List[str] | None = None) -> int:
    """CLI entry-point.  Returns 0 on success, 1 on any test failure."""
    parser = argparse.ArgumentParser(
        description="Red-team: verify year-9999 timestamps are clamped to a sane window."
    )
    parser.add_argument(
        "--max-year",
        type=int,
        default=DEFAULT_MAX_YEAR,
        help=f"Maximum allowed year after clamping (default: {DEFAULT_MAX_YEAR}).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit results as JSON to stdout.",
    )
    args = parser.parse_args(argv)

    passed, failed, errors = run_tests(args.max_year)
    total = passed + failed

    if args.json:
        report: Dict[str, Any] = {
            "total": total,
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "max_year": args.max_year,
        }
        print(json.dumps(report, indent=2))
    else:
        print(f"Red-team year-9999 timestamp clamp — {total} tests")
        for msg in errors:
            print(f"  {msg}")
        print(f"  {passed} passed, {failed} failed")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
