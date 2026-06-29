#!/usr/bin/env python3
"""Tests for bin/edge_test_max_int_values.py — Boundary test for int64 max values.

Verifies that the edge test script correctly handles int64 boundary values
without overflow in adapter math operations on frame_id.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# Path to the edge test script (in parent bin/ directory)
EDGE_TEST_SCRIPT = (
    Path(__file__).parent.parent.parent / "bin" / "edge_test_max_int_values.py"
)


class TestEdgeTestMaxIntValues:
    """Test suite for edge_test_max_int_values.py."""

    def test_script_exists(self):
        """Verify the edge test script exists and is executable."""
        assert EDGE_TEST_SCRIPT.exists(), f"Script not found: {EDGE_TEST_SCRIPT}"

    def test_runs_without_args_fails(self):
        """Verify script requires arguments (exits non-zero without args)."""
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT)],
            capture_output=True,
            text=True,
        )
        # Should fail without arguments
        assert result.returncode != 0, "Script should fail without arguments"

    def test_run_all_flag(self):
        """Verify --run-all flag runs all boundary tests."""
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT), "--run-all"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        # Verify output contains expected test names
        data = json.loads(result.stdout)
        assert "tests" in data
        test_names = [t["name"] for t in data["tests"]]
        assert "max" in test_names
        assert "min" in test_names
        assert "zero" in test_names

    def test_frame_id_max_value(self):
        """Verify --frame-id with max int64 value works."""
        INT64_MAX = 2**63 - 1
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT), "--frame-id", str(INT64_MAX)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        data = json.loads(result.stdout)
        assert "frame_check" in data
        assert data["frame_check"]["frame_id"] == INT64_MAX
        assert data["frame_check"]["in_range"] is True

    def test_frame_id_min_value(self):
        """Verify --frame-id with min int64 value works."""
        INT64_MIN = -(2**63)
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT), "--frame-id", str(INT64_MIN)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        data = json.loads(result.stdout)
        assert "frame_check" in data
        assert data["frame_check"]["frame_id"] == INT64_MIN
        assert data["frame_check"]["in_range"] is True

    def test_frame_id_zero(self):
        """Verify --frame-id with zero works."""
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT), "--frame-id", "0"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        data = json.loads(result.stdout)
        assert data["frame_check"]["frame_id"] == 0

    def test_output_file(self):
        """Verify --output flag writes to file instead of stdout."""
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            output_path = f.name

        try:
            result = subprocess.run(
                [sys.executable, str(EDGE_TEST_SCRIPT), "--run-all", "--output", output_path],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, f"Script failed: {result.stderr}"
            # stdout should be empty when using --output
            assert result.stdout == ""

            # File should contain valid JSON
            with open(output_path, "r") as f:
                data = json.load(f)
            assert "tests" in data
        finally:
            import os

            os.unlink(output_path)
