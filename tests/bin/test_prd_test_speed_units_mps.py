#!/usr/bin/env python3
"""
Tests for bin/prd_test_speed_units_mps.py

PRD p3 #6: linear_velocity in m/s — bound to player walk run sprint speeds.
Validates that linear_velocity values are correctly expressed in meters per
second (m/s) and fall within expected bounds for human movement.

Core functions:
- classify_speed: classify velocity (m/s) into MovementType (WALK/RUN/SPRINT/INVALID)
- validate_velocity: return (is_valid, message) for a velocity value
- run_all_tests: built-in self-test, returns exit code
"""

import pytest

from bin.prd_test_speed_units_mps import (
    SPEED_BOUNDS,
    MovementType,
    SpeedBounds,
    classify_speed,
    run_all_tests,
    validate_velocity,
)


class TestMovementType:
    """Tests for MovementType enum."""

    def test_movement_type_values(self):
        """Test MovementType enum members and their string values."""
        assert MovementType.WALK.value == "walk"
        assert MovementType.RUN.value == "run"
        assert MovementType.SPRINT.value == "sprint"
        assert MovementType.INVALID.value == "invalid"

    def test_movement_type_count(self):
        """Test that MovementType has exactly 4 members."""
        assert len(MovementType) == 4


class TestSpeedBounds:
    """Tests for SpeedBounds dataclass."""

    def test_dataclass_fields(self):
        """Test SpeedBounds is a dataclass with expected fields."""
        bounds = SpeedBounds(min_mps=0.8, max_mps=2.0, typical_mps=1.4)
        assert bounds.min_mps == 0.8
        assert bounds.max_mps == 2.0
        assert bounds.typical_mps == 1.4

    def test_speed_bounds_table_complete(self):
        """Test the global SPEED_BOUNDS table has all MovementType entries."""
        assert MovementType.WALK in SPEED_BOUNDS
        assert MovementType.RUN in SPEED_BOUNDS
        assert MovementType.SPRINT in SPEED_BOUNDS
        # INVALID has no bounds (it represents out-of-range values)
        assert MovementType.INVALID not in SPEED_BOUNDS

    def test_speed_bounds_walk_range(self):
        """Test WALK bounds match PRD expectation (0.8 - 2.0 m/s)."""
        bounds = SPEED_BOUNDS[MovementType.WALK]
        assert bounds.min_mps == 0.8
        assert bounds.max_mps == 2.0
        assert bounds.typical_mps == 1.4

    def test_speed_bounds_run_range(self):
        """Test RUN bounds match PRD expectation (2.0 - 6.0 m/s)."""
        bounds = SPEED_BOUNDS[MovementType.RUN]
        assert bounds.min_mps == 2.0
        assert bounds.max_mps == 6.0
        assert bounds.typical_mps == 4.0

    def test_speed_bounds_sprint_range(self):
        """Test SPRINT bounds match PRD expectation (6.0 - 12.5 m/s)."""
        bounds = SPEED_BOUNDS[MovementType.SPRINT]
        assert bounds.min_mps == 6.0
        assert bounds.max_mps == 12.5
        assert bounds.typical_mps == 9.0

    def test_speed_bounds_continuity(self):
        """Test that bounds are continuous (no gaps or overlap)."""
        walk = SPEED_BOUNDS[MovementType.WALK]
        run = SPEED_BOUNDS[MovementType.RUN]
        sprint = SPEED_BOUNDS[MovementType.SPRINT]
        # walk.max should touch run.min
        assert walk.max_mps == run.min_mps
        # run.max should touch sprint.min
        assert run.max_mps == sprint.min_mps


class TestClassifySpeed:
    """Tests for classify_speed function."""

    def test_negative_is_invalid(self):
        """Test negative velocity returns INVALID."""
        assert classify_speed(-1.0) == MovementType.INVALID
        assert classify_speed(-0.001) == MovementType.INVALID
        assert classify_speed(-100.0) == MovementType.INVALID

    def test_walk_range(self):
        """Test velocities in walk range are classified as WALK.

        Note: due to inclusive bounds and dict iteration order, the upper
        boundary 2.0 m/s is classified as WALK (not RUN). This documents
        existing behavior — the WALK band [0.8, 2.0] claims 2.0 first.
        """
        assert classify_speed(0.8) == MovementType.WALK
        assert classify_speed(1.0) == MovementType.WALK
        assert classify_speed(1.4) == MovementType.WALK
        assert classify_speed(1.9) == MovementType.WALK
        assert classify_speed(2.0) == MovementType.WALK  # boundary: WALK wins

    def test_run_range(self):
        """Test velocities strictly inside the run range are classified as RUN.

        Note: 2.0 and 6.0 are boundary values claimed by the previous band
        (WALK claims 2.0, SPRINT claims 6.0 — see walk_range and sprint_range
        tests). Use values strictly above 2.0 and strictly below 6.0.
        """
        assert classify_speed(2.01) == MovementType.RUN
        assert classify_speed(3.0) == MovementType.RUN
        assert classify_speed(4.0) == MovementType.RUN
        assert classify_speed(5.0) == MovementType.RUN
        assert classify_speed(5.99) == MovementType.RUN

    def test_sprint_range(self):
        """Test velocities strictly inside the sprint range are classified as SPRINT.

        Note: at the 6.0 m/s boundary, the RUN band (declared before SPRINT
        in SPEED_BOUNDS) claims the value, so 6.0 is classified as RUN.
        Use values strictly above 6.0 to hit SPRINT.
        """
        assert classify_speed(6.01) == MovementType.SPRINT
        assert classify_speed(7.0) == MovementType.SPRINT
        assert classify_speed(9.0) == MovementType.SPRINT
        assert classify_speed(11.0) == MovementType.SPRINT
        assert classify_speed(12.5) == MovementType.SPRINT

    def test_exceeds_sprint_max_is_invalid(self):
        """Test velocities exceeding sprint max return INVALID."""
        assert classify_speed(12.6) == MovementType.INVALID
        assert classify_speed(15.0) == MovementType.INVALID
        assert classify_speed(100.0) == MovementType.INVALID

    def test_below_walk_min_is_walk(self):
        """Test velocities below walk min (but >= 0) are classified as WALK.

        Note: classify_speed has a quirk where values in [0, walk.min) skip
        the range loop and return WALK as the default. This documents the
        existing behavior — classify_speed does NOT flag sub-walk velocities
        as INVALID; only validate_velocity does.
        """
        assert classify_speed(0.0) == MovementType.WALK
        assert classify_speed(0.5) == MovementType.WALK
        assert classify_speed(0.7) == MovementType.WALK

    def test_boundary_at_2_0_is_walk(self):
        """Test boundary quirk: 2.0 m/s is WALK (first match wins)."""
        # Due to inclusive bounds and insertion order, WALK claims 2.0
        # before RUN gets a chance. This documents the quirk.
        assert classify_speed(2.0) == MovementType.WALK

    def test_boundary_at_6_0_is_run(self):
        """Test boundary quirk: 6.0 m/s is RUN (first match wins)."""
        # RUN is declared before SPRINT, so RUN claims 6.0 first.
        assert classify_speed(6.0) == MovementType.RUN


class TestValidateVelocity:
    """Tests for validate_velocity function."""

    def test_negative_velocity_invalid(self):
        """Test negative velocity returns (False, message)."""
        is_valid, msg = validate_velocity(-1.0)
        assert is_valid is False
        assert "negative" in msg.lower()
        assert "-1.0" in msg

    def test_exceeds_sprint_max_invalid(self):
        """Test velocity above sprint max returns (False, message)."""
        is_valid, msg = validate_velocity(15.0)
        assert is_valid is False
        assert "exceeds sprint max" in msg.lower()
        assert "15.0" in msg

    def test_walk_velocity_valid(self):
        """Test walk-range velocity returns (True, message)."""
        is_valid, msg = validate_velocity(1.4)
        assert is_valid is True
        assert "walk" in msg.lower()
        assert "1.4" in msg

    def test_run_velocity_valid(self):
        """Test run-range velocity returns (True, message)."""
        is_valid, msg = validate_velocity(4.0)
        assert is_valid is True
        assert "run" in msg.lower()
        assert "4.0" in msg

    def test_sprint_velocity_valid(self):
        """Test sprint-range velocity returns (True, message)."""
        is_valid, msg = validate_velocity(9.0)
        assert is_valid is True
        assert "sprint" in msg.lower()
        assert "9.0" in msg

    def test_sub_walk_still_valid(self):
        """Test sub-walk velocities (0 to walk.min) are still valid."""
        is_valid, msg = validate_velocity(0.5)
        assert is_valid is True
        assert "walk" in msg.lower()

    def test_zero_velocity_valid(self):
        """Test zero velocity is valid (player standing still)."""
        is_valid, msg = validate_velocity(0.0)
        assert is_valid is True
        assert "walk" in msg.lower()

    def test_message_contains_units(self):
        """Test all messages include the m/s unit."""
        for v in [-1.0, 1.4, 4.0, 9.0, 15.0]:
            _, msg = validate_velocity(v)
            assert "m/s" in msg


class TestRunAllTests:
    """Tests for the run_all_tests self-test function."""

    def test_returns_zero_on_success(self):
        """Test that run_all_tests returns 0 (no failures)."""
        exit_code = run_all_tests()
        assert exit_code == 0

    def test_does_not_raise(self):
        """Test that run_all_tests runs without raising exceptions."""
        # Should complete cleanly
        result = run_all_tests()
        assert isinstance(result, int)


class TestIntegration:
    """End-to-end integration tests for the speed validation pipeline."""

    @pytest.mark.parametrize(
        "velocity,expected_type,expected_valid",
        [
            (1.4, MovementType.WALK, True),
            (4.0, MovementType.RUN, True),
            (9.0, MovementType.SPRINT, True),
            (0.0, MovementType.WALK, True),
            (-1.0, MovementType.INVALID, False),
            (15.0, MovementType.INVALID, False),
            (1.0, MovementType.WALK, True),
            (3.0, MovementType.RUN, True),
            (8.0, MovementType.SPRINT, True),
        ],
    )
    def test_classify_and_validate_match(
        self, velocity: float, expected_type: MovementType, expected_valid: bool
    ):
        """Test that classify_speed and validate_velocity agree across the test matrix."""
        actual_type = classify_speed(velocity)
        is_valid, _ = validate_velocity(velocity)
        assert actual_type == expected_type
        assert is_valid == expected_valid
