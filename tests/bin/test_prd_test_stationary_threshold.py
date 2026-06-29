#!/usr/bin/env python3
"""
Tests for bin/prd_test_stationary_threshold.py

PRD p6 #5: Verify stationary frames over 5s trigger clip stop.
Validates that the recording pipeline detects when consecutive frames
show no meaningful motion for >= 5 seconds and triggers clip stop.

Core functions:
- is_stationary: detects if frame_diff indicates no motion
- frames_to_seconds: converts frame count to wall-clock seconds
- detect_stationary_cutoff: finds first frame index where stationary >= threshold
"""

import pytest

from bin.prd_test_stationary_threshold import (
    DEFAULT_FPS,
    DEFAULT_THRESHOLD_SEC,
    MIN_FRAME_DIFF,
    detect_stationary_cutoff,
    frames_to_seconds,
    is_stationary,
)


class TestIsStationary:
    """Tests for is_stationary function."""

    def test_below_epsilon_is_stationary(self):
        """Test that frame_diff below epsilon returns True."""
        assert is_stationary(0.0) is True
        assert is_stationary(0.0001) is True
        assert is_stationary(MIN_FRAME_DIFF / 2) is True

    def test_at_epsilon_boundary(self):
        """Test boundary at exactly epsilon threshold."""
        # frame_diff == epsilon is NOT stationary (must be < epsilon)
        assert is_stationary(MIN_FRAME_DIFF) is False

    def test_above_epsilon_is_not_stationary(self):
        """Test that frame_diff above epsilon returns False."""
        assert is_stationary(0.01) is False
        assert is_stationary(1.0) is False
        assert is_stationary(100.0) is False

    def test_custom_epsilon(self):
        """Test is_stationary with custom epsilon value."""
        custom_epsilon = 0.5
        assert is_stationary(0.1, epsilon=custom_epsilon) is True
        assert is_stationary(0.5, epsilon=custom_epsilon) is False
        assert is_stationary(1.0, epsilon=custom_epsilon) is False


class TestFramesToSeconds:
    """Tests for frames_to_seconds function."""

    def test_basic_conversion(self):
        """Test basic frame to seconds conversion."""
        assert frames_to_seconds(30, 30) == 1.0
        assert frames_to_seconds(60, 30) == 2.0
        assert frames_to_seconds(150, 30) == 5.0

    def test_fractional_seconds(self):
        """Test conversion produces fractional seconds."""
        assert frames_to_seconds(1, 30) == pytest.approx(1 / 30, rel=1e-6)
        assert frames_to_seconds(10, 30) == pytest.approx(10 / 30, rel=1e-6)

    def test_zero_frames(self):
        """Test zero frames equals zero seconds."""
        assert frames_to_seconds(0, 30) == 0.0

    def test_invalid_fps_raises(self):
        """Test that invalid fps raises ValueError."""
        with pytest.raises(ValueError, match="fps must be positive"):
            frames_to_seconds(30, 0)
        with pytest.raises(ValueError, match="fps must be positive"):
            frames_to_seconds(30, -1)


class TestDetectStationaryCutoff:
    """Tests for detect_stationary_cutoff function."""

    def test_always_stationary_returns_frame_index(self):
        """Test that always-stationary frames trigger cutoff at threshold."""
        fps = 30
        threshold_sec = 5.0
        threshold_frames = int(threshold_sec * fps)  # 150 frames
        # 150 + 10 stationary frames
        frame_diffs = [0.0] * (threshold_frames + 10)
        result = detect_stationary_cutoff(frame_diffs, fps, threshold_sec)
        # Should fire at frame index threshold_frames - 1 (0-indexed)
        assert result == threshold_frames - 1

    def test_always_moving_returns_none(self):
        """Test that always-moving frames never trigger cutoff."""
        frame_diffs = [1.0] * 200
        result = detect_stationary_cutoff(frame_diffs, 30, 5.0)
        assert result is None

    def test_stationary_burst_below_threshold_returns_none(self):
        """Test that stationary burst below threshold does not trigger cutoff."""
        fps = 30
        threshold_sec = 5.0
        threshold_frames = int(threshold_sec * fps)  # 150 frames
        # 50 moving, 149 stationary (below 150 threshold), 50 moving
        frame_diffs = [1.0] * 50 + [0.0] * (threshold_frames - 1) + [1.0] * 50
        result = detect_stationary_cutoff(frame_diffs, fps, threshold_sec)
        assert result is None

    def test_stationary_burst_at_threshold_triggers(self):
        """Test that stationary burst at exact threshold triggers cutoff."""
        fps = 30
        threshold_sec = 5.0
        threshold_frames = int(threshold_sec * fps)  # 150 frames
        # 50 moving, 150 stationary, 50 moving
        frame_diffs = [1.0] * 50 + [0.0] * threshold_frames + [1.0] * 50
        result = detect_stationary_cutoff(frame_diffs, fps, threshold_sec)
        expected = 50 + threshold_frames - 1  # 199
        assert result == expected

    def test_split_bursts_no_cutoff(self):
        """Test that two short stationary bursts don't trigger cutoff."""
        fps = 30
        threshold_sec = 5.0
        threshold_frames = int(threshold_sec * fps)  # 150 frames
        # Two 75-frame stationary bursts separated by motion
        half = threshold_frames // 2  # 75
        frame_diffs = [0.0] * half + [1.0] * 5 + [0.0] * half
        result = detect_stationary_cutoff(frame_diffs, fps, threshold_sec)
        assert result is None

    def test_empty_frame_diffs_returns_none(self):
        """Test that empty frame_diffs returns None."""
        result = detect_stationary_cutoff([], 30, 5.0)
        assert result is None

    def test_single_stationary_frame_no_cutoff(self):
        """Test that single stationary frame doesn't trigger cutoff."""
        frame_diffs = [1.0] * 100 + [0.0] + [1.0] * 100
        result = detect_stationary_cutoff(frame_diffs, 30, 5.0)
        assert result is None

    def test_motion_resets_counter(self):
        """Test that motion resets the consecutive stationary counter."""
        fps = 30
        threshold_sec = 5.0
        threshold_frames = int(threshold_sec * fps)  # 150 frames
        # 149 stationary, 1 moving (resets), 149 stationary
        frame_diffs = [0.0] * 149 + [1.0] + [0.0] * 149
        result = detect_stationary_cutoff(frame_diffs, fps, threshold_sec)
        # Should not trigger because counter reset at frame 149
        assert result is None


class TestDefaults:
    """Tests for default constants."""

    def test_default_fps(self):
        """Test DEFAULT_FPS is 30."""
        assert DEFAULT_FPS == 30

    def test_default_threshold_sec(self):
        """Test DEFAULT_THRESHOLD_SEC is 5.0."""
        assert DEFAULT_THRESHOLD_SEC == 5.0

    def test_default_threshold_frames(self):
        """Test default threshold equals 150 frames (5s * 30fps)."""
        assert int(DEFAULT_THRESHOLD_SEC * DEFAULT_FPS) == 150

    def test_min_frame_diff(self):
        """Test MIN_FRAME_DIFF is small positive value."""
        assert MIN_FRAME_DIFF == 1e-3
        assert MIN_FRAME_DIFF > 0


class TestScriptExitCode:
    """Tests for script exit code behavior."""

    def test_main_returns_0_on_success(self, capsys):
        """Test that main returns 0 when tests pass."""
        from bin.prd_test_stationary_threshold import main

        exit_code = main(["--fps", "30", "--threshold", "5.0"])
        assert exit_code == 0

    def test_main_with_custom_fps(self, capsys):
        """Test main with custom fps parameter."""
        from bin.prd_test_stationary_threshold import main

        exit_code = main(["--fps", "60", "--threshold", "2.0"])
        assert exit_code == 0

    def test_help_flag(self, capsys):
        """Test that --help works."""
        from bin.prd_test_stationary_threshold import main

        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        # argparse exits with 0 for --help
        assert exc_info.value.code == 0
