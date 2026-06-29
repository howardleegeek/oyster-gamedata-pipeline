#!/usr/bin/env python3
"""Tests for bin/edge_test_high_precision_floats.py — Boundary test for tiny float round-trip.

Verifies that the edge test script correctly handles JSON encode/decode of
extremely small camera position values (1e-300, subnormals, signed tiny)
without precision loss. Guards against silent truncation in camera
calibration pipelines.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Path to the edge test script (in parent bin/ directory)
EDGE_TEST_SCRIPT = (
    Path(__file__).parent.parent.parent / "bin" / "edge_test_high_precision_floats.py"
)


class TestEdgeTestHighPrecisionFloats:
    """Test suite for edge_test_high_precision_floats.py."""

    def test_script_exists(self):
        """Verify the edge test script exists and is executable."""
        assert EDGE_TEST_SCRIPT.exists(), f"Script not found: {EDGE_TEST_SCRIPT}"

    def test_runs_successfully(self):
        """Verify script runs successfully with default arguments."""
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        # Verify output shows PASS message
        assert "PASS" in result.stdout
        assert "camera positions survived JSON round-trip" in result.stdout

    def test_default_tolerance_message(self):
        """Verify default tolerance (1e-15) is reported in success output."""
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        # Default tolerance of 1e-15 should be reported
        assert "1.00e-15" in result.stdout

    def test_custom_tolerance_loose(self):
        """Verify a looser --tolerance value still passes."""
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT), "--tolerance", "1e-10"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        assert "PASS" in result.stdout
        assert "1.00e-10" in result.stdout

    def test_custom_tolerance_extreme(self):
        """Verify an extreme --tolerance value still completes without crashing."""
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT), "--tolerance", "1e-500"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        # Extreme tolerance should still produce PASS (bit-exact round-trip)
        assert "PASS" in result.stdout

    def test_position_count(self):
        """Verify all 5 test positions are reported in output."""
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        # Script reports "all 5 camera positions" in its PASS message
        assert "5 camera positions" in result.stdout

    def test_no_precision_loss_for_subnormals(self):
        """Verify subnormal-range floats (1e-308 to 1e-100) survive round-trip."""
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT), "--tolerance", "1e-15"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        # If precision was lost for subnormals, exit code would be 1 with FAIL
        assert "FAIL" not in result.stdout

    def test_no_stderr_on_success(self):
        """Verify no error output to stderr when round-trip succeeds."""
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        # On success, stderr should be empty
        assert result.stderr == "", f"Unexpected stderr output: {result.stderr!r}"
