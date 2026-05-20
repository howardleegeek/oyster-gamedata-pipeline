#!/usr/bin/env python3
"""Tests for bin/prd_test_metric_units_meters.py"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "bin" / "prd_test_metric_units_meters.py"


def _load_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("prd_test_metric_units_meters", str(SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["prd_test_metric_units_meters"] = mod
    spec.loader.exec_module(mod)
    return mod


mod = _load_module()
parse_camera_data = mod.parse_camera_data
check_bounds = mod.check_bounds
DEFAULT_WORLD_CUBE_RADIUS = mod.DEFAULT_WORLD_CUBE_RADIUS


# ---------------------------------------------------------------------------
# Unit tests for parse_camera_data
# ---------------------------------------------------------------------------


class TestParseCameraData:
    """Tests for parse_camera_data function."""

    def test_single_record_format(self):
        """Single record with camera_position and world_cube_radius."""
        data = {"camera_position": [1.0, 2.0, 3.0], "world_cube_radius": 5.0}
        pos, radius = parse_camera_data(data)
        assert pos == [1.0, 2.0, 3.0]
        assert radius == 5.0

    def test_single_record_default_radius(self):
        """Single record without world_cube_radius uses default."""
        data = {"camera_position": [1.0, 2.0, 3.0]}
        pos, radius = parse_camera_data(data)
        assert pos == [1.0, 2.0, 3.0]
        assert radius == DEFAULT_WORLD_CUBE_RADIUS

    def test_list_format_uses_first_record(self):
        """List format (action_camera.json) uses first record's camera_position."""
        data = [
            {"camera_position": [1.0, 2.0, 3.0], "frame": 0},
            {"camera_position": [4.0, 5.0, 6.0], "frame": 1},
        ]
        pos, radius = parse_camera_data(data)
        assert pos == [1.0, 2.0, 3.0]
        assert radius == DEFAULT_WORLD_CUBE_RADIUS

    def test_list_format_empty_raises(self):
        """Empty list raises ValueError."""
        with pytest.raises(ValueError, match="Empty list"):
            parse_camera_data([])

    def test_missing_camera_position_raises(self):
        """Missing camera_position raises ValueError."""
        with pytest.raises(ValueError, match="Missing required field"):
            parse_camera_data({"world_cube_radius": 5.0})

    def test_invalid_position_not_list_raises(self):
        """camera_position not a list raises ValueError."""
        with pytest.raises(ValueError, match="must be a list"):
            parse_camera_data({"camera_position": "not a list"})

    def test_invalid_position_wrong_length_raises(self):
        """camera_position with wrong length raises ValueError."""
        with pytest.raises(ValueError, match="must be a list of 3"):
            parse_camera_data({"camera_position": [1.0, 2.0]})

    def test_invalid_position_non_numeric_raises(self):
        """camera_position with non-numeric values raises ValueError."""
        with pytest.raises(ValueError, match="must be a number"):
            parse_camera_data({"camera_position": [1.0, "two", 3.0]})

    def test_invalid_radius_raises(self):
        """Invalid world_cube_radius raises ValueError."""
        with pytest.raises(ValueError, match="must be a positive number"):
            parse_camera_data({"camera_position": [1.0, 2.0, 3.0], "world_cube_radius": -5.0})

    def test_zero_radius_raises(self):
        """Zero world_cube_radius raises ValueError."""
        with pytest.raises(ValueError, match="must be a positive number"):
            parse_camera_data({"camera_position": [1.0, 2.0, 3.0], "world_cube_radius": 0.0})


# ---------------------------------------------------------------------------
# Unit tests for check_bounds
# ---------------------------------------------------------------------------


class TestCheckBounds:
    """Tests for check_bounds function."""

    def test_within_bounds(self):
        """Position within cube bounds returns VALID."""
        result = check_bounds([1.0, 2.0, 3.0], 10.0)
        assert result["within_bounds"] is True
        assert result["status"] == "VALID"
        assert result["world_cube_radius_meters"] == 10.0

    def test_exactly_at_boundary(self):
        """Position exactly at boundary is within bounds."""
        # Distance = sqrt(5^2 + 0 + 0) = 5.0
        result = check_bounds([5.0, 0.0, 0.0], 5.0)
        assert result["within_bounds"] is True
        assert result["status"] == "VALID"
        assert result["distance_from_origin"] == 5.0

    def test_out_of_bounds(self):
        """Position outside cube bounds returns OUT_OF_BOUNDS."""
        result = check_bounds([10.0, 10.0, 10.0], 5.0)
        assert result["within_bounds"] is False
        assert result["status"] == "OUT_OF_BOUNDS"

    def test_at_origin(self):
        """Position at origin is within bounds."""
        result = check_bounds([0.0, 0.0, 0.0], 1.0)
        assert result["within_bounds"] is True
        assert result["distance_from_origin"] == 0.0

    def test_distance_calculation(self):
        """Distance from origin is calculated correctly."""
        # sqrt(3^2 + 4^2) = 5.0
        result = check_bounds([3.0, 4.0, 0.0], 10.0)
        assert result["distance_from_origin"] == 5.0


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


class TestCLI:
    """Tests for CLI interface."""

    def test_cli_valid_input(self, tmp_path):
        """CLI with valid input returns 0."""
        input_file = tmp_path / "input.json"
        input_file.write_text(json.dumps({"camera_position": [1.0, 2.0, 3.0], "world_cube_radius": 10.0}))
        
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--input", str(input_file)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_cli_out_of_bounds_strict(self, tmp_path):
        """CLI with out-of-bounds position in strict mode returns 1."""
        input_file = tmp_path / "input.json"
        input_file.write_text(json.dumps({"camera_position": [100.0, 100.0, 100.0], "world_cube_radius": 5.0}))
        
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--input", str(input_file), "--strict"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1

    def test_cli_stdin_input(self):
        """CLI can read from stdin."""
        input_data = json.dumps({"camera_position": [1.0, 2.0, 3.0]})
        
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            input=input_data,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_cli_verbose_output(self, tmp_path):
        """CLI with --verbose outputs detailed info."""
        input_file = tmp_path / "input.json"
        input_file.write_text(json.dumps({"camera_position": [1.0, 2.0, 3.0], "world_cube_radius": 10.0}))
        
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--input", str(input_file), "--verbose"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "distance" in result.stdout.lower() or "valid" in result.stdout.lower()

    def test_cli_list_format(self, tmp_path):
        """CLI accepts action_camera.json list format."""
        input_file = tmp_path / "action_camera.json"
        input_file.write_text(json.dumps([
            {"camera_position": [1.0, 2.0, 3.0], "frame": 0},
            {"camera_position": [4.0, 5.0, 6.0], "frame": 1},
        ]))
        
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--input", str(input_file)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0