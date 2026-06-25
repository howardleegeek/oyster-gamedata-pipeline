#!/usr/bin/env python3
"""edge_test_dst_clock_change.py — Boundary test for DST clock transitions.

Verifies that UTC timestamps remain strictly monotonic across Daylight Saving
Time transitions (spring-forward and fall-back).  Local wall-clock time may
jump or repeat, but UTC must never go backwards.

Usage:
    python3 bin/edge_test_dst_clock_change.py [--tz America/New_York] [--year 2024]
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from typing import Sequence

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]


def _dst_transitions(tz_name: str, year: int) -> list[datetime]:
    """Return UTC datetimes where DST offset changes for a given TZ/year."""
    tz = ZoneInfo(tz_name)
    transitions: list[datetime] = []
    start = datetime(year, 1, 1, tzinfo=timezone.utc)
    end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    current = start
    prev_offset = current.astimezone(tz).utcoffset()
    while current < end:
        offset = current.astimezone(tz).utcoffset()
        if offset != prev_offset:
            transitions.append(current)
        prev_offset = offset
        current += timedelta(hours=1)
    return transitions


def test_utc_monotonicity_across_dst(
    tz_name: str,
    year: int,
    step_minutes: int = 1,
) -> list[str]:
    """Walk each DST transition in UTC and verify round-trip monotonicity.

    Iterates in UTC (ground truth), converts to local-aware then back to UTC.
    The resulting UTC sequence must be strictly increasing.
    """
    errors: list[str] = []
    transitions = _dst_transitions(tz_name, year)
    if not transitions:
        errors.append(f"No DST transitions found for {tz_name} in {year}")
        return errors
    tz = ZoneInfo(tz_name)
    for transition_utc in transitions:
        window_start = transition_utc - timedelta(hours=2)
        window_end = transition_utc + timedelta(hours=2)
        current_utc = window_start
        prev_rt: datetime | None = None
        while current_utc <= window_end:
            local_aware = current_utc.astimezone(tz)
            rt_utc = local_aware.astimezone(timezone.utc)
            if prev_rt is not None and rt_utc <= prev_rt:
                errors.append(
                    f"UTC non-monotonic: {rt_utc.isoformat()} "
                    f"<= {prev_rt.isoformat()} (local={local_aware.isoformat()})"
                )
            prev_rt = rt_utc
            current_utc += timedelta(minutes=step_minutes)
    return errors


def test_local_time_behavior(
    tz_name: str,
    year: int,
    step_minutes: int = 1,
) -> list[str]:
    """Verify offset changes at each DST transition boundary."""
    errors: list[str] = []
    transitions = _dst_transitions(tz_name, year)
    tz = ZoneInfo(tz_name)
    for t_utc in transitions:
        pre = (t_utc - timedelta(minutes=step_minutes)).astimezone(tz)
        post = (t_utc + timedelta(minutes=step_minutes)).astimezone(tz)
        if pre.utcoffset() == post.utcoffset():
            errors.append(f"Pre/post offsets equal at {t_utc.isoformat()} (expected DST change)")
    return errors


def main(argv: Sequence[str] | None = None) -> int:
    """Entry-point with argparse CLI. Returns 0 on success, 1 on failure."""
    parser = argparse.ArgumentParser(
        description="Verify UTC monotonicity across DST transitions.",
    )
    parser.add_argument(
        "--tz", default="America/New_York", help="IANA timezone name (default: America/New_York)"
    )
    parser.add_argument(
        "--year", type=int, default=2024, help="Calendar year to scan (default: 2024)"
    )
    parser.add_argument("--step-minutes", type=int, default=1, help="Scan granularity in minutes")
    args = parser.parse_args(list(argv) if argv is not None else None)

    print(f"Testing DST transitions for {args.tz} in {args.year} (step={args.step_minutes} min)")
    all_errors: list[str] = []

    print("\n[Test 1] UTC monotonicity across DST transitions...")
    errs = test_utc_monotonicity_across_dst(args.tz, args.year, args.step_minutes)
    all_errors.extend(errs)
    print("  ✓ PASSED" if not errs else "\n".join(f"  ✗ {e}" for e in errs))

    print("\n[Test 2] Local time offset behavior at transitions...")
    errs = test_local_time_behavior(args.tz, args.year, args.step_minutes)
    all_errors.extend(errs)
    print("  ✓ PASSED" if not errs else "\n".join(f"  ✗ {e}" for e in errs))

    if all_errors:
        print(f"\nFAILED — {len(all_errors)} error(s) total.")
        return 1
    print("\nAll tests PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
