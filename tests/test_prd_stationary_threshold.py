#!/usr/bin/env python3
"""Tests for bin/prd_test_stationary_threshold.py"""

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "bin" / "prd_test_stationary_threshold.py"


def _load_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("prd_test_stationary_threshold", str(SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["prd_test_stationary_threshold"] = mod
    spec.loader.exec_module(mod)
    return mod


mod = _load_module()
is_stationary = mod.is_stationary
frames_to_seconds = mod.frames_to_seconds
detect_stationary_cutoff = mod.detect_stationary_cutoff
DEFAULT_FPS = mod.DEFAULT_FPS
DEFAULT_THRESHOLD_SEC = mod.DEFAULT_THRESHOLD_SEC
MIN_FRAME_DIFF = mod.MIN_FRAME_DIFF


# ---------------------------------------------------------------------------
# Unit tests for is_stationary
# ---------------------------------------------------------------------------


class TestIsStationary:
    """Tests for is_stationary function."""

    def test_zero_diff_is_stationary(self):
        """Zero frame difference is stationary."""
        assert is_stationary(0.0) is True

    def test_very_small_diff_is_stationary(self):
        """Very small frame difference is stationary."""
        assert is_stationary(1e-6) is True
        assert is_stationary(1e-4) is True

    def test_at_epsilon_boundary(self):
        """Frame difference at epsilon boundary is stationary."""
        assert is_stationary(MIN_FRAME_DIFF * 0.99) is True

    def test_above_epsilon_is_not_stationary(self):
        """Frame difference above epsilon is not stationary."""
        assert is_stationary(MIN_FRAME_DIFF * 1.01) is False
        assert is_stationary(0.01) is False
        assert is_stationary(1.0) is False

    def test_custom_epsilon(self):
        """Custom epsilon can be specified."""
        assert is_stationary(0.5, epsilon=1.0) is True
        assert is_stationary(1.5, epsilon=1.0) is False


# ---------------------------------------------------------------------------
# Unit tests for frames_to_seconds
# ---------------------------------------------------------------------------


class TestFramesToSeconds:
    """Tests for frames_to_seconds function."""

    def test_30fps_one_second(self):
        """30 frames at 30fps = 1 second."""
        assert frames_to_seconds(30, fps=30) == 1.0

    def test_30fps_half_second(self):
        """15 frames at 30fps = 0.5 seconds."""
        assert frames_to_seconds(15, fps=30) == 0.5

    def test_60fps_one_second(self):
        """60 frames at 60fps = 1 second."""
        assert frames_to_seconds(60, fps=60) == 1.0

    def test_zero_frames(self):
        """Zero frames = 0 seconds."""
        assert frames_to_seconds(0, fps=30) == 0.0

    def test_invalid_fps_raises(self):
        """Invalid fps raises ValueError."""
        with pytest.raises(ValueError, match="fps must be positive"):
            frames_to_seconds(30, fps=0)
        with pytest.raises(ValueError, match="fps must be positive"):
            frames_to_seconds(30, fps=-1)


# ---------------------------------------------------------------------------
# Unit tests for detect_stationary_cutoff
# ---------------------------------------------------------------------------


class TestDetectStationaryCutoff:
    """Tests for detect_stationary_cutoff function."""

    def test_no_stationary_frames(self):
        """No stationary frames returns None."""
        diffs = [0.1] * 100  # All moving
        result = detect_stationary_cutoff(diffs, fps=30, threshold_sec=5.0)
        assert result is None

    def test_stationary_from_start(self):
        """Stationary from start triggers cutoff at threshold_frames - 1."""
        fps = 30
        threshold_sec = 5.0
        threshold_frames = int(threshold_sec * fps)  # 150 frames
        diffs = [0.0] * 200  # All stationary
        result = detect_stationary_cutoff(diffs, fps=fps, threshold_sec=threshold_sec)
        # Cutoff fires when consecutive_stationary >= threshold_frames
        # That happens at index 149 (frame 150)
        assert result == threshold_frames - 1

    def test_stationary_after_motion(self):
        """Stationary period after motion triggers cutoff."""
        fps = 30
        threshold_sec = 1.0  # 30 frames
        # 50 moving frames, then 50 stationary frames
        diffs = [0.1] * 50 + [0.0] * 50
        result = detect_stationary_cutoff(diffs, fps=fps, threshold_sec=threshold_sec)
        # Cutoff at frame 50 + 29 = 79 (30th stationary frame)
        assert result == 50 + 29

    def test_short_stationary_period_no_cutoff(self):
        """Short stationary period below threshold does not trigger cutoff."""
        fps = 30
        threshold_sec = 5.0  # 150 frames required
        # 100 moving, 100 stationary (below 150), 100 moving
        diffs = [0.1] * 100 + [0.0] * 100 + [0.1] * 100
        result = detect_stationary_cutoff(diffs, fps=fps, threshold_sec=threshold_sec)
        assert result is None

    def test_multiple_stationary_periods(self):
        """Multiple stationary periods - first one exceeding threshold triggers."""
        fps = 30
        threshold_sec = 1.0  # 30 frames
        # 50 moving, 20 stationary (short), 50 moving, 50 stationary (long)
        diffs = [0.1] * 50 + [0.0] * 20 + [0.1] * 50 + [0.0] * 50
        result = detect_stationary_cutoff(diffs, fps=fps, threshold_sec=threshold_sec)
        # First long stationary period starts at frame 120, cutoff at frame 149
        assert result == 120 + 29

    def test_exactly_at_threshold(self):
        """Exactly threshold frames stationary triggers cutoff."""
        fps = 30
        threshold_sec = 1.0  # 30 frames
        diffs = [0.1] * 50 + [0.0] * 30  # Exactly 30 stationary frames
        result = detect_stationary_cutoff(diffs, fps=fps, threshold_sec=threshold_sec)
        assert result == 50 + 29  # Frame index 79

    def test_custom_fps_and_threshold(self):
        """Custom fps and threshold work correctly."""
        fps = 60
        threshold_sec = 2.0  # 120 frames at 60fps
        diffs = [0.0] * 200
        result = detect_stationary_cutoff(diffs, fps=fps, threshold_sec=threshold_sec)
        assert result == 119  # Frame 120 at index 119


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


class TestCLI:
    """Tests for CLI interface."""

    def test_cli_help(self):
        """CLI --help returns 0."""
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "fps" in result.stdout.lower() or "threshold" in result.stdout.lower()

    def test_cli_default_runs_tests(self):
        """CLI with no args runs built-in tests and passes."""
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "PASS" in result.stdout or "pass" in result.stdout.lower()

    def test_cli_custom_fps_and_threshold(self):
        """CLI accepts custom fps and threshold."""
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--fps", "60", "--threshold", "2.0"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "fps=60" in result.stdout