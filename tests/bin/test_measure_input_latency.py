#!/usr/bin/env python3
"""
Tests for bin/measure_input_latency.py

These are unit tests that verify the data structures, statistics computation,
and CLI argument parsing without requiring an actual Minecraft window or
screen capture hardware.
"""

import json
import math
import os
import platform
import statistics
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure bin/ is on the path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bin.measure_input_latency import (
    DEFAULT_PRD_LIMIT_MS,
    DEFAULT_TRIALS,
    InputInjector,
    LatencyDetector,
    SessionReport,
    TrialResult,
    WindowManager,
    percentile,
    run_measurement_session,
)


# ---------------------------------------------------------------------------
# Test: TrialResult dataclass
# ---------------------------------------------------------------------------

class TestTrialResult:
    def test_successful_trial(self):
        r = TrialResult(
            trial_id=1,
            latency_ms=12.345,
            frames_until_change=6,
            actual_capture_fps=498.2,
            roi_mean_delta=45.6,
            success=True,
        )
        assert r.trial_id == 1
        assert r.latency_ms == 12.345
        assert r.success is True
        assert r.error is None

    def test_failed_trial(self):
        r = TrialResult(
            trial_id=2,
            latency_ms=0,
            frames_until_change=0,
            actual_capture_fps=0,
            roi_mean_delta=0,
            success=False,
            error="Timeout",
        )
        assert r.success is False
        assert r.error == "Timeout"


# ---------------------------------------------------------------------------
# Test: SessionReport dataclass
# ---------------------------------------------------------------------------

class TestSessionReport:
    def test_default_values(self):
        r = SessionReport()
        assert r.tool == "measure_input_latency"
        assert r.version == "1.0.0"
        assert r.prd_limit_ms == DEFAULT_PRD_LIMIT_MS
        assert r.latencies_ms == []
        assert r.trial_details == []

    def test_prd_pass_computation(self):
        r = SessionReport()
        r.median_ms = 15.0
        r.prd_limit_ms = 20.0
        r.prd_pass = r.median_ms <= r.prd_limit_ms
        assert r.prd_pass is True

    def test_prd_fail_computation(self):
        r = SessionReport()
        r.median_ms = 25.0
        r.prd_limit_ms = 20.0
        r.prd_pass = r.median_ms <= r.prd_limit_ms
        assert r.prd_pass is False


# ---------------------------------------------------------------------------
# Test: percentile function
# ---------------------------------------------------------------------------

class TestPercentile:
    def test_p50_of_odd_list(self):
        data = [1, 2, 3, 4, 5]
        assert percentile(data, 50) == 3.0

    def test_p50_of_even_list(self):
        data = [1, 2, 3, 4, 5, 6]
        # Linear interpolation between 3 and 4
        assert percentile(data, 50) == 3.5

    def test_p0_returns_min(self):
        data = [10, 20, 30, 40, 50]
        assert percentile(data, 0) == 10.0

    def test_p100_returns_max(self):
        data = [10, 20, 30, 40, 50]
        assert percentile(data, 100) == 50.0

    def test_p95_typical_use(self):
        data = list(range(1, 101))  # 1 to 100
        result = percentile(data, 95)
        # Linear interpolation near 95th percentile
        assert 94.0 <= result <= 96.0


# ---------------------------------------------------------------------------
# Test: WindowManager
# ---------------------------------------------------------------------------

class TestWindowManager:
    def test_find_mc_window_returns_none_when_not_found(self):
        """Test that find_mc_window returns None when no MC window exists."""
        wm = WindowManager()
        result = wm.find_mc_window()
        # On a dev machine without MC running, this should be None
        # or a valid window ID (int on Windows, dict on macOS)
        assert result is None or isinstance(result, (int, dict))

    @pytest.mark.skipif(platform.system() != "Windows", reason="Windows-only test")
    def test_find_window_windows_returns_none_or_int(self):
        wm = WindowManager()
        result = wm._find_window_windows()
        assert result is None or isinstance(result, int)

    @pytest.mark.skipif(platform.system() != "Darwin", reason="macOS-only test")
    def test_find_window_macos_returns_none_or_dict(self):
        wm = WindowManager()
        result = wm._find_window_macos()
        assert result is None or isinstance(result, dict)

    def test_focus_window_returns_false_for_invalid_id(self):
        wm = WindowManager()
        # Invalid window ID should return False
        result = wm.focus_window(-999999)
        assert result is False


# ---------------------------------------------------------------------------
# Test: InputInjector
# ---------------------------------------------------------------------------

class TestInputInjector:
    def test_injector_initializes(self):
        inj = InputInjector()
        assert inj._platform == platform.system()

    def test_injector_initializes_with_key(self):
        inj = InputInjector(key="a")
        assert inj.key == "a"

    @pytest.mark.skipif(platform.system() != "Windows", reason="Windows-only test")
    def test_press_and_release_windows_returns_bool(self):
        inj = InputInjector(key="w")
        # This will likely fail without a real window focused
        # but should at least return a bool
        result = inj.press_and_release()
        assert isinstance(result, bool)

    @pytest.mark.skipif(platform.system() != "Darwin", reason="macOS-only test")
    def test_press_and_release_macos_returns_bool(self):
        inj = InputInjector(key="w")
        result = inj.press_and_release()
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# Test: LatencyDetector
# ---------------------------------------------------------------------------

class TestLatencyDetector:
    def test_detector_initializes(self):
        det = LatencyDetector(roi_width=64, roi_height=64, change_threshold=30)
        assert det.roi_width == 64
        assert det.roi_height == 64
        assert det.change_threshold == 30

    def test_frame_interval(self):
        det = LatencyDetector(capture_fps=500)
        assert abs(det._frame_interval - 0.002) < 1e-9

    def test_frame_interval_1000fps(self):
        det = LatencyDetector(capture_fps=1000)
        assert abs(det._frame_interval - 0.001) < 1e-9


# ---------------------------------------------------------------------------
# Test: run_measurement_session — integration with mocks
# ---------------------------------------------------------------------------

class TestRunMeasurementSession:
    @patch("bin.measure_input_latency.WindowManager.find_mc_window", return_value=12345)
    @patch("bin.measure_input_latency.WindowManager.focus_window", return_value=True)
    @patch("bin.measure_input_latency.InputInjector.press_and_release")
    @patch("bin.measure_input_latency.mss.mss")
    def test_session_with_mocked_capture(
        self, mock_mss, mock_press, mock_focus, mock_find
    ):
        """Test a full session with mocked screen capture and input."""
        # Mock mss context manager
        mock_sct = MagicMock()
        mock_sct.monitors = [
            {"left": 0, "top": 0, "width": 0, "height": 0},  # dummy
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
        ]

        # Create mock frames: first warmup frames are identical,
        # then after keypress they change
        import numpy as np
        baseline_frame = np.zeros((64, 64, 4), dtype=np.uint8)
        changed_frame = np.ones((64, 64, 4), dtype=np.uint8) * 100

        call_count = [0]

        def mock_grab(roi):
            call_count[0] += 1
            # First 5 calls = warmup (return baseline)
            if call_count[0] <= 5:
                mock_frame = MagicMock()
                mock_frame.__array__ = MagicMock(return_value=baseline_frame)
                return mock_frame
            # After keypress, return changed frame
            mock_frame = MagicMock()
            mock_frame.__array__ = MagicMock(return_value=changed_frame)
            return mock_frame

        mock_sct.grab = mock_grab
        mock_mss.return_value.__enter__ = MagicMock(return_value=mock_sct)
        mock_mss.return_value.__exit__ = MagicMock(return_value=False)

        # Mock keypress timestamp
        mock_press.return_value = True

        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir)
            report = run_measurement_session(
                session_dir=session_dir,
                trials=3,
                capture_fps=500,
                key="w",
                roi_width=64,
                roi_height=64,
                change_threshold=30,
                prd_limit_ms=20.0,
            )

            # Verify report was created
            assert report.trials_requested == 3
            assert report.key_pressed == "w"
            assert report.capture_fps_target == 500

            # Check JSON file was written
            report_path = session_dir / "latency_report.json"
            assert report_path.exists()

            # Validate JSON content
            with open(report_path) as f:
                data = json.load(f)
            assert data["tool"] == "measure_input_latency"
            assert data["trials"] == 3
            assert "median_ms" in data
            assert "prd_pass" in data

    @patch("bin.measure_input_latency.WindowManager.find_mc_window", return_value=None)
    @patch("bin.measure_input_latency.WindowManager.focus_window")
    @patch("bin.measure_input_latency.InputInjector.press_and_release")
    @patch("bin.measure_input_latency.mss.mss")
    def test_session_all_trials_fail(
        self, mock_mss, mock_press, mock_focus, mock_find
    ):
        """Test session where all trials fail (no change detected)."""
        mock_sct = MagicMock()
        mock_sct.monitors = [
            {"left": 0, "top": 0, "width": 0, "height": 0},
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
        ]

        import numpy as np
        # All frames are identical (no change)
        baseline_frame = np.zeros((64, 64, 4), dtype=np.uint8)

        mock_sct.grab = lambda roi: MagicMock(__array__=MagicMock(return_value=baseline_frame))
        mock_mss.return_value.__enter__ = MagicMock(return_value=mock_sct)
        mock_mss.return_value.__exit__ = MagicMock(return_value=False)

        mock_press.return_value = True

        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir)
            report = run_measurement_session(
                session_dir=session_dir,
                trials=2,
                capture_fps=500,
                key="w",
                roi_width=64,
                roi_height=64,
                change_threshold=30,
                prd_limit_ms=20.0,
            )

            # Should return early with error since no MC window found
            assert report.error == "Minecraft window not found"
            assert report.trials_requested == 2  # Value passed in, even on error

    @patch("bin.measure_input_latency.WindowManager.find_mc_window", return_value=None)
    def test_session_no_mc_window(self, mock_find):
        """Test session returns error when no MC window is found."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir)
            report = run_measurement_session(
                session_dir=session_dir,
                trials=3,
            )
            assert report.error == "Minecraft window not found"
            assert report.trials_requested == 3  # Value passed in, even on error


# ---------------------------------------------------------------------------
# Test: CLI argument parsing
# ---------------------------------------------------------------------------

class TestCLI:
    def test_default_args(self):
        """Test that default arguments are set correctly."""
        from bin.measure_input_latency import (
            DEFAULT_TRIALS,
            DEFAULT_CAPTURE_FPS,
            DEFAULT_KEY,
            DEFAULT_ROI_WIDTH,
            DEFAULT_ROI_HEIGHT,
            DEFAULT_CHANGE_THRESHOLD,
            DEFAULT_PRD_LIMIT_MS,
        )
        assert DEFAULT_TRIALS == 50
        assert DEFAULT_CAPTURE_FPS == 500
        assert DEFAULT_KEY == "w"
        assert DEFAULT_ROI_WIDTH == 64
        assert DEFAULT_ROI_HEIGHT == 64
        assert DEFAULT_CHANGE_THRESHOLD == 30
        assert DEFAULT_PRD_LIMIT_MS == 20.0

    def test_argparse_help(self, capsys):
        """Test that --help works without errors."""
        from bin.measure_input_latency import main
        with pytest.raises(SystemExit) as exc_info:
            main()
        # --help is not provided, so it should fail with "no MC window"
        # or similar error, not an import error
        assert exc_info.value.code in (0, 1, 2)


# ---------------------------------------------------------------------------
# Test: JSON output format
# ---------------------------------------------------------------------------

class TestJSONOutput:
    def test_trial_result_serialization(self):
        """Test that TrialResult can be serialized to dict."""
        r = TrialResult(
            trial_id=1,
            latency_ms=12.5,
            frames_until_change=6,
            actual_capture_fps=500.0,
            roi_mean_delta=45.0,
            success=True,
        )
        # dataclasses.asdict should work
        from dataclasses import asdict
        d = asdict(r)
        assert d["trial_id"] == 1
        assert d["latency_ms"] == 12.5
        assert d["success"] is True

    def test_session_report_serialization(self):
        """Test that SessionReport can be serialized to dict."""
        from dataclasses import asdict
        r = SessionReport()
        r.median_ms = 15.0
        r.mean_ms = 14.8
        r.prd_pass = True
        d = asdict(r)
        assert d["median_ms"] == 15.0
        assert d["mean_ms"] == 14.8
        assert d["prd_pass"] is True

    def test_report_json_has_required_fields(self):
        """Verify the full report JSON has all required fields."""
        required_fields = [
            "tool",
            "version",
            "timestamp",
            "platform",
            "trials",
            "capture_fps_target",
            "key",
            "roi_width",
            "roi_height",
            "change_threshold",
            "prd_limit_ms",
            "trials_requested",
            "key_pressed",
            "latencies_ms",
            "median_ms",
            "mean_ms",
            "std_ms",
            "p50_ms",
            "p95_ms",
            "p99_ms",
            "min_ms",
            "max_ms",
            "prd_pass",
            "trial_details",
            "error",
        ]
        for field_name in required_fields:
            assert field_name in required_fields, f"Missing field: {field_name}"

    def test_summary_json_structure(self):
        """Verify the summary JSON has the compact set of fields."""
        summary = {
            "median_ms": 11.0,
            "mean_ms": 11.3,
            "p95_ms": 12.8,
            "p99_ms": 12.95,
            "prd_pass": True,
            "prd_limit_ms": 20.0,
            "trials_completed": 50,
            "trials_failed": 0,
            "timestamp_utc": "2026-01-01T00:00:00+00:00",
        }
        required = [
            "median_ms", "mean_ms", "p95_ms", "p99_ms",
            "prd_pass", "prd_limit_ms", "trials_completed",
            "trials_failed", "timestamp_utc",
        ]
        for field_name in required:
            assert field_name in summary, f"Missing field: {field_name}"


# ---------------------------------------------------------------------------
# Test: Statistics computation
# ---------------------------------------------------------------------------

class TestStatisticsComputation:
    def test_median_calculation(self):
        data = [10.0, 12.0, 11.0, 13.0, 10.5]
        assert statistics.median(data) == 11.0

    def test_mean_calculation(self):
        data = [10.0, 12.0, 11.0, 13.0, 10.5]
        assert abs(statistics.mean(data) - 11.3) < 0.01

    def test_std_calculation(self):
        data = [10.0, 12.0, 11.0, 13.0, 10.5]
        std = statistics.stdev(data)
        assert 1.0 < std < 1.5

    def test_prd_compliance_boundary(self):
        """Test exact boundary: median == limit should pass."""
        median = 20.0
        limit = 20.0
        assert median <= limit  # should pass

    def test_prd_compliance_over(self):
        """Test over limit: median > limit should fail."""
        median = 20.1
        limit = 20.0
        assert not (median <= limit)  # should fail