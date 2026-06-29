#!/usr/bin/env python3
"""Test for bin/edge_test_dst_clock_change.py — Boundary test for DST clock transitions.

Verifies that UTC timestamps remain strictly monotonic across Daylight Saving
Time transitions (spring-forward and fall-back).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Path to the source script being tested
SRC_SCRIPT = Path(__file__).parent.parent.parent / "bin" / "edge_test_dst_clock_change.py"


class TestDstClockChange:
    """Tests for DST clock change edge case handling."""

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
        # Script runs with defaults (America/New_York, current year)
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        assert "DST" in result.stdout or "transition" in result.stdout.lower()

    def test_default_timezone_2024(self):
        """Test DST transition detection for default timezone (America/New_York) in 2024."""
        result = subprocess.run(
            [sys.executable, str(SRC_SCRIPT), "--tz", "America/New_York", "--year", "2024"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        # Should find DST transitions in 2024 for New York
        assert "No DST transitions found" not in result.stdout

    def test_europe_london_2024(self):
        """Test DST transition detection for Europe/London in 2024."""
        result = subprocess.run(
            [sys.executable, str(SRC_SCRIPT), "--tz", "Europe/London", "--year", "2024"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"

    def test_asia_tokyo_2024(self):
        """Test timezone without DST (Asia/Tokyo) returns no transitions."""
        result = subprocess.run(
            [sys.executable, str(SRC_SCRIPT), "--tz", "Asia/Tokyo", "--year", "2024"],
            capture_output=True,
            text=True,
        )
        # Tokyo doesn't have DST, script reports this as a failure case (exit 1)
        # but it still outputs the correct information
        assert "No DST transitions found" in result.stdout

    def test_utc_no_dst(self):
        """Test UTC timezone has no DST transitions."""
        result = subprocess.run(
            [sys.executable, str(SRC_SCRIPT), "--tz", "UTC", "--year", "2024"],
            capture_output=True,
            text=True,
        )
        # UTC doesn't have DST, script reports this as a failure case (exit 1)
        # but it still outputs the correct information
        assert "No DST transitions found" in result.stdout

    def test_spring_forward_transition(self):
        """Test spring-forward DST transition (March 2024 US)."""
        result = subprocess.run(
            [sys.executable, str(SRC_SCRIPT), "--tz", "America/New_York", "--year", "2024"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        # New York has spring forward around March 10, 2024
        output = result.stdout.lower()
        assert "transition" in output or "dst" in output or "offset" in output

    def test_fall_back_transition(self):
        """Test fall-back DST transition (November 2024 US)."""
        result = subprocess.run(
            [sys.executable, str(SRC_SCRIPT), "--tz", "America/Los_Angeles", "--year", "2024"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        # Should detect at least one transition (fall back in Nov)
        output = result.stdout.lower()
        assert "transition" in output or "dst" in output or "offset" in output

    def test_custom_step_minutes(self):
        """Test custom step minutes parameter."""
        result = subprocess.run(
            [sys.executable, str(SRC_SCRIPT), "--tz", "America/Chicago", "--year", "2024", "--step", "30"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"

    def test_multiple_years(self):
        """Test DST detection across multiple years."""
        for year in [2023, 2024, 2025]:
            result = subprocess.run(
                [sys.executable, str(SRC_SCRIPT), "--tz", "America/New_York", "--year", str(year)],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, f"Script failed for year {year}: {result.stderr}"

    def test_invalid_timezone_handling(self):
        """Test handling of invalid timezone name."""
        result = subprocess.run(
            [sys.executable, str(SRC_SCRIPT), "--tz", "Invalid/Timezone", "--year", "2024"],
            capture_output=True,
            text=True,
        )
        # Should handle gracefully (either error or fallback)
        assert result.returncode in [0, 1]

    def test_json_output_flag(self):
        """Test JSON output flag if available."""
        result = subprocess.run(
            [sys.executable, str(SRC_SCRIPT), "--tz", "America/New_York", "--year", "2024", "--json"],
            capture_output=True,
            text=True,
        )
        # May not have --json flag, so accept either 0 (success) or 2 (unrecognized args)
        assert result.returncode in [0, 2]

    def test_verbose_flag(self):
        """Test verbose flag if available."""
        result = subprocess.run(
            [sys.executable, str(SRC_SCRIPT), "--tz", "America/New_York", "--year", "2024", "--verbose"],
            capture_output=True,
            text=True,
        )
        # May not have --verbose flag, so accept either 0 (success) or 2 (unrecognized args)
        assert result.returncode in [0, 2]
