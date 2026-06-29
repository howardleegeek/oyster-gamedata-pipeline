#!/usr/bin/env python3
"""
Tests for bin/prd_test_30min_scene_cap.py

PRD p7 #3: Validate max 30 minutes per scene — clock cap enforced.
"""


from bin.prd_test_30min_scene_cap import (
    calculate_elapsed_minutes,
    check_scene_duration,
    create_scene_result,
)


class TestCalculateElapsedMinutes:
    """Tests for calculate_elapsed_minutes function."""

    def test_exact_30_minutes(self):
        """Test scene at exactly 30 minutes."""
        start = "2026-01-01T00:00:00"
        end = "2026-01-01T00:30:00"
        result = calculate_elapsed_minutes(start, end)
        assert result == 30.0

    def test_under_30_minutes(self):
        """Test scene under 30 minutes."""
        start = "2026-01-01T00:00:00"
        end = "2026-01-01T00:15:00"
        result = calculate_elapsed_minutes(start, end)
        assert result == 15.0

    def test_over_30_minutes(self):
        """Test scene over 30 minutes."""
        start = "2026-01-01T00:00:00"
        end = "2026-01-01T00:45:00"
        result = calculate_elapsed_minutes(start, end)
        assert result == 45.0

    def test_fractional_minutes(self):
        """Test scene with fractional minutes."""
        start = "2026-01-01T00:00:00"
        end = "2026-01-01T00:00:30"
        result = calculate_elapsed_minutes(start, end)
        assert result == 0.5


class TestCheckSceneDuration:
    """Tests for check_scene_duration function."""

    def test_exact_threshold(self):
        """Test scene at exactly 30 minutes threshold - matches main() behavior."""
        result = check_scene_duration(30.0, threshold_minutes=30.0)
        assert result["exceeded"] is False
        # At exactly 30 minutes: 30 > 24 is True, so warning is True
        # This matches the main() function behavior: warning = elapsed > threshold * 0.8
        assert result["warning"] is True
        assert result["status"] == "WARNING"

    def test_below_threshold(self):
        """Test scene below threshold."""
        result = check_scene_duration(20.0, threshold_minutes=30.0)
        assert result["exceeded"] is False
        assert result["warning"] is False
        assert result["status"] == "OK"
        assert result["remaining_minutes"] == 10.0

    def test_above_threshold(self):
        """Test scene above threshold."""
        result = check_scene_duration(45.0, threshold_minutes=30.0)
        assert result["exceeded"] is True
        assert result["warning"] is False
        assert result["status"] == "EXCEEDED"
        assert result["over_by_minutes"] == 15.0

    def test_warning_zone(self):
        """Test scene in warning zone (80% of threshold)."""
        result = check_scene_duration(25.0, threshold_minutes=30.0)
        assert result["exceeded"] is False
        assert result["warning"] is True
        assert result["status"] == "WARNING"
        assert result["remaining_minutes"] == 5.0

    def test_just_below_warning(self):
        """Test scene just below warning zone."""
        result = check_scene_duration(23.99, threshold_minutes=30.0)
        assert result["exceeded"] is False
        assert result["warning"] is False
        assert result["status"] == "OK"

    def test_just_above_warning(self):
        """Test scene just above warning zone."""
        result = check_scene_duration(24.01, threshold_minutes=30.0)
        assert result["exceeded"] is False
        assert result["warning"] is True
        assert result["status"] == "WARNING"

    def test_custom_threshold(self):
        """Test with custom threshold."""
        result = check_scene_duration(10.0, threshold_minutes=15.0)
        assert result["exceeded"] is False
        assert result["remaining_minutes"] == 5.0


class TestCreateSceneResult:
    """Tests for create_scene_result function."""

    def test_ok_status(self):
        """Test result with OK status."""
        result = create_scene_result(
            scene_id="test_scene",
            duration_minutes=20.0,
            threshold_minutes=30.0,
        )
        assert result["scene_id"] == "test_scene"
        assert result["duration_minutes"] == 20.0
        assert result["threshold_minutes"] == 30.0
        assert result["exceeded"] is False
        assert result["status"] == "OK"

    def test_exceeded_status(self):
        """Test result with EXCEEDED status."""
        result = create_scene_result(
            scene_id="long_scene",
            duration_minutes=45.0,
            threshold_minutes=30.0,
        )
        assert result["scene_id"] == "long_scene"
        assert result["exceeded"] is True
        assert result["over_by_minutes"] == 15.0
        assert result["status"] == "EXCEEDED"

    def test_warning_status(self):
        """Test result with WARNING status."""
        result = create_scene_result(
            scene_id="approaching_limit",
            duration_minutes=25.0,
            threshold_minutes=30.0,
        )
        assert result["warning"] is True
        assert result["status"] == "WARNING"
        assert result["remaining_minutes"] == 5.0
