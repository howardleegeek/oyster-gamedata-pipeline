#!/usr/bin/env python3
"""Tests for bin/prd_test_speed_units_mps.py"""

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "bin" / "prd_test_speed_units_mps.py"


def _load_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("speed_units_mps", str(SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["speed_units_mps"] = mod
    spec.loader.exec_module(mod)
    return mod


mod = _load_module()
MovementType = mod.MovementType
SpeedBounds = mod.SpeedBounds
SPEED_BOUNDS = mod.SPEED_BOUNDS
classify_speed = mod.classify_speed
validate_velocity = mod.validate_velocity
run_all_tests = mod.run_all_tests
main = mod.main


# ---------------------------------------------------------------------------
# Unit tests via import
# ---------------------------------------------------------------------------


class TestSpeedBounds:
    """Tests for SpeedBounds dataclass and SPEED_BOUNDS constants."""

    def test_walk_bounds(self):
        b = SPEED_BOUNDS[MovementType.WALK]
        assert b.min_mps == 0.8
        assert b.max_mps == 2.0
        assert b.typical_mps == 1.4

    def test_run_bounds(self):
        b = SPEED_BOUNDS[MovementType.RUN]
        assert b.min_mps == 2.0
        assert b.max_mps == 6.0
        assert b.typical_mps == 4.0

    def test_sprint_bounds(self):
        b = SPEED_BOUNDS[MovementType.SPRINT]
        assert b.min_mps == 6.0
        assert b.max_mps == 12.5
        assert b.typical_mps == 9.0

    def test_all_movement_types_present(self):
        assert set(SPEED_BOUNDS.keys()) == {
            MovementType.WALK,
            MovementType.RUN,
            MovementType.SPRINT,
        }


class TestClassifySpeed:
    """Tests for classify_speed function."""

    def test_walk_speed(self):
        assert classify_speed(1.4) == MovementType.WALK

    def test_walk_lower_bound(self):
        assert classify_speed(0.8) == MovementType.WALK

    def test_walk_upper_bound(self):
        # 2.0 is walk's max and is checked first, so it matches WALK
        assert classify_speed(2.0) == MovementType.WALK

    def test_run_speed(self):
        assert classify_speed(4.0) == MovementType.RUN

    def test_run_just_above_walk_max(self):
        # 2.1 is just above walk's max, so it falls into RUN
        assert classify_speed(2.1) == MovementType.RUN

    def test_run_upper_bound(self):
        # 6.0 is run's max and is checked before sprint, so it matches RUN
        assert classify_speed(6.0) == MovementType.RUN

    def test_sprint_speed(self):
        assert classify_speed(9.0) == MovementType.SPRINT

    def test_sprint_lower_bound(self):
        # 6.0 is sprint's min but RUN catches it first
        assert classify_speed(6.0) == MovementType.RUN

    def test_sprint_just_above_run_max(self):
        assert classify_speed(6.1) == MovementType.SPRINT

    def test_sprint_upper_bound(self):
        assert classify_speed(12.5) == MovementType.SPRINT

    def test_negative_is_invalid(self):
        assert classify_speed(-1.0) == MovementType.INVALID

    def test_zero_is_walk(self):
        # 0.0 falls below walk min (0.8) but is not negative,
        # so it falls through to the default WALK return
        assert classify_speed(0.0) == MovementType.WALK

    def test_exceeds_sprint_max_is_invalid(self):
        assert classify_speed(15.0) == MovementType.INVALID

    def test_just_above_sprint_max_is_invalid(self):
        assert classify_speed(12.6) == MovementType.INVALID


class TestValidateVelocity:
    """Tests for validate_velocity function."""

    def test_valid_walk(self):
        valid, msg = validate_velocity(1.4)
        assert valid is True
        assert "walk" in msg

    def test_valid_run(self):
        valid, msg = validate_velocity(4.0)
        assert valid is True
        assert "run" in msg

    def test_valid_sprint(self):
        valid, msg = validate_velocity(9.0)
        assert valid is True
        assert "sprint" in msg

    def test_negative_is_invalid(self):
        valid, msg = validate_velocity(-1.0)
        assert valid is False
        assert "negative" in msg

    def test_exceeds_max_is_invalid(self):
        valid, msg = validate_velocity(15.0)
        assert valid is False
        assert "exceeds sprint max" in msg

    def test_zero_is_valid(self):
        valid, msg = validate_velocity(0.0)
        assert valid is True

    def test_message_contains_velocity_value(self):
        _, msg = validate_velocity(5.5)
        assert "5.5" in msg

    def test_message_contains_mps_unit(self):
        _, msg = validate_velocity(3.0)
        assert "m/s" in msg


class TestRunAllTests:
    """Tests for run_all_tests function."""

    def test_all_builtin_tests_pass(self, capsys):
        exit_code = run_all_tests()
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "FAIL" not in captured.out

    def test_builtin_tests_produce_output(self, capsys):
        run_all_tests()
        captured = capsys.readouterr()
        assert "PASS" in captured.out


class TestCLI:
    """Tests for the CLI interface via subprocess."""

    def _run(self, args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT)] + args,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_cli_all_tests_passes(self):
        r = self._run(["--all-tests"])
        assert r.returncode == 0
        assert "PASS" in r.stdout

    def test_cli_all_tests_short_flag(self):
        r = self._run(["-a"])
        assert r.returncode == 0

    def test_cli_valid_velocity(self):
        r = self._run(["--velocity", "4.0"])
        assert r.returncode == 0
        assert "run" in r.stdout

    def test_cli_invalid_velocity_negative(self):
        r = self._run(["--velocity", "-5.0"])
        assert r.returncode == 1
        assert "negative" in r.stdout

    def test_cli_invalid_velocity_too_fast(self):
        r = self._run(["--velocity", "20.0"])
        assert r.returncode == 1
        assert "exceeds sprint max" in r.stdout

    def test_cli_short_velocity_flag(self):
        r = self._run(["-v", "1.4"])
        assert r.returncode == 0
        assert "walk" in r.stdout

    def test_cli_no_args_shows_help(self):
        r = self._run([])
        assert r.returncode == 0
        assert "usage" in r.stdout.lower() or "validate" in r.stdout.lower()

    def test_cli_boundary_velocity(self):
        r = self._run(["--velocity", "12.5"])
        assert r.returncode == 0
        assert "sprint" in r.stdout
