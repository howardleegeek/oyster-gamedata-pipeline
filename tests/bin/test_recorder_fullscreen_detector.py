#!/usr/bin/env python3
"""Tests for bin/recorder_fullscreen_detector.py."""

from __future__ import annotations

import sys
from unittest.mock import patch

# Import the module under test
from bin.recorder_fullscreen_detector import (
    WINDOW_TITLE_KEYWORDS,
    DetectionResult,
    detect_exclusive_fullscreen,
)


class TestDetectionResult:
    """Tests for the DetectionResult dataclass."""

    def test_detection_result_defaults(self):
        """DetectionResult can be created with required fields only."""
        result = DetectionResult(
            is_exclusive_fullscreen=False,
            foreground_title=None,
            window_size=None,
            screen_size=None,
            platform="linux",
        )
        assert result.is_exclusive_fullscreen is False
        assert result.foreground_title is None
        assert result.window_size is None
        assert result.screen_size is None
        assert result.platform == "linux"
        assert result.note == ""

    def test_detection_result_with_note(self):
        """DetectionResult accepts optional note parameter."""
        result = DetectionResult(
            is_exclusive_fullscreen=True,
            foreground_title="Minecraft 1.21.4",
            window_size=(1920, 1080),
            screen_size=(1920, 1080),
            platform="win32",
            note="exclusive fullscreen detected",
        )
        assert result.note == "exclusive fullscreen detected"

    def test_detection_result_with_window_size_tuple(self):
        """DetectionResult stores window_size as tuple."""
        result = DetectionResult(
            is_exclusive_fullscreen=False,
            foreground_title="Not Minecraft",
            window_size=(1280, 720),
            screen_size=(1920, 1080),
            platform="win32",
        )
        assert result.window_size == (1280, 720)
        assert result.screen_size == (1920, 1080)


class TestConstants:
    """Tests for module constants."""

    def test_window_title_keywords_contains_minecraft(self):
        """WINDOW_TITLE_KEYWORDS should contain 'minecraft'."""
        assert "minecraft" in WINDOW_TITLE_KEYWORDS


class TestDetectExclusiveFullscreen:
    """Tests for detect_exclusive_fullscreen function."""

    def test_returns_detection_result_on_non_windows(self):
        """On non-Windows platform, returns DetectionResult with is_exclusive_fullscreen=False."""
        with patch.object(sys, "platform", "darwin"):
            result = detect_exclusive_fullscreen()
        assert isinstance(result, DetectionResult)
        assert result.is_exclusive_fullscreen is False
        assert result.platform == "darwin"

    def test_returns_detection_result_on_linux(self):
        """On Linux platform, returns DetectionResult with is_exclusive_fullscreen=False."""
        with patch.object(sys, "platform", "linux"):
            result = detect_exclusive_fullscreen()
        assert isinstance(result, DetectionResult)
        assert result.is_exclusive_fullscreen is False
        assert result.platform == "linux"

    def test_non_windows_has_note_about_skipping(self):
        """Non-Windows result should have a note about skipping the check."""
        with patch.object(sys, "platform", "darwin"):
            result = detect_exclusive_fullscreen()
        assert "non-Windows" in result.note or "skipped" in result.note.lower()

    @patch("bin.recorder_fullscreen_detector._windows_detect")
    def test_windows_calls_windows_detect(self, mock_windows_detect):
        """On Windows platform, _windows_detect should be called."""
        mock_windows_detect.return_value = DetectionResult(
            is_exclusive_fullscreen=False,
            foreground_title="Minecraft",
            window_size=(1280, 720),
            screen_size=(1920, 1080),
            platform="win32",
        )
        with patch.object(sys, "platform", "win32"):
            result = detect_exclusive_fullscreen()
        mock_windows_detect.assert_called_once()
        assert isinstance(result, DetectionResult)

    @patch("bin.recorder_fullscreen_detector._windows_detect")
    def test_windows_detect_exception_caught(self, mock_windows_detect):
        """If _windows_detect raises exception, still returns safe DetectionResult."""
        mock_windows_detect.side_effect = RuntimeError("test error")
        with patch.object(sys, "platform", "win32"):
            result = detect_exclusive_fullscreen()
        assert isinstance(result, DetectionResult)
        assert result.is_exclusive_fullscreen is False
        assert "error" in result.note.lower()


class TestWindowsDetect:
    """Tests for _windows_detect function behavior.

    These tests verify the logic that doesn't require actually running on Windows.
    On macOS, _windows_detect would fail at import time due to missing windll,
    so we test the detect_exclusive_fullscreen exception handling instead.
    """

    def test_windows_detect_would_be_called_on_windows(self):
        """Verify _windows_detect is defined in the module (exists)."""
        import bin.recorder_fullscreen_detector as detector

        # The function exists in the module
        assert hasattr(detector, "_windows_detect")
        assert callable(detector._windows_detect)


class TestIntegration:
    """Integration tests for the full detection flow."""

    def test_import_succeeds(self):
        """Module can be imported without errors."""
        from bin import recorder_fullscreen_detector

        assert hasattr(recorder_fullscreen_detector, "detect_exclusive_fullscreen")
        assert hasattr(recorder_fullscreen_detector, "DetectionResult")

    def test_cli_entry_point_exists(self):
        """_main function exists and is callable."""
        from bin.recorder_fullscreen_detector import _main

        assert callable(_main)

    def test_cli_entry_point_returns_int(self):
        """_main returns an integer (exit code)."""
        from bin.recorder_fullscreen_detector import _main

        with patch.object(sys, "platform", "darwin"):
            exit_code = _main([])
        assert isinstance(exit_code, int)

    def test_cli_returns_1_when_fullscreen(self):
        """_main returns 1 when fullscreen is detected."""
        from bin.recorder_fullscreen_detector import _main

        with patch("bin.recorder_fullscreen_detector._windows_detect") as mock:
            mock.return_value = DetectionResult(
                is_exclusive_fullscreen=True,
                foreground_title="Minecraft 1.21.4",
                window_size=(1920, 1080),
                screen_size=(1920, 1080),
                platform="win32",
            )
            with patch.object(sys, "platform", "win32"):
                exit_code = _main([])
        assert exit_code == 1

    def test_cli_returns_0_when_not_fullscreen(self):
        """_main returns 0 when fullscreen is NOT detected."""
        from bin.recorder_fullscreen_detector import _main

        with patch.object(sys, "platform", "darwin"):
            exit_code = _main([])
        assert exit_code == 0
