#!/usr/bin/env python3
"""Tests for bin/edge_test_min_int_values.py — Boundary test for int64 min values.

Verifies that the edge test script correctly handles int64 minimum boundary values
without underflow in adapter math operations on frame_id.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Path to the edge test script (in parent bin/ directory)
EDGE_TEST_SCRIPT = (
    Path(__file__).parent.parent.parent / "bin" / "edge_test_min_int_values.py"
)


class TestEdgeTestMinIntValues:
    """Test suite for edge_test_min_int_values.py."""

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
        # Verify output shows success message
        assert "passed" in result.stdout.lower()

    def test_verbose_flag(self):
        """Verify --verbose flag produces detailed output."""
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT), "--verbose"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        # Verbose should show PASS/FAIL markers
        assert "[PASS]" in result.stdout
        assert "frame_id_boundary" in result.stdout

    def test_int64_min_boundary(self):
        """Verify int64 minimum value is handled correctly."""
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"

    def test_frame_id_range_validation(self):
        """Verify range validation includes edge cases."""
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT), "--verbose"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        # Verify all test values are validated
        assert "frame_id_range_validation" in result.stdout

    def test_arithmetic_operations(self):
        """Verify arithmetic operations don't cause underflow."""
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT), "--verbose"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        # Verify arithmetic test passes
        assert "frame_id_boundary" in result.stdout

    def test_comparison_operations(self):
        """Verify comparison operations work at boundary."""
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT), "--verbose"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        # The boundary test includes comparison checks
        assert "frame_id_boundary" in result.stdout or "boundary" in result.stdout.lower()
