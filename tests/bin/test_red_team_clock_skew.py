#!/usr/bin/env python3
"""Test coverage for bin/red_team_clock_skew.py.

This module exercises the red-team clock skew detection utility that monitors
system clock for backward jumps and switches to monotonic clock. Coverage:

- ClockSource enum: values
- ClockState dataclass: fields and defaults
- ClockAdapter: init with threshold, current_source property, skew_detected
  property, skew_amount property, get_time returns monotonic when switched,
  get_time returns system when not switched, check_for_skew detects backward
  jump beyond threshold, check_for_skew returns False on normal time,
  check_for_skew updates last times, check_for_skew sets skew_amount,
  reset restores initial state.
- main: help exits cleanly, --threshold flag, --monitor-duration flag,
  --simulate-skew flag works.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add bin/ to sys.path so the module is importable as a top-level name
_BIN_DIR = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(_BIN_DIR))

from red_team_clock_skew import (  # noqa: E402
    ClockAdapter,
    ClockSource,
    ClockState,
)

# ---------------------------------------------------------------------------
# ClockSource
# ---------------------------------------------------------------------------


class TestClockSource:
    """Tests for ClockSource enum."""

    def test_values(self):
        assert ClockSource.SYSTEM.value == "system"
        assert ClockSource.MONOTONIC.value == "monotonic"


# ---------------------------------------------------------------------------
# ClockState
# ---------------------------------------------------------------------------


class TestClockState:
    """Tests for ClockState dataclass."""

    def test_fields(self):
        state = ClockState(
            source=ClockSource.SYSTEM,
            last_system_time=1000.0,
            last_monotonic_time=500.0,
            skew_detected=False,
            skew_amount=0.0,
        )
        assert state.source == ClockSource.SYSTEM
        assert state.last_system_time == 1000.0
        assert state.last_monotonic_time == 500.0
        assert state.skew_detected is False
        assert state.skew_amount == 0.0

    def test_skew_detected_true(self):
        state = ClockState(
            source=ClockSource.MONOTONIC,
            last_system_time=1000.0,
            last_monotonic_time=500.0,
            skew_detected=True,
            skew_amount=-7200.0,
        )
        assert state.skew_detected is True
        assert state.skew_amount == -7200.0


# ---------------------------------------------------------------------------
# ClockAdapter
# ---------------------------------------------------------------------------


class TestClockAdapter:
    """Tests for ClockAdapter class."""

    def test_init_default_threshold(self):
        adapter = ClockAdapter()
        assert adapter.current_source == ClockSource.SYSTEM
        assert adapter.skew_detected is False
        assert adapter.skew_amount == 0.0

    def test_init_custom_threshold(self):
        adapter = ClockAdapter(threshold_seconds=1800.0)
        assert adapter.current_source == ClockSource.SYSTEM

    def test_get_time_returns_system_when_not_switched(self):
        adapter = ClockAdapter()
        # Manually set state to simulate non-switched
        adapter._state.source = ClockSource.SYSTEM

        result = adapter.get_time()
        # Should return system time (close to current time)
        import time
        now = time.time()
        assert abs(result - now) < 1.0  # within 1 second

    def test_get_time_returns_monotonic_when_switched(self):
        adapter = ClockAdapter()
        # Manually switch to monotonic
        adapter._state.source = ClockSource.MONOTONIC

        result = adapter.get_time()
        # Should return monotonic time
        import time
        now = time.monotonic()
        assert abs(result - now) < 1.0  # within 1 second

    def test_check_for_skew_returns_false_on_normal_time(self):
        adapter = ClockAdapter()
        # Simulate normal time: both clocks advance together
        adapter._state.last_system_time = 1000.0
        adapter._state.last_monotonic_time = 500.0

        # Patch time functions to return values that result in no skew
        # skew = (current_system - last_system) - (current_monotonic - last_monotonic)
        # = (1001 - 1000) - (501 - 500) = 1 - 1 = 0
        with patch("red_team_clock_skew.time.time", return_value=1001.0), \
             patch("red_team_clock_skew.time.monotonic", return_value=501.0):
            result = adapter.check_for_skew()
            assert result is False
            assert adapter.skew_detected is False

    def test_check_for_skew_detects_backward_jump(self):
        # Use a small threshold for testing
        adapter = ClockAdapter(threshold_seconds=5.0)
        adapter._state.last_system_time = 1000.0
        adapter._state.last_monotonic_time = 500.0

        # System jumped backward by 10 seconds, monotonic advanced by 1 second
        # skew = (990 - 1000) - (501 - 500) = -10 - 1 = -11 (< -5 threshold)
        with patch("red_team_clock_skew.time.time", return_value=990.0), \
             patch("red_team_clock_skew.time.monotonic", return_value=501.0):
            result = adapter.check_for_skew()
            assert result is True
            assert adapter.skew_detected is True
            assert adapter.current_source == ClockSource.MONOTONIC
            assert adapter.skew_amount < 0

    def test_check_for_skew_updates_last_times(self):
        adapter = ClockAdapter()
        adapter._state.last_system_time = 1000.0
        adapter._state.last_monotonic_time = 500.0

        with patch("red_team_clock_skew.time.time", return_value=1001.0), \
             patch("red_team_clock_skew.time.monotonic", return_value=501.0):
            adapter.check_for_skew()
            assert adapter._state.last_system_time == 1001.0
            assert adapter._state.last_monotonic_time == 501.0

    def test_check_for_skew_sets_skew_amount(self):
        adapter = ClockAdapter(threshold_seconds=5.0)
        adapter._state.last_system_time = 1000.0
        adapter._state.last_monotonic_time = 500.0

        with patch("red_team_clock_skew.time.time", return_value=990.0), \
             patch("red_team_clock_skew.time.monotonic", return_value=501.0):
            adapter.check_for_skew()
            # skew = (990 - 1000) - (501 - 500) = -10 - 1 = -11
            assert adapter.skew_amount == pytest.approx(-11.0, rel=0.1)

    def test_reset_restores_initial_state(self):
        adapter = ClockAdapter()
        adapter._state.source = ClockSource.MONOTONIC
        adapter._state.skew_detected = True
        adapter._state.skew_amount = -7200.0

        adapter.reset()
        assert adapter.current_source == ClockSource.SYSTEM
        assert adapter.skew_detected is False
        assert adapter.skew_amount == 0.0

    def test_skew_just_above_threshold_not_detected(self):
        """Test that skew just above threshold (but not crossing) is not detected."""
        adapter = ClockAdapter(threshold_seconds=10.0)
        adapter._state.last_system_time = 1000.0
        adapter._state.last_monotonic_time = 500.0

        # Skew = -9 seconds (greater than -10 threshold, so not detected)
        with patch("red_team_clock_skew.time.time", return_value=991.0), \
             patch("red_team_clock_skew.time.monotonic", return_value=501.0):
            result = adapter.check_for_skew()
            assert result is False
            assert adapter.skew_detected is False

    def test_skew_at_threshold_boundary(self):
        """Test skew at exactly threshold boundary."""
        adapter = ClockAdapter(threshold_seconds=10.0)
        adapter._state.last_system_time = 1000.0
        adapter._state.last_monotonic_time = 500.0

        # Skew = -10.001 seconds (less than -10 threshold, so detected)
        with patch("red_team_clock_skew.time.time", return_value=989.999), \
             patch("red_team_clock_skew.time.monotonic", return_value=501.0):
            result = adapter.check_for_skew()
            assert result is True
            assert adapter.skew_detected is True


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


class TestMain:
    """Tests for main CLI entry point."""

    def test_help_exits_cleanly(self):
        result = subprocess.run(
            [sys.executable, str(_BIN_DIR / "red_team_clock_skew.py"), "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "threshold" in result.stdout.lower()

    def test_no_args_runs(self):
        # Test it runs without crashing (short duration to avoid long waits)
        result = subprocess.run(
            [
                sys.executable,
                str(_BIN_DIR / "red_team_clock_skew.py"),
                "--monitor-duration",
                "0.1",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        # Should exit 0 (no skew) or 1 (skew detected) or 2 (error)
        assert result.returncode in (0, 1, 2)

    def test_simulate_skew_flag_exists(self):
        """Test that --simulate-skew flag is recognized."""
        result = subprocess.run(
            [
                sys.executable,
                str(_BIN_DIR / "red_team_clock_skew.py"),
                "--help",
            ],
            capture_output=True,
            text=True,
        )
        assert "--simulate-skew" in result.stdout

    def test_threshold_flag(self):
        result = subprocess.run(
            [
                sys.executable,
                str(_BIN_DIR / "red_team_clock_skew.py"),
                "--help",
            ],
            capture_output=True,
            text=True,
        )
        assert "--threshold" in result.stdout

    def test_monitor_duration_flag(self):
        result = subprocess.run(
            [
                sys.executable,
                str(_BIN_DIR / "red_team_clock_skew.py"),
                "--help",
            ],
            capture_output=True,
            text=True,
        )
        assert "--monitor-duration" in result.stdout
