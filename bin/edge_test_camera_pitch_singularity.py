#!/usr/bin/env python3
"""
Edge test for camera pitch singularity at exactly ±90 degrees (gimbal-lock case).

Purpose:
    Boundary test: pitch exactly 90.0 / -90.0 gimbal-lock case — adapter clamps not wraps.
"""

import argparse
import math
import os
import sys
import tempfile
from typing import List, Optional, Tuple


def clamp_pitch(pitch_deg: float, min_pitch: float = -90.0, max_pitch: float = 90.0) -> float:
    """Clamp pitch angle to valid range (adapter clamps, not wraps)."""
    return max(min_pitch, min(max_pitch, pitch_deg))


def is_singularity(pitch_deg: float, tolerance: float = 1e-9) -> bool:
    """Check if pitch angle is at gimbal-lock singularity."""
    return abs(abs(pitch_deg) - 90.0) < tolerance


def test_singularity_behavior(pitch_deg: float) -> Tuple[bool, str]:
    """Test singularity handling for a given pitch angle."""
    clamped = clamp_pitch(pitch_deg)

    # Exact singularities: clamped should equal boundary
    if is_singularity(pitch_deg):
        expected = 90.0 if pitch_deg > 0 else -90.0
        if abs(clamped - expected) > 1e-9:
            return False, f"Singularity {pitch_deg}° not clamped to {expected}°, got {clamped}"
        # Verify trig at singularity
        pitch_rad = math.radians(pitch_deg)
        if abs(math.cos(pitch_rad)) > 1e-6:
            return False, f"Cosine at {pitch_deg}° should be ~0"
        return True, f"Singularity {pitch_deg}° handled correctly"

    # Beyond ±90°: should clamp (not wrap)
    if pitch_deg > 90.0:
        return (abs(clamped - 90.0) < 1e-9, f"Beyond +90° ({pitch_deg}°) clamped to {clamped}")
    if pitch_deg < -90.0:
        return (abs(clamped + 90.0) < 1e-9, f"Beyond -90° ({pitch_deg}°) clamped to {clamped}")

    # Normal range: pass through unchanged
    return (abs(clamped - pitch_deg) < 1e-9, f"Angle {pitch_deg}° handled correctly")


def run_all_tests() -> Tuple[bool, List[str]]:
    """Run comprehensive singularity tests."""
    failures: List[str] = []

    # Exact singularities
    for angle in [90.0, -90.0]:
        success, msg = test_singularity_behavior(angle)
        if not success:
            failures.append(msg)

    # Near singularities (should clamp)
    for angle in [89.9999999, 90.0000001, -89.9999999, -90.0000001]:
        success, msg = test_singularity_behavior(angle)
        if not success:
            failures.append(msg)

    # Beyond singularity (should clamp)
    for angle in [91.0, 100.0, 180.0, -91.0, -100.0, -180.0]:
        success, msg = test_singularity_behavior(angle)
        if not success:
            failures.append(msg)

    # Normal range
    for angle in [0.0, 45.0, -45.0, 30.0, -30.0]:
        success, msg = test_singularity_behavior(angle)
        if not success:
            failures.append(msg)

    return len(failures) == 0, failures


def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point for the edge test."""
    parser = argparse.ArgumentParser(
        description="Edge test for camera pitch singularity at ±90 degrees (gimbal-lock case)"
    )
    parser.add_argument("--angle", "-a", type=float, help="Test specific pitch angle (degrees)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed output")
    parser.add_argument(
        "--temp-dir", action="store_true", help="Use temp directory (no hardcoded paths)"
    )

    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    temp_dir = None
    if args.temp_dir:
        temp_dir = tempfile.mkdtemp(prefix="pitch_test_")
        print(f"Using temporary directory: {temp_dir}")

    try:
        if args.angle is not None:
            success, msg = test_singularity_behavior(args.angle)
            print(msg)
            if args.verbose:
                clamped = clamp_pitch(args.angle)
                singular = is_singularity(args.angle)
                print(f"  Input: {args.angle}°, Clamped: {clamped}, Is singularity: {singular}")
            return 0 if success else 1
        else:
            print("Camera Pitch Singularity Edge Test")
            print("=" * 40)
            success, failures = run_all_tests()
            if success:
                print("All tests passed!")
                return 0
            print(f"FAILED: {len(failures)} test(s)")
            for f in failures:
                print(f"  - {f}")
            return 1
    finally:
        if temp_dir and os.path.exists(temp_dir):
            os.rmdir(temp_dir)


if __name__ == "__main__":
    sys.exit(main())
