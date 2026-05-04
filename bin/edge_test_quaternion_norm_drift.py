#!/usr/bin/env python3
"""edge_test_quaternion_norm_drift.py

Boundary test: quaternion magnitude 1.0001 from float drift — lint tolerates
within epsilon.

Exercises the edge case where accumulated floating-point rounding errors cause
a unit quaternion's norm to drift slightly above 1.0 (e.g. 1.0001).  The test
verifies that the validation layer tolerates this drift within a configurable
epsilon band while still rejecting quaternions whose norm deviates beyond it.

Usage:
    python3 bin/edge_test_quaternion_norm_drift.py [--epsilon EPS] [--verbose]

Exit codes: 0 = all passed, 1 = one or more failed.
"""

from __future__ import annotations

import argparse
import math
import sys
from typing import List, Tuple

DEFAULT_EPSILON: float = 1e-3
UNIT_QUATERNION: Tuple[float, float, float, float] = (1.0, 0.0, 0.0, 0.0)


def quaternion_norm(q: Tuple[float, float, float, float]) -> float:
    """Return the Euclidean norm (magnitude) of quaternion *q* = (w, x, y, z)."""
    return math.sqrt(q[0] ** 2 + q[1] ** 2 + q[2] ** 2 + q[3] ** 2)


def is_unit_quaternion(
    q: Tuple[float, float, float, float],
    epsilon: float = DEFAULT_EPSILON,
) -> bool:
    """Check whether *q* is a unit quaternion within *epsilon* tolerance.

    Returns True when |‖q‖ − 1| ≤ epsilon.
    """
    return abs(quaternion_norm(q) - 1.0) <= epsilon


def drift_quaternion(
    base: Tuple[float, float, float, float],
    drift: float,
) -> Tuple[float, float, float, float]:
    """Apply a small uniform drift to each component of *base*."""
    return tuple(c + drift for c in base)  # type: ignore[return-value]


def _build_test_cases(epsilon: float) -> List[Tuple[str, Tuple[float, float, float, float], bool]]:
    """Return a list of (label, quaternion, expected_is_unit) test tuples."""
    cases: List[Tuple[str, Tuple[float, float, float, float], bool]] = []
    cases.append(("perfect_unit", UNIT_QUATERNION, True))
    # Drift producing norm ≈ 1.000025 — within epsilon
    cases.append(("drift_2.5e-5", drift_quaternion(UNIT_QUATERNION, 2.5e-5), True))
    # Drift producing norm ≈ 1.00005 — within epsilon
    cases.append(("drift_5e-5_norm~1.0001", drift_quaternion(UNIT_QUATERNION, 5.0e-5), True))
    # Drift pushing norm beyond epsilon — should fail
    cases.append(("drift_0.01_out_of_tolerance", drift_quaternion(UNIT_QUATERNION, 0.01), False))
    # Zero quaternion — norm 0
    cases.append(("zero_quaternion", (0.0, 0.0, 0.0, 0.0), False))
    # Norm < 1 but within epsilon (0.99975, diff=0.00025 < 1e-3)
    cases.append(("slightly_below_unit", (0.99975, 0.0, 0.0, 0.0), True))
    # Pre-normalised quaternion
    raw = (0.5, 0.5, 0.5, 0.5)
    n = quaternion_norm(raw)
    normalised = tuple(c / n for c in raw)
    cases.append(("normalised_05s", normalised, True))
    # Same with tiny drift — still passes
    cases.append(("normalised_drift_1e-6", drift_quaternion(normalised, 1.0e-6), True))
    return cases


def run_tests(epsilon: float, verbose: bool) -> int:
    """Execute all boundary test cases. Returns 0 on success, 1 on failure."""
    cases = _build_test_cases(epsilon)
    failures: int = 0
    for label, quat, expected in cases:
        norm = quaternion_norm(quat)
        result = is_unit_quaternion(quat, epsilon)
        status = "PASS" if result == expected else "FAIL"
        if result != expected:
            failures += 1
        if verbose or status == "FAIL":
            print(
                f"[{status}] {label:40s}  "
                f"norm={norm:.10f}  "
                f"delta={abs(norm - 1.0):.2e}  "
                f"expected={expected}  got={result}"
            )
    return failures


def main(argv: List[str] | None = None) -> int:
    """Entry point with argparse CLI."""
    parser = argparse.ArgumentParser(
        description="Boundary test: quaternion norm drift tolerance.",
    )
    parser.add_argument(
        "--epsilon", type=float, default=DEFAULT_EPSILON,
        help=f"Epsilon tolerance (default: {DEFAULT_EPSILON})",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print results for every test case.",
    )
    args = parser.parse_args(argv)
    failures = run_tests(epsilon=args.epsilon, verbose=args.verbose)
    if failures:
        print(f"\n{failures} test(s) FAILED.")
        return 1
    print("\nAll boundary tests PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
