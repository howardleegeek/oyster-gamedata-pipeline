#!/usr/bin/env python3
"""Tests for bin/edge_test_missing_optional_fields.py — Boundary test for missing optional fields.

Verifies that a Vector4 adapter handles missing optional quaternion fields gracefully
by providing sensible default values instead of raising exceptions.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Path to the edge test script (in parent bin/ directory)
EDGE_TEST_SCRIPT = Path(__file__).parent.parent.parent / "bin" / "edge_test_missing_optional_fields.py"


class TestEdgeTestMissingOptionalFields:
    """Test suite for edge_test_missing_optional_fields.py."""

    def test_script_exists(self):
        """Verify the edge test script exists and is executable."""
        assert EDGE_TEST_SCRIPT.exists(), f"Script not found: {EDGE_TEST_SCRIPT}"

    def test_runs_successfully(self):
        """Verify the edge test runs and exits with success (0)."""
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        assert "passed" in result.stdout, "Expected pass summary in output"

    def test_verbose_mode(self):
        """Verify verbose mode works without errors."""
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT), "--verbose"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Verbose mode failed: {result.stderr}"


class TestVector4Adapter:
    """Test suite for Vector4Adapter class."""

    def test_complete_data(self):
        """Test with complete data - no defaults needed."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_missing_optional_fields import Vector4Adapter

        data = {
            "x": 1.0,
            "y": 2.0,
            "z": 3.0,
            "w": 0.5,
            "quat": {"x": 1.0, "y": 2.0, "z": 3.0, "w": 0.5},
        }
        result = Vector4Adapter.adapt(data)
        assert result["w"] == 0.5
        assert result["quat"]["w"] == 0.5

    def test_missing_w(self):
        """Test default w is filled in when missing."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_missing_optional_fields import Vector4Adapter

        data = {"x": 1.0, "y": 2.0, "z": 3.0}
        result = Vector4Adapter.adapt(data)
        assert "w" in result
        assert result["w"] == 1.0  # default

    def test_missing_quat(self):
        """Test default quat dict is filled when missing."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_missing_optional_fields import Vector4Adapter

        data = {"x": 1.0, "y": 2.0, "z": 3.0, "w": 0.5}
        result = Vector4Adapter.adapt(data)
        assert "quat" in result
        assert isinstance(result["quat"], dict)
        assert result["quat"]["w"] == 1.0  # identity quaternion

    def test_quat_none(self):
        """Test quat=None is replaced with default quat."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_missing_optional_fields import Vector4Adapter

        data = {"x": 1.0, "y": 2.0, "z": 3.0, "w": 0.5, "quat": None}
        result = Vector4Adapter.adapt(data)
        assert result["quat"] is not None
        assert isinstance(result["quat"], dict)
        assert result["quat"]["w"] == 1.0

    def test_quat_partial(self):
        """Test partial quat dict is filled with defaults."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_missing_optional_fields import Vector4Adapter

        data = {"x": 1.0, "y": 2.0, "z": 3.0, "w": 0.5, "quat": {"x": 1.0}}
        result = Vector4Adapter.adapt(data)
        assert result["quat"]["y"] == 0.0  # default
        assert result["quat"]["z"] == 0.0  # default
        assert result["quat"]["w"] == 1.0  # default

    def test_empty_dict(self):
        """Test empty input dict gets all defaults."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_missing_optional_fields import Vector4Adapter

        data = {}
        result = Vector4Adapter.adapt(data)
        assert result["w"] == 1.0
        assert "quat" in result
        assert result["quat"]["x"] == 0.0
        assert result["quat"]["y"] == 0.0
        assert result["quat"]["z"] == 0.0
        assert result["quat"]["w"] == 1.0
