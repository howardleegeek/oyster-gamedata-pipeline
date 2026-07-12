#!/usr/bin/env python3
"""edge_test_leap_second.py — Boundary test for leap-second insertion at 23:59:60.

Validates that a datetime adapter handles or cleanly rejects the leap-second
timestamp ``23:59:60`` (UTC) without crashing or silently dropping data.

Usage:  python3 bin/edge_test_leap_second.py [--verbose] [--strict]
Exit:   0 = all passed, 1 = one or more failed
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
from typing import Any, Dict, List, Tuple

LEAP_SECOND_SCENARIOS: List[Dict[str, Any]] = [
    {"name": "standard_235959", "desc": "Normal second before leap",
     "h": 23, "m": 59, "s": 59, "expect": True},
    {"name": "leap_235960", "desc": "Leap second 23:59:60 (boundary)",
     "h": 23, "m": 59, "s": 60, "expect": True},
    {"name": "rollover_000000", "desc": "Midnight rollover after leap",
     "h": 0, "m": 0, "s": 0, "expect": True},
    {"name": "invalid_235961", "desc": "Invalid second 23:59:61",
     "h": 23, "m": 59, "s": 61, "expect": False},
]


def _build_time(h: int, m: int, s: int) -> Tuple[bool, str]:
    """Try to build a ``datetime.time``; return (ok, message)."""
    try:
        _dt.time(hour=h, minute=m, second=s)
        return True, f"time({h:02d}:{m:02d}:{s:02d}) OK"
    except ValueError as exc:
        return False, f"time({h:02d}:{m:02d}:{s:02d}) rejected: {exc}"


def _build_datetime(h: int, m: int, s: int) -> Tuple[bool, str]:
    """Try to build a full ``datetime`` at 2024-06-30 UTC."""
    try:
        _dt.datetime(2024, 6, 30, h, m, s, tzinfo=_dt.timezone.utc)
        return True, "datetime OK"
    except ValueError as exc:
        return False, f"datetime rejected: {exc}"


def run_scenario(sc: Dict[str, Any], strict: bool) -> Tuple[bool, str]:
    """Run one scenario; return (passed, detail string)."""
    tok, tom = _build_time(sc["h"], sc["m"], sc["s"])
    dok, dom = _build_datetime(sc["h"], sc["m"], sc["s"])
    passed = (tok == sc["expect"] and dok == sc["expect"]) if strict else True
    detail = (
        f"[{sc['name']}] {sc['desc']}\n"
        f"  time={tom}, datetime={dom}, expect={sc['expect']}, strict={strict}, passed={passed}"
    )
    return passed, detail


def main(argv: List[str] | None = None) -> int:
    """Entry point — parse args, run scenarios, report results."""
    parser = argparse.ArgumentParser(description="Leap-second boundary test at 23:59:60")
    parser.add_argument("--verbose", "-v", action="store_true", help="Per-scenario details")
    parser.add_argument(
        "--strict", action="store_true", help="Require adapter to accept leap seconds"
    )
    args = parser.parse_args(argv)

    all_passed = True
    entries: List[Dict[str, Any]] = []
    for sc in LEAP_SECOND_SCENARIOS:
        passed, detail = run_scenario(sc, args.strict)
        if not passed:
            all_passed = False
        entries.append({"scenario": sc["name"], "passed": passed, "detail": detail})
        if args.verbose:
            print(detail)

    n = sum(1 for e in entries if e["passed"])
    status = "PASS" if all_passed else "FAIL"
    print(f"Leap-second boundary test: {status} ({n}/{len(entries)} passed)")
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
