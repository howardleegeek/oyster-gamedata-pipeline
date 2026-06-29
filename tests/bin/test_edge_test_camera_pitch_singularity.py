#!/usr/bin/env python3
"""Tests for bin/edge_test_camera_pitch_singularity.py — Edge test for camera pitch singularity.

Verifies that camera pitch angles at exactly ±90 degrees (gimbal-lock case)
are clamped correctly, not wrapped.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Path to the edge test script (in parent bin/ directory)
EDGE_TEST_SCRIPT = Path(__file__).parent.parent.parent / "bin" / "edge_test_camera_pitch_singularity.py"


class TestEdgeTestCameraPitchSingularity:
    """Test suite for edge_test_camera_pitch_singularity.py."""

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

    def test_verbose_mode(self):
        """Verify verbose mode works without errors."""
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT), "--verbose"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Verbose mode failed: {result.stderr}"


class TestClampPitch:
    """Test suite for clamp_pitch function."""

    def test_clamp_pitch_positive_boundary(self):
        """Test clamp at +90.0 degrees."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_camera_pitch_singularity import clamp_pitch

        assert clamp_pitch(90.0) == 90.0

    def test_clamp_pitch_negative_boundary(self):
        """Test clamp at -90.0 degrees."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_camera_pitch_singularity import clamp_pitch

        assert clamp_pitch(-90.0) == -90.0

    def test_clamp_pitch_beyond_positive(self):
        """Test clamping of pitch beyond +90.0."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_camera_pitch_singularity import clamp_pitch

        assert clamp_pitch(91.0) == 90.0
        assert clamp_pitch(100.0) == 90.0
        assert clamp_pitch(180.0) == 90.0

    def test_clamp_pitch_beyond_negative(self):
        """Test clamping of pitch beyond -90.0."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_camera_pitch_singularity import clamp_pitch

        assert clamp_pitch(-91.0) == -90.0
        assert clamp_pitch(-100.0) == -90.0
        assert clamp_pitch(-180.0) == -90.0

    def test_clamp_pitch_normal_range(self):
        """Test pass-through for normal pitch range."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_camera_pitch_singularity import clamp_pitch

        assert clamp_pitch(0.0) == 0.0
        assert clamp_pitch(45.0) == 45.0
        assert clamp_pitch(-45.0) == -45.0
        assert clamp_pitch(30.0) == 30.0
        assert clamp_pitch(-30.0) == -30.0


class TestIsSingularity:
    """Test suite for is_singularity function."""

    def test_is_singularity_exact_positive(self):
        """Test detection at exact +90.0 degrees."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_camera_pitch_singularity import is_singularity

        assert is_singularity(90.0) is True

    def test_is_singularity_exact_negative(self):
        """Test detection at exact -90.0 degrees."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_camera_pitch_singularity import is_singularity

        assert is_singularity(-90.0) is True

    def test_is_singularity_near_positive(self):
        """Test near +90.0 is not considered singularity (within tolerance)."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_camera_pitch_singularity import is_singularity

        # These are close but not exact - they should clamp
        assert is_singularity(89.9999999) is False
        assert is_singularity(90.0000001) is False

    def test_is_singularity_near_negative(self):
        """Test near -90.0 is not considered singularity (within tolerance)."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_camera_pitch_singularity import is_singularity

        assert is_singularity(-89.9999999) is False
        assert is_singularity(-90.0000001) is False

    def test_is_singularity_normal_range(self):
        """Test normal angles are not singularities."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_camera_pitch_singularity import is_singularity

        assert is_singularity(0.0) is False
        assert is_singularity(45.0) is False
        assert is_singularity(-45.0) is False


class TestSingularityBehavior:
    """Test suite for test_singularity_behavior function."""

    def test_singularity_behavior_exact_positive(self):
        """Test behavior at exact +90.0 singularity."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_camera_pitch_singularity import test_singularity_behavior

        success, msg = test_singularity_behavior(90.0)
        assert success is True, f"Should handle +90° singularity: {msg}"

    def test_singularity_behavior_exact_negative(self):
        """Test behavior at exact -90.0 singularity."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_camera_pitch_singularity import test_singularity_behavior

        success, msg = test_singularity_behavior(-90.0)
        assert success is True, f"Should handle -90° singularity: {msg}"

    def test_singularity_behavior_beyond_positive(self):
        """Test behavior for angles beyond +90.0."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_camera_pitch_singularity import test_singularity_behavior

        success, msg = test_singularity_behavior(91.0)
        assert success is True, f"Should clamp beyond +90°: {msg}"

    def test_singularity_behavior_beyond_negative(self):
        """Test behavior for angles beyond -90.0."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_camera_pitch_singularity import test_singularity_behavior

        success, msg = test_singularity_behavior(-91.0)
        assert success is True, f"Should clamp beyond -90°: {msg}"

    def test_singularity_behavior_normal(self):
        """Test behavior for normal angle range."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_camera_pitch_singularity import test_singularity_behavior

        for angle in [0.0, 45.0, -45.0, 30.0, -30.0]:
            success, msg = test_singularity_behavior(angle)
            assert success is True, f"Should pass through normal angle {angle}: {msg}"


class TestRunAllTests:
    """Test suite for run_all_tests function."""

    def test_run_all_tests_returns_success(self):
        """Verify run_all_tests returns success for valid input."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_camera_pitch_singularity import run_all_tests

        success, failures = run_all_tests()
        assert success is True, f"Expected all tests to pass, failures: {failures}"
        assert len(failures) == 0, f"Expected no failures, got: {failures}"
