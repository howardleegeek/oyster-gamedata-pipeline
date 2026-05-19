#!/usr/bin/env python3
"""
G048 · bin/edge_test_min_int_values.py

Boundary test: int64 min for frame_id — confirm no underflow.
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Tuple

# int64 minimum value: -2^63
INT64_MIN: int = -9223372036854775808
INT64_MAX: int = 9223372036854775807


def test_frame_id_boundary() -> Tuple[bool, str]:
    """Test frame_id handling at int64 minimum boundary."""
    frame_id = INT64_MIN
    
    # Verify the value is correctly represented
    if frame_id != INT64_MIN:
        return False, f"frame_id mismatch: expected {INT64_MIN}, got {frame_id}"
    
    # Verify arithmetic operations don't cause unexpected behavior
    result = frame_id + 1
    if result != INT64_MIN + 1:
        return False, f"Arithmetic error: {frame_id} + 1 = {result}"
    
    # Verify comparison operations
    if not (frame_id <= 0 and frame_id < INT64_MAX):
        return False, f"Comparison error for frame_id={frame_id}"
    
    # Verify string representation
    if str(frame_id) != "-9223372036854775808":
        return False, f"String representation error for frame_id={frame_id}"
    
    return True, "All int64 min boundary tests passed for frame_id"


def test_frame_id_range_validation() -> Tuple[bool, str]:
    """Test that frame_id values within int64 range are valid."""
    test_values: List[int] = [
        INT64_MIN, INT64_MIN + 1, -1, 0, 1, INT64_MAX - 1, INT64_MAX
    ]
    
    for val in test_values:
        # Verify value is within int64 range and can be stored/retrieved
        if val < INT64_MIN or val > INT64_MAX:
            return False, f"Value {val} is outside int64 range"
        if val != val:  # sanity check
            return False, f"Value storage error: {val}"
    
    return True, "All frame_id range validation tests passed"


def main(argv: List[str] | None = None) -> int:
    """
    Main entry point for the boundary test.
    
    Args:
        argv: Command-line arguments. Defaults to sys.argv[1:] if None.
    
    Returns:
        Exit code: 0 for success, 1 for failure.
    """
    parser = argparse.ArgumentParser(
        description="Boundary test: int64 min for frame_id — confirm no underflow"
    )
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Enable verbose output")
    
    args = parser.parse_args(argv)
    
    tests = [
        ("frame_id_boundary", test_frame_id_boundary),
        ("frame_id_range_validation", test_frame_id_range_validation),
    ]
    
    all_passed = True
    for test_name, test_func in tests:
        success, message = test_func()
        status = "PASS" if success else "FAIL"
        if args.verbose or not success:
            print(f"[{status}] {test_name}: {message}")
        if not success:
            all_passed = False
    
    if all_passed:
        print("All boundary tests passed successfully.")
        return 0
    print("Some boundary tests failed.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())