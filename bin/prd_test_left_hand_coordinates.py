#!/usr/bin/env python3
"""
G075 · bin/prd_test_left_hand_coordinates.py

PRD p3 #4: Left-hand coordinate system validation.

This module asserts handedness via cross-product sign on Vector3 axes.
For a LEFT-handed coordinate system:
    - X × Y = -Z  (opposite of right-handed)
    - Y × Z = -X
    - Z × X = -Y

For a right-handed system (numpy default):
    - X × Y = +Z
    - Y × Z = +X
    - Z × X = +Y

Usage:
    python prd_test_left_hand_coordinates.py [--verbose]
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Tuple

import numpy as np
from numpy.typing import NDArray


def create_unit_axes() -> Tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """Create unit vectors for X, Y, Z axes."""
    return (
        np.array([1.0, 0.0, 0.0], dtype=np.float64),
        np.array([0.0, 1.0, 0.0], dtype=np.float64),
        np.array([0.0, 0.0, 1.0], dtype=np.float64),
    )


def compute_handedness_sign() -> int:
    """
    Compute the handedness sign via cross-product.

    Returns:
        +1 for right-handed, -1 for left-handed coordinate system.
    """
    x_axis, y_axis, z_axis = create_unit_axes()
    cross_xy = np.cross(x_axis, y_axis)
    return int(np.dot(cross_xy, z_axis))


def test_left_handed_cross_products(verbose: bool = False) -> bool:
    """
    Test that the coordinate system follows left-handed convention.

    In a left-handed coordinate system:
        - X × Y = -Z, Y × Z = -X, Z × X = -Y

    Args:
        verbose: If True, print detailed test results.

    Returns:
        True if all cross-product tests pass for left-handed system.
    """
    x_axis, y_axis, z_axis = create_unit_axes()
    all_passed = True

    # Test 1: X × Y should equal -Z (left-handed)
    cross_xy = np.cross(x_axis, y_axis)
    test1_pass = np.allclose(cross_xy, -z_axis)
    if verbose:
        print(f"Test X × Y = -Z: {'PASS' if test1_pass else 'FAIL'}")
        print(f"  Expected: {-z_axis}, Got: {cross_xy}")
    all_passed = all_passed and test1_pass

    # Test 2: Y × Z should equal -X (left-handed)
    cross_yz = np.cross(y_axis, z_axis)
    test2_pass = np.allclose(cross_yz, -x_axis)
    if verbose:
        print(f"Test Y × Z = -X: {'PASS' if test2_pass else 'FAIL'}")
        print(f"  Expected: {-x_axis}, Got: {cross_yz}")
    all_passed = all_passed and test2_pass

    # Test 3: Z × X should equal -Y (left-handed)
    cross_zx = np.cross(z_axis, x_axis)
    test3_pass = np.allclose(cross_zx, -y_axis)
    if verbose:
        print(f"Test Z × X = -Y: {'PASS' if test3_pass else 'FAIL'}")
        print(f"  Expected: {-y_axis}, Got: {cross_zx}")
    all_passed = all_passed and test3_pass

    return all_passed


def assert_left_handed_coordinates() -> None:
    """
    Assert that the coordinate system is left-handed.

    Raises:
        AssertionError: If the coordinate system is not left-handed.
    """
    if not test_left_handed_cross_products():
        raise AssertionError(
            "Coordinate system is NOT left-handed. "
            "Expected X × Y = -Z, Y × Z = -X, Z × X = -Y"
        )


def main(argv: List[str] | None = None) -> int:
    """
    Main entry point for the left-hand coordinate test.

    Args:
        argv: Command-line arguments. Defaults to sys.argv[1:] if None.

    Returns:
        Exit code: 0 for success (left-handed), 1 for failure (right-handed).
    """
    parser = argparse.ArgumentParser(
        description="Test left-hand coordinate system handedness via cross-product sign."
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Print detailed test results"
    )
    args = parser.parse_args(argv)

    try:
        if args.verbose:
            print("Testing left-handed coordinate system...\n")

        is_left_handed = test_left_handed_cross_products(verbose=args.verbose)
        sign = compute_handedness_sign()

        if args.verbose:
            if is_left_handed:
                print("\nResult: LEFT-HANDED coordinate system confirmed.")
            else:
                print(f"\nResult: RIGHT-HANDED coordinate system (sign={sign:+d}).")
                print("Note: NumPy uses right-handed cross products by default.")

        return 0 if is_left_handed else 1

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
