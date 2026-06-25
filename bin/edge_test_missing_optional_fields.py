#!/usr/bin/env python3
"""
G043 · bin/edge_test_missing_optional_fields.py

Boundary test: skip optional Vector4 quat field — adapter must default-fill not crash.

Tests that a Vector4 adapter handles missing optional quaternion fields gracefully
by providing sensible default values instead of raising exceptions.

Usage:
    python3 bin/edge_test_missing_optional_fields.py [--verbose]
"""

import argparse
import json
import sys
from typing import Any, Dict, List, Optional, Tuple


class Vector4Adapter:
    """Adapter that fills missing optional quat fields with identity defaults."""

    _DEFAULT_W: float = 1.0
    _DEFAULT_QUAT: Dict[str, float] = {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}

    @classmethod
    def adapt(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        """Fill missing optional fields with defaults; never raises on missing quat."""
        adapted: Dict[str, Any] = dict(data)

        # Default scalar w if absent
        if "w" not in adapted:
            adapted["w"] = cls._DEFAULT_W

        # Default full quat dict if absent or None
        if adapted.get("quat") is None:
            adapted["quat"] = dict(cls._DEFAULT_QUAT)
        elif isinstance(adapted["quat"], dict):
            # Fill missing components inside the quat dict
            for key, default in cls._DEFAULT_QUAT.items():
                if key not in adapted["quat"]:
                    adapted["quat"][key] = default

        return adapted


def _build_test_cases() -> List[Tuple[str, Dict[str, Any], bool]]:
    """Return (name, input_data, should_succeed) test tuples."""
    return [
        (
            "complete_data",
            {
                "x": 1.0,
                "y": 2.0,
                "z": 3.0,
                "w": 0.5,
                "quat": {"x": 1.0, "y": 2.0, "z": 3.0, "w": 0.5},
            },
            True,
        ),
        (
            "missing_w",
            {"x": 1.0, "y": 2.0, "z": 3.0, "quat": {"x": 1.0, "y": 2.0, "z": 3.0, "w": 0.5}},
            True,
        ),
        ("missing_quat", {"x": 1.0, "y": 2.0, "z": 3.0, "w": 0.5}, True),
        ("missing_w_and_quat", {"x": 1.0, "y": 2.0, "z": 3.0}, True),
        ("quat_none", {"x": 1.0, "y": 2.0, "z": 3.0, "w": 0.5, "quat": None}, True),
        ("quat_partial", {"x": 1.0, "y": 2.0, "z": 3.0, "w": 0.5, "quat": {"x": 1.0}}, True),
        ("quat_partial_no_w", {"x": 1.0, "y": 2.0, "z": 3.0, "quat": {"y": 2.0, "z": 3.0}}, True),
        ("empty_dict", {}, True),
    ]


def run_tests(verbose: bool = False) -> Tuple[int, int]:
    """Execute all boundary tests. Returns (passed, failed)."""
    passed = 0
    failed = 0

    for name, data, should_succeed in _build_test_cases():
        try:
            result = Vector4Adapter.adapt(data)
            # Verify quat key always present after adaptation
            assert "quat" in result, f"quat missing after adapt: {name}"
            assert isinstance(result["quat"], dict), f"quat not dict: {name}"
            for key in ("x", "y", "z", "w"):
                assert key in result["quat"], f"quat.{key} missing: {name}"
                assert isinstance(result["quat"][key], (int, float)), (
                    f"quat.{key} not numeric: {name}"
                )
            if should_succeed:
                passed += 1
                if verbose:
                    print(f"  PASS  {name}")
            else:
                failed += 1
                if verbose:
                    print(f"  FAIL  {name} (expected failure but succeeded)")
        except Exception as exc:
            if not should_succeed:
                passed += 1
                if verbose:
                    print(f"  PASS  {name} (expected failure: {exc})")
            else:
                failed += 1
                if verbose:
                    print(f"  FAIL  {name}: {exc}")

    return passed, failed


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point. Returns 0 on success, 1 on any test failure."""
    parser = argparse.ArgumentParser(
        description="Boundary test: missing optional Vector4 quat field"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Print per-test results")
    parser.add_argument("--json", action="store_true", help="Output results as JSON to stdout")
    args = parser.parse_args(argv)

    passed, failed = run_tests(verbose=args.verbose)
    total = passed + failed

    if args.json:
        print(json.dumps({"passed": passed, "failed": failed, "total": total}))

    print(f"Results: {passed}/{total} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
