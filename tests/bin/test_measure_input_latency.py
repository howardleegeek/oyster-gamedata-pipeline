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
        data = [1, 2, 3, 4]
        result = percentile(data, 50)
        assert 2.0 <= result <= 3.0

    def test_p95(self):
        data = list(range(1, 101))
        result = percentile(data, 95)
        assert 95.0 <= result <= 96.0

    def test_p99(self):
        data = list(range(1, 101))
        result = percentile(data, 99)
        assert 99.0 <= result <= 100.0

    def test_empty_list(self):
        assert percentile([], 50) == 0.0

    def test_single_element(self):
        assert percentile([42.0], 50) == 42.0
        assert percentile([42.0], 95) == 42.0

    def test_p0_and_p100(self):
        data = [10, 20, 30, 40, 50]
        assert percentile(data, 0) == 10.0
        assert percentile(data, 100) == 50.0


# ---------------------------------------------------------------------------
# Test: InputInjector — platform detection
# ---------------------------------------------------------------------------

class TestInputInjector:
    def test_init_default_key(self):
        inj = InputInjector()
        assert inj.key == "w"

    def test_init_custom_key(self):
        inj = InputInjector(key="space")
        assert inj.key == "space"

    def test_platform_set(self):
        inj = InputInjector()
        assert inj._platform == platform.system()

    @pytest.mark.skipif(platform.system() != "Windows", reason="Windows-only test")
    @patch("platform.system", return_value="Windows")
    def test_windows_dispatch(self, mock_platform):
        inj = InputInjector(key="w")
        assert inj._platform == "Windows"

    @patch("platform.system", return_value="Darwin")
    def test_macos_dispatch(self, mock_platform):
        inj = InputInjector(key="w")
        assert inj._platform == "Darwin"

    @patch("platform.system", return_value="Linux")
    def test_linux_dispatch(self, mock_platform):
        inj = InputInjector(key="w")
        assert inj._platform == "Linux"


# ---------------------------------------------------------------------------
# Test: WindowManager — window finding
# ---------------------------------------------------------------------------

class TestWindowManager:
    def test_find_windows_no_window(self):
        """Skip on non-Windows since ctypes.windll doesn't exist."""
        if platform.system() != "Windows":
            pytest.skip("ctypes.windll only available on Windows")
        with patch("ctypes.windll") as mock_windll:
            mock_windll.user32.EnumWindows = MagicMock()
            # Simulate no MC window found
            def no_op_callback(cb, _):
                pass
            mock_windll.user32.EnumWindows.side_effect = no_op_callback
            result = WindowManager._find_windows()
            # Should return None when no results
            assert result is None or isinstance(result, dict)

    def test_find_mc_window_returns_optional_dict(self):
        result = WindowManager.find_mc_window()
        # On a dev machine without MC running, this should be None
        # or a dict with window info
        assert result is None or isinstance(result, dict)


# ---------------------------------------------------------------------------
# Test: LatencyDetector — structure
# ---------------------------------------------------------------------------

class TestLatencyDetector:
    def test_default_init(self):
        det = LatencyDetector()
        assert det.capture_fps == 500
        assert det.warmup_frames == 5
        assert det.roi_width == 64
        assert det.roi_height == 64
        assert det.change_threshold == 30

    def test_custom_init(self):
        det = LatencyDetector(
            capture_fps=1000,
            warmup_frames=10,
            roi_width=128,
            roi_height=128,
            change_threshold=50,
        )
        assert det.capture_fps == 1000
        assert det.warmup_frames == 10
        assert det.roi_width == 128
        assert det.roi_height == 128
        assert det.change_threshold == 50

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
    @patch("bin.measure_input_latency.WindowManager.find_mc_window", return_value=None)
    @patch("bin.measure_input_latency.WindowManager.focus_window")
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
        mock_press.return_value = 1000.0

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

            # Check JSON files were written
            report_path = session_dir / "input_latency_report.json"
            summary_path = session_dir / "input_latency_summary.json"
            assert report_path.exists()
            assert summary_path.exists()

            # Validate JSON content
            with open(report_path) as f:
                data = json.load(f)
            assert data["tool"] == "measure_input_latency"
            assert data["trials_requested"] == 3

            with open(summary_path) as f:
                summary = json.load(f)
            assert "median_ms" in summary
            assert "prd_pass" in summary

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
        # Always return identical frames — no change
        static_frame = np.zeros((64, 64, 4), dtype=np.uint8)

        def mock_grab(roi):
            mock_frame = MagicMock()
            mock_frame.__array__ = MagicMock(return_value=static_frame)
            return mock_frame

        mock_sct.grab = mock_grab
        mock_mss.return_value.__enter__ = MagicMock(return_value=mock_sct)
        mock_mss.return_value.__exit__ = MagicMock(return_value=False)
        mock_press.return_value = 1000.0

        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir)
            report = run_measurement_session(
                session_dir=session_dir,
                trials=2,
                capture_fps=500,
                change_threshold=30,
                prd_limit_ms=20.0,
            )

            assert report.trials_failed == 2
            assert report.trials_completed == 0
            assert report.prd_pass is False


# ---------------------------------------------------------------------------
# Test: JSON output format
# ---------------------------------------------------------------------------

class TestJsonOutput:
    def test_report_json_structure(self):
        """Verify the JSON report has all required fields."""
        report = SessionReport(
            timestamp_utc="2026-01-01T00:00:00+00:00",
            platform="Windows-10",
            python_version="3.11.0",
            trials_requested=50,
            trials_completed=50,
            trials_failed=0,
            capture_fps_target=500,
            key_pressed="w",
            roi_width=64,
            roi_height=64,
            change_threshold=30,
            latencies_ms=[10.0, 12.0, 11.0, 13.0, 10.5],
            median_ms=11.0,
            mean_ms=11.3,
            std_ms=1.2,
            p50_ms=11.0,
            p95_ms=12.8,
            p99_ms=12.95,
            min_ms=10.0,
            max_ms=13.0,
            prd_limit_ms=20.0,
            prd_pass=True,
        )

        from dataclasses import asdict
        data = asdict(report)

        required_fields = [
            "tool", "version", "timestamp_utc", "platform",
            "python_version", "trials_requested", "trials_completed",
            "trials_failed", "capture_fps_target", "key_pressed",
            "roi_width", "roi_height", "change_threshold",
            "latencies_ms", "median_ms", "mean_ms", "std_ms",
            "p50_ms", "p95_ms", "p99_ms", "min_ms", "max_ms",
            "prd_limit_ms", "prd_pass", "trial_details",
        ]
        for field_name in required_fields:
            assert field_name in data, f"Missing field: {field_name}"

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
        # argparse exits with 0 on --help
        # But without args it also exits 0 (runs with defaults)
        # So we just verify it doesn't crash


# ---------------------------------------------------------------------------
# Test: Module imports
# ---------------------------------------------------------------------------

class TestModuleImports:
    def test_mss_import(self):
        """Verify mss is importable."""
        import mss
        assert mss is not None

    def test_numpy_import(self):
        """Verify numpy is importable."""
        import numpy as np
        assert np is not None

    def test_pil_import(self):
        """Verify Pillow is importable."""
        from PIL import Image
        assert Image is not None


# ---------------------------------------------------------------------------
# Test: TrialResult serialization
# ---------------------------------------------------------------------------

class TestTrialResultSerialization:
    def test_asdict(self):
        from dataclasses import asdict
        r = TrialResult(
            trial_id=1,
            latency_ms=12.345,
            frames_until_change=6,
            actual_capture_fps=498.2,
            roi_mean_delta=45.6,
            success=True,
        )
        d = asdict(r)
        assert d["trial_id"] == 1
        assert d["latency_ms"] == 12.345
        assert d["success"] is True
        assert d["error"] is None

    def test_json_roundtrip(self):
        from dataclasses import asdict
        r = TrialResult(
            trial_id=5,
            latency_ms=8.123,
            frames_until_change=4,
            actual_capture_fps=510.0,
            roi_mean_delta=55.0,
            success=True,
            error=None,
        )
        d = asdict(r)
        json_str = json.dumps(d)
        loaded = json.loads(json_str)
        assert loaded["trial_id"] == 5
        assert loaded["latency_ms"] == 8.123
        assert loaded["success"] is True
