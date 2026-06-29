#!/usr/bin/env python3
"""
Tests for bin/prd_test_metric_units_meters.py

PRD p3 #5: Validate camera_position in meters is bounded by world_cube_radius.
"""

import pytest

from bin.prd_test_metric_units_meters import (
    check_bounds,
    parse_camera_data,
)


class TestParseCameraData:
    """Tests for parse_camera_data function."""

    def test_valid_data(self):
        """Test parsing valid camera data returns floats."""
        data = {"camera_position": [1.0, 2.0, 3.0], "world_cube_radius": 10.0}
        position, radius = parse_camera_data(data)
        assert position == [1.0, 2.0, 3.0]
        assert radius == 10.0
        assert all(isinstance(v, float) for v in position)
        assert isinstance(radius, float)

    def test_valid_data_at_origin(self):
        """Test parsing position at origin with large radius."""
        data = {"camera_position": [0.0, 0.0, 0.0], "world_cube_radius": 100.0}
        position, radius = parse_camera_data(data)
        assert position == [0.0, 0.0, 0.0]
        assert radius == 100.0

    def test_valid_data_with_negative_coords(self):
        """Test parsing position with negative coordinates (allowed)."""
        data = {"camera_position": [-5.0, -10.0, -15.0], "world_cube_radius": 50.0}
        position, radius = parse_camera_data(data)
        assert position == [-5.0, -10.0, -15.0]
        assert radius == 50.0

    def test_valid_data_int_position(self):
        """Test parsing integer coordinates are converted to floats."""
        data = {"camera_position": [1, 2, 3], "world_cube_radius": 10}
        position, radius = parse_camera_data(data)
        assert position == [1.0, 2.0, 3.0]
        assert radius == 10.0

    def test_missing_camera_position(self):
        """Test missing camera_position field raises ValueError."""
        data = {"world_cube_radius": 10.0}
        with pytest.raises(ValueError, match="camera_position"):
            parse_camera_data(data)

    def test_missing_world_cube_radius(self):
        """Test missing world_cube_radius field raises ValueError."""
        data = {"camera_position": [0.0, 0.0, 0.0]}
        with pytest.raises(ValueError, match="world_cube_radius"):
            parse_camera_data(data)

    def test_empty_dict(self):
        """Test empty dict raises ValueError for missing fields."""
        with pytest.raises(ValueError):
            parse_camera_data({})

    def test_position_not_list(self):
        """Test camera_position as non-list raises ValueError."""
        data = {"camera_position": (0.0, 0.0, 0.0), "world_cube_radius": 10.0}
        with pytest.raises(ValueError, match="list of 3 numbers"):
            parse_camera_data(data)

    def test_position_too_short(self):
        """Test camera_position with fewer than 3 elements raises ValueError."""
        data = {"camera_position": [0.0, 0.0], "world_cube_radius": 10.0}
        with pytest.raises(ValueError, match="list of 3 numbers"):
            parse_camera_data(data)

    def test_position_too_long(self):
        """Test camera_position with more than 3 elements raises ValueError."""
        data = {"camera_position": [0.0, 0.0, 0.0, 0.0], "world_cube_radius": 10.0}
        with pytest.raises(ValueError, match="list of 3 numbers"):
            parse_camera_data(data)

    def test_position_with_non_numeric(self):
        """Test camera_position with non-numeric element raises ValueError."""
        data = {"camera_position": [0.0, "bad", 0.0], "world_cube_radius": 10.0}
        with pytest.raises(ValueError, match="must be a number"):
            parse_camera_data(data)

    def test_position_with_none(self):
        """Test camera_position with None element raises ValueError."""
        data = {"camera_position": [0.0, None, 0.0], "world_cube_radius": 10.0}
        with pytest.raises(ValueError, match="must be a number"):
            parse_camera_data(data)

    def test_radius_zero(self):
        """Test world_cube_radius of 0 raises ValueError (must be positive)."""
        data = {"camera_position": [0.0, 0.0, 0.0], "world_cube_radius": 0}
        with pytest.raises(ValueError, match="positive number"):
            parse_camera_data(data)

    def test_radius_negative(self):
        """Test negative world_cube_radius raises ValueError."""
        data = {"camera_position": [0.0, 0.0, 0.0], "world_cube_radius": -5.0}
        with pytest.raises(ValueError, match="positive number"):
            parse_camera_data(data)

    def test_radius_non_numeric(self):
        """Test non-numeric world_cube_radius raises ValueError."""
        data = {"camera_position": [0.0, 0.0, 0.0], "world_cube_radius": "big"}
        with pytest.raises(ValueError, match="positive number"):
            parse_camera_data(data)

    def test_position_with_bool_is_accepted(self):
        """Test bool elements are accepted (bool is a subclass of int in Python)."""
        # Booleans are technically ints in Python, so this is consistent with
        # the existing isinstance check. Documenting the current behavior.
        data = {"camera_position": [True, False, True], "world_cube_radius": 10.0}
        position, radius = parse_camera_data(data)
        assert position == [1.0, 0.0, 1.0]


class TestCheckBounds:
    """Tests for check_bounds function."""

    def test_within_bounds_at_origin(self):
        """Test position at origin is within bounds for any positive radius."""
        result = check_bounds([0.0, 0.0, 0.0], 10.0)
        assert result["within_bounds"] is True
        assert result["status"] == "VALID"
        assert result["distance_from_origin"] == 0.0
        assert result["camera_position_meters"] == [0.0, 0.0, 0.0]
        assert result["world_cube_radius_meters"] == 10.0

    def test_within_bounds_inside(self):
        """Test position well inside the world cube is within bounds."""
        result = check_bounds([3.0, 4.0, 0.0], 10.0)
        assert result["within_bounds"] is True
        assert result["status"] == "VALID"
        # 3-4-5 triangle: distance = 5.0
        assert result["distance_from_origin"] == 5.0

    def test_within_bounds_at_surface(self):
        """Test position exactly on the radius is within bounds (<=)."""
        # distance = sqrt(3) * 2 ≈ 3.4641
        result = check_bounds([2.0, 2.0, 2.0], 3.464101616)
        assert result["within_bounds"] is True
        assert result["status"] == "VALID"

    def test_out_of_bounds(self):
        """Test position beyond radius is out of bounds."""
        # distance = sqrt(9 + 16 + 25) = 7.071
        result = check_bounds([3.0, 4.0, 5.0], 5.0)
        assert result["within_bounds"] is False
        assert result["status"] == "OUT_OF_BOUNDS"
        assert result["distance_from_origin"] == pytest.approx(7.071068, abs=1e-5)

    def test_distance_rounded(self):
        """Test distance_from_origin is rounded to 6 decimal places."""
        # distance = sqrt(2) ≈ 1.4142135...
        result = check_bounds([1.0, 1.0, 0.0], 10.0)
        # sqrt(2) rounded to 6 places = 1.414214
        assert result["distance_from_origin"] == 1.414214

    def test_negative_position_within_bounds(self):
        """Test negative coordinate position within radius is valid."""
        result = check_bounds([-3.0, -4.0, 0.0], 10.0)
        assert result["within_bounds"] is True
        assert result["status"] == "VALID"
        assert result["distance_from_origin"] == 5.0

    def test_far_out_of_bounds(self):
        """Test position far beyond radius is out of bounds."""
        result = check_bounds([100.0, 100.0, 100.0], 10.0)
        assert result["within_bounds"] is False
        assert result["status"] == "OUT_OF_BOUNDS"
        # distance = sqrt(30000) ≈ 173.205
        assert result["distance_from_origin"] == pytest.approx(173.205081, abs=1e-5)

    def test_result_keys_complete(self):
        """Test that result dict contains all expected keys."""
        result = check_bounds([1.0, 1.0, 1.0], 10.0)
        expected_keys = {
            "camera_position_meters",
            "world_cube_radius_meters",
            "distance_from_origin",
            "within_bounds",
            "status",
        }
        assert set(result.keys()) == expected_keys

    def test_status_valid_vs_out_of_bounds(self):
        """Test status field correctly reflects within_bounds."""
        valid_result = check_bounds([1.0, 1.0, 1.0], 10.0)
        invalid_result = check_bounds([100.0, 100.0, 100.0], 10.0)
        assert valid_result["status"] == "VALID"
        assert invalid_result["status"] == "OUT_OF_BOUNDS"

    def test_zero_radius_with_nonzero_position(self):
        """Test check_bounds with zero radius and nonzero position is out of bounds."""
        # parse_camera_data rejects zero radius, but check_bounds itself can be
        # called directly with zero. The within_bounds check uses <= 0.
        result = check_bounds([1.0, 0.0, 0.0], 0.0)
        assert result["within_bounds"] is False
        assert result["status"] == "OUT_OF_BOUNDS"
