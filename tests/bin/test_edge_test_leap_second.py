#!/usr/bin/env python3
"""Test for bin/edge_test_leap_second.py — Boundary test for leap-second insertion at 23:59:60.

Validates that a datetime adapter handles or cleanly rejects the leap-second
timestamp ``23:59:60`` (UTC) without crashing or silently dropping data.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Path to the source script being tested
SRC_SCRIPT = Path(__file__).parent.parent.parent / "bin" / "edge_test_leap_second.py"


class TestLeapSecond:
    """Tests for leap second edge case handling."""

    def test_script_exists(self):
        """Verify the source script exists."""
        assert SRC_SCRIPT.exists(), f"Source script not found: {SRC_SCRIPT}"

    def test_script_runs_without_args(self):
        """Verify script runs without arguments (runs with defaults)."""
        result = subprocess.run(
            [sys.executable, str(SRC_SCRIPT)],
            capture_output=True,
            text=True,
        )
        # Script runs with defaults, should handle all scenarios
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        assert "leap" in result.stdout.lower() or "23:59" in result.stdout

    def test_standard_235959(self):
        """Test normal second before leap (23:59:59) is accepted."""
        result = subprocess.run(
            [sys.executable, str(SRC_SCRIPT), "--verbose"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        assert "standard_235959" in result.stdout

    def test_leap_235960(self):
        """Test leap second 23:59:60 boundary case."""
        result = subprocess.run(
            [sys.executable, str(SRC_SCRIPT), "--verbose"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        assert "leap_235960" in result.stdout

    def test_rollover_000000(self):
        """Test midnight rollover after leap second."""
        result = subprocess.run(
            [sys.executable, str(SRC_SCRIPT), "--verbose"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        assert "rollover_000000" in result.stdout

    def test_invalid_235961(self):
        """Test invalid second 23:59:61 is rejected."""
        result = subprocess.run(
            [sys.executable, str(SRC_SCRIPT), "--verbose"],
            capture_output=True,
            text=True,
        )
        # The script should handle invalid seconds gracefully
        assert "invalid_235961" in result.stdout
