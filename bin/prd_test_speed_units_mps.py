#!/usr/bin/env python3
"""
G077 · bin/prd_test_speed_units_mps.py

PRD p3 #6: linear_velocity in m/s — bound to player walk run sprint speeds.

Validates that linear_velocity values are correctly expressed in meters per
second (m/s) and fall within expected bounds for human movement.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class MovementType(Enum):
    """Classification of movement speed types."""
    WALK = "walk"
    RUN = "run"
    SPRINT = "sprint"
    INVALID = "invalid"


@dataclass
class SpeedBounds:
    """Speed bounds in m/s for a movement type."""
    min_mps: float
    max_mps: float
    typical_mps: float


# Standard human movement speed bounds in m/s
SPEED_BOUNDS: dict[MovementType, SpeedBounds] = {
    MovementType.WALK: SpeedBounds(min_mps=0.8, max_mps=2.0, typical_mps=1.4),
    MovementType.RUN: SpeedBounds(min_mps=2.0, max_mps=6.0, typical_mps=4.0),
    MovementType.SPRINT: SpeedBounds(min_mps=6.0, max_mps=12.5, typical_mps=9.0),
}


def classify_speed(velocity_mps: float) -> MovementType:
    """Classify a velocity value into movement type based on m/s bounds."""
    if velocity_mps < 0:
        return MovementType.INVALID
    for movement_type, bounds in SPEED_BOUNDS.items():
        if bounds.min_mps <= velocity_mps <= bounds.max_mps:
            return movement_type
    if velocity_mps > SPEED_BOUNDS[MovementType.SPRINT].max_mps:
        return MovementType.INVALID
    return MovementType.WALK


def validate_velocity(velocity_mps: float) -> tuple[bool, str]:
    """Validate that a velocity value is within acceptable human movement bounds."""
    if velocity_mps < 0:
        return False, f"Invalid velocity: {velocity_mps} m/s (negative)"
    if velocity_mps > SPEED_BOUNDS[MovementType.SPRINT].max_mps:
        return False, f"Invalid velocity: {velocity_mps} m/s (exceeds sprint max)"
    movement_type = classify_speed(velocity_mps)
    return True, f"Valid {movement_type.value} speed: {velocity_mps} m/s"


def run_all_tests() -> int:
    """Run all built-in speed unit validation tests. Returns exit code."""
    test_cases = [
        (1.4, MovementType.WALK, True), (4.0, MovementType.RUN, True),
        (9.0, MovementType.SPRINT, True), (0.0, MovementType.WALK, True),
        (-1.0, MovementType.INVALID, False), (15.0, MovementType.INVALID, False),
        (1.0, MovementType.WALK, True), (3.0, MovementType.RUN, True),
        (8.0, MovementType.SPRINT, True),
    ]
    failures = 0
    for velocity, expected_type, expected_valid in test_cases:
        actual_type = classify_speed(velocity)
        is_valid, _ = validate_velocity(velocity)
        if actual_type != expected_type or is_valid != expected_valid:
            print(f"FAIL: {velocity} m/s -> {actual_type.value} (expected {expected_type.value})")
            failures += 1
        else:
            print(f"PASS: {velocity} m/s -> {actual_type.value}")
    return 1 if failures > 0 else 0


def main(argv: Optional[list[str]] = None) -> int:
    """Main entry point with argparse CLI. Returns exit code."""
    parser = argparse.ArgumentParser(
        description="Validate linear_velocity in m/s against player movement bounds."
    )
    parser.add_argument("--velocity", "-v", type=float, help="Velocity value in m/s to validate")
    parser.add_argument("--all-tests", "-a", action="store_true", help="Run all built-in tests")
    args = parser.parse_args(argv)

    if args.all_tests:
        return run_all_tests()
    if args.velocity is not None:
        is_valid, message = validate_velocity(args.velocity)
        print(message)
        return 0 if is_valid else 1
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
