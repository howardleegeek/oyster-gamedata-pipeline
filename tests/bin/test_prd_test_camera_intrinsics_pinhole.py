#!/usr/bin/env python3
"""
Tests for bin/prd_test_camera_intrinsics_pinhole.py

PRD p3 #2: Camera Intrinsics Pinhole Validation — validates camera projection
uses pinhole model with fov, aspect ratio populated, and no fisheye distortion.
"""

from bin.prd_test_camera_intrinsics_pinhole import (
    validate_pinhole_intrinsics,
)


class TestValidatePinholeIntrinsics:
    """Tests for validate_pinhole_intrinsics function."""

    def test_valid_pinhole_camera(self):
        """Test valid pinhole camera passes validation."""
        camera = {
            "intrinsics": {
                "fov": 90.0,
                "aspect": 1.7777777910232544,
                "projection": {
                    "model": "pinhole",
                },
            }
        }
        errors = validate_pinhole_intrinsics(camera, "test_cam")
        assert errors == [], f"Expected no errors, got: {errors}"

    def test_valid_pinhole_with_projection_fallback(self):
        """Test valid pinhole with fov/aspect in projection."""
        camera = {
            "intrinsics": {
                "projection": {
                    "model": "pinhole",
                    "fov": 75.0,
                    "aspect": 1.6,
                }
            }
        }
        errors = validate_pinhole_intrinsics(camera, "test_cam")
        assert errors == [], f"Expected no errors, got: {errors}"

    def test_invalid_projection_model(self):
        """Test fisheye projection model fails validation."""
        camera = {
            "intrinsics": {
                "fov": 90.0,
                "aspect": 1.78,
                "projection": {
                    "model": "fisheye",
                },
            }
        }
        errors = validate_pinhole_intrinsics(camera, "test_cam")
        assert any("fisheye" in e.lower() for e in errors)

    def test_missing_fov(self):
        """Test missing fov fails validation."""
        camera = {
            "intrinsics": {
                "aspect": 1.78,
                "projection": {"model": "pinhole"},
            }
        }
        errors = validate_pinhole_intrinsics(camera, "test_cam")
        assert any("fov" in e.lower() for e in errors)

    def test_invalid_fov_negative(self):
        """Test negative fov fails validation."""
        camera = {
            "intrinsics": {
                "fov": -10.0,
                "aspect": 1.78,
                "projection": {"model": "pinhole"},
            }
        }
        errors = validate_pinhole_intrinsics(camera, "test_cam")
        assert any("fov" in e.lower() for e in errors)

    def test_invalid_fov_zero(self):
        """Test zero fov fails validation."""
        camera = {
            "intrinsics": {
                "fov": 0,
                "aspect": 1.78,
                "projection": {"model": "pinhole"},
            }
        }
        errors = validate_pinhole_intrinsics(camera, "test_cam")
        assert any("fov" in e.lower() for e in errors)

    def test_invalid_fov_string(self):
        """Test string fov fails validation."""
        camera = {
            "intrinsics": {
                "fov": "90",
                "aspect": 1.78,
                "projection": {"model": "pinhole"},
            }
        }
        errors = validate_pinhole_intrinsics(camera, "test_cam")
        assert any("fov" in e.lower() for e in errors)

    def test_missing_aspect(self):
        """Test missing aspect fails validation."""
        camera = {
            "intrinsics": {
                "fov": 90.0,
                "projection": {"model": "pinhole"},
            }
        }
        errors = validate_pinhole_intrinsics(camera, "test_cam")
        assert any("aspect" in e.lower() for e in errors)

    def test_invalid_aspect_negative(self):
        """Test negative aspect fails validation."""
        camera = {
            "intrinsics": {
                "fov": 90.0,
                "aspect": -1.0,
                "projection": {"model": "pinhole"},
            }
        }
        errors = validate_pinhole_intrinsics(camera, "test_cam")
        assert any("aspect" in e.lower() for e in errors)

    def test_forbidden_fisheye_parameter_in_intrinsics(self):
        """Test fisheye parameter in intrinsics fails validation."""
        camera = {
            "intrinsics": {
                "fov": 90.0,
                "aspect": 1.78,
                "fisheye": True,
                "projection": {"model": "pinhole"},
            }
        }
        errors = validate_pinhole_intrinsics(camera, "test_cam")
        assert any("fisheye" in e.lower() for e in errors)

    def test_forbidden_fisheye_coefficients(self):
        """Test fisheye_coefficients parameter fails validation."""
        camera = {
            "intrinsics": {
                "fov": 90.0,
                "aspect": 1.78,
                "fisheye_coefficients": [0.1, 0.2, 0.3],
                "projection": {"model": "pinhole"},
            }
        }
        errors = validate_pinhole_intrinsics(camera, "test_cam")
        assert any("fisheye_coefficients" in e.lower() for e in errors)

    def test_forbidden_fisheye_in_projection(self):
        """Test fisheye parameter in projection fails validation."""
        camera = {
            "intrinsics": {
                "fov": 90.0,
                "aspect": 1.78,
                "projection": {
                    "model": "pinhole",
                    "fisheye_params": [0.1, 0.2],
                },
            }
        }
        errors = validate_pinhole_intrinsics(camera, "test_cam")
        assert any("fisheye_params" in e.lower() for e in errors)

    def test_forbidden_fisheye_distortion_model(self):
        """Test fisheye distortion model fails validation."""
        camera = {
            "intrinsics": {
                "fov": 90.0,
                "aspect": 1.78,
                "distortion": {"model": "fisheye_equidistant"},
                "projection": {"model": "pinhole"},
            }
        }
        errors = validate_pinhole_intrinsics(camera, "test_cam")
        assert any("fisheye" in e.lower() for e in errors)

    def test_camera_name_in_error_message(self):
        """Test camera name appears in error message."""
        camera = {
            "intrinsics": {
                "projection": {"model": "fisheye"},
            }
        }
        errors = validate_pinhole_intrinsics(camera, "my_camera")
        assert any("my_camera" in e for e in errors)

    def test_nested_camera_format(self):
        """Test camera with nested intrinsics format."""
        camera = {
            "name": "back_camera",
            "intrinsics": {
                "fov": 60.0,
                "aspect": 1.5,
                "projection": {"model": "pinhole"},
            }
        }
        errors = validate_pinhole_intrinsics(camera, "back_camera")
        assert errors == [], f"Expected no errors, got: {errors}"
