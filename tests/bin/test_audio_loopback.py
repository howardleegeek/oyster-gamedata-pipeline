#!/usr/bin/env python3
"""Tests for bin/audio_loopback.py — privacy-safe audio capture planning."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

# Ensure the bin module is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from bin.audio_loopback import (
    AudioCaptureMode,
    AudioCapturePlan,
    _cli,
    build_ffmpeg_args,
    plan_audio_capture,
)


class TestAudioCaptureMode:
    """Verify the enum-like constants are defined."""

    def test_mode_constants_exist(self):
        assert AudioCaptureMode.WASAPI_LOOPBACK == "wasapi_loopback"
        assert AudioCaptureMode.DSHOW_LOOPBACK_FILTER == "dshow_loopback_filter"
        assert AudioCaptureMode.DSHOW_MICROPHONE == "dshow_microphone"
        assert AudioCaptureMode.NONE == "none"


class TestAudioCapturePlan:
    """Verify the frozen dataclass works as expected."""

    def test_creation_with_required_fields(self):
        plan = AudioCapturePlan(mode=AudioCaptureMode.WASAPI_LOOPBACK)
        assert plan.mode == AudioCaptureMode.WASAPI_LOOPBACK
        assert plan.device_name is None
        assert plan.fallback_used is False
        assert plan.notes == []

    def test_creation_with_all_fields(self):
        plan = AudioCapturePlan(
            mode=AudioCaptureMode.DSHOW_LOOPBACK_FILTER,
            device_name="Stereo Mix",
            fallback_used=False,
            notes=["note1", "note2"],
        )
        assert plan.mode == AudioCaptureMode.DSHOW_LOOPBACK_FILTER
        assert plan.device_name == "Stereo Mix"
        # fallback_used is False because loopback filter is a preferred non-WASAPI path
        assert plan.fallback_used is False
        assert plan.notes == ["note1", "note2"]

    def test_frozen_immutability(self):
        plan = AudioCapturePlan(mode=AudioCaptureMode.NONE)
        with pytest.raises(AttributeError):
            plan.mode = AudioCaptureMode.WASAPI_LOOPBACK  # type: ignore


class TestBuildFfmpegArgs:
    """Verify ffmpeg argument generation for each capture mode."""

    def test_wasapi_loopback(self):
        plan = AudioCapturePlan(mode=AudioCaptureMode.WASAPI_LOOPBACK)
        args = build_ffmpeg_args(plan)
        assert args == ["-f", "wasapi", "-i", "loopback"]

    def test_dshow_loopback_filter_requires_device_name(self):
        plan = AudioCapturePlan(
            mode=AudioCaptureMode.DSHOW_LOOPBACK_FILTER, device_name=None
        )
        with pytest.raises(ValueError, match="missing device_name"):
            build_ffmpeg_args(plan)

    def test_dshow_loopback_filter_with_device(self):
        plan = AudioCapturePlan(
            mode=AudioCaptureMode.DSHOW_LOOPBACK_FILTER,
            device_name="Virtual Audio Cable",
        )
        args = build_ffmpeg_args(plan)
        assert args == ["-f", "dshow", "-i", "audio=Virtual Audio Cable"]

    def test_dshow_microphone_requires_device_name(self):
        plan = AudioCapturePlan(mode=AudioCaptureMode.DSHOW_MICROPHONE, device_name=None)
        with pytest.raises(ValueError, match="missing device_name"):
            build_ffmpeg_args(plan)

    def test_dshow_microphone_with_device(self):
        plan = AudioCapturePlan(
            mode=AudioCaptureMode.DSHOW_MICROPHONE,
            device_name="Microphone (Realtek)",
        )
        args = build_ffmpeg_args(plan)
        assert args == ["-f", "dshow", "-i", "audio=Microphone (Realtek)"]

    def test_none_mode_returns_empty(self):
        plan = AudioCapturePlan(mode=AudioCaptureMode.NONE)
        args = build_ffmpeg_args(plan)
        assert args == []

    def test_unknown_mode_raises(self):
        class UnknownMode:
            pass

        plan = AudioCapturePlan(mode="unknown")  # type: ignore
        with pytest.raises(ValueError, match="unknown audio capture mode"):
            build_ffmpeg_args(plan)


class TestPlanAudioCapture:
    """Test the probe and decision logic with mocked ffmpeg."""

    @mock.patch("bin.audio_loopback._run_ffmpeg")
    @mock.patch("bin.audio_loopback._ffmpeg_supports_wasapi")
    def test_wasapi_available_returns_wasapi_loopback(
        self, mock_wasapi, mock_run_ffmpeg
    ):
        mock_wasapi.return_value = True
        mock_run_ffmpeg.return_value = ""  # unused when wasapi found

        plan = plan_audio_capture(prefer_wasapi=True)

        assert plan.mode == AudioCaptureMode.WASAPI_LOOPBACK
        assert plan.fallback_used is False

    @mock.patch("bin.audio_loopback._run_ffmpeg")
    @mock.patch("bin.audio_loopback._ffmpeg_supports_wasapi")
    @mock.patch("bin.audio_loopback._list_dshow_audio_devices")
    def test_no_wasapi_but_dshow_loopback_returns_dshow_filter(
        self, mock_dshow_list, mock_wasapi, mock_run_ffmpeg
    ):
        mock_wasapi.return_value = False
        mock_dshow_list.return_value = ["Stereo Mix", "Microphone (Realtek)"]

        plan = plan_audio_capture(prefer_wasapi=True)

        assert plan.mode == AudioCaptureMode.DSHOW_LOOPBACK_FILTER
        assert plan.device_name == "Stereo Mix"
        # dshow loopback filter is a preferred path (not fallback) when WASAPI unavailable
        assert plan.fallback_used is False

    @mock.patch("bin.audio_loopback._run_ffmpeg")
    @mock.patch("bin.audio_loopback._ffmpeg_supports_wasapi")
    @mock.patch("bin.audio_loopback._list_dshow_audio_devices")
    def test_no_wasapi_no_loopback_falls_back_to_mic(
        self, mock_dshow_list, mock_wasapi, mock_run_ffmpeg
    ):
        mock_wasapi.return_value = False
        mock_dshow_list.return_value = ["Microphone (Realtek)"]

        plan = plan_audio_capture(prefer_wasapi=True)

        assert plan.mode == AudioCaptureMode.DSHOW_MICROPHONE
        assert plan.device_name == "Microphone (Realtek)"
        assert plan.fallback_used is True

    @mock.patch("bin.audio_loopback._run_ffmpeg")
    @mock.patch("bin.audio_loopback._ffmpeg_supports_wasapi")
    @mock.patch("bin.audio_loopback._list_dshow_audio_devices")
    def test_no_devices_returns_none(self, mock_dshow_list, mock_wasapi, mock_run_ffmpeg):
        mock_wasapi.return_value = False
        mock_dshow_list.return_value = []

        plan = plan_audio_capture(prefer_wasapi=True)

        assert plan.mode == AudioCaptureMode.NONE
        assert plan.fallback_used is True

    @mock.patch("bin.audio_loopback._run_ffmpeg")
    @mock.patch("bin.audio_loopback._ffmpeg_supports_wasapi")
    def test_prefer_wasapi_false_skips_wasapi_probe(self, mock_wasapi, mock_run_ffmpeg):
        mock_wasapi.return_value = True  # should NOT be called
        plan_audio_capture(prefer_wasapi=False)
        mock_wasapi.assert_not_called()


class TestCliEntryPoint:
    """Test the main() CLI entry point with various argument combinations."""

    @mock.patch("bin.audio_loopback.plan_audio_capture")
    @mock.patch("bin.audio_loopback.build_ffmpeg_args")
    def test_cli_defaults_emits_human_text(self, mock_build, mock_plan):
        mock_plan.return_value = AudioCapturePlan(
            mode=AudioCaptureMode.WASAPI_LOOPBACK,
            device_name=None,
            fallback_used=False,
            notes=[],
        )
        mock_build.return_value = ["-f", "wasapi", "-i", "loopback"]

        with mock.patch.object(sys, "argv", ["audio_loopback.py"]):
            rc = _cli([])

        assert rc == 0

    @mock.patch("bin.audio_loopback.plan_audio_capture")
    @mock.patch("bin.audio_loopback.build_ffmpeg_args")
    def test_cli_json_flag_emits_json(self, mock_build, mock_plan):
        import io

        mock_plan.return_value = AudioCapturePlan(
            mode=AudioCaptureMode.DSHOW_LOOPBACK_FILTER,
            device_name="Stereo Mix",
            fallback_used=True,
            notes=["fallback used"],
        )
        mock_build.return_value = ["-f", "dshow", "-i", "audio=Stereo Mix"]

        with mock.patch.object(sys, "argv", ["audio_loopback.py", "--json"]):
            with mock.patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
                rc = _cli(["--json"])

        assert rc == 0
        output = mock_stdout.getvalue()
        data = json.loads(output)
        assert data["mode"] == AudioCaptureMode.DSHOW_LOOPBACK_FILTER
        assert data["device_name"] == "Stereo Mix"
        assert data["fallback_used"] is True

    @mock.patch("bin.audio_loopback.plan_audio_capture")
    def test_cli_no_wasapi_flag(self, mock_plan):
        mock_plan.return_value = AudioCapturePlan(
            mode=AudioCaptureMode.NONE, fallback_used=True, notes=[]
        )

        with mock.patch.object(sys, "argv", ["audio_loopback.py", "--no-wasapi"]):
            rc = _cli(["--no-wasapi"])

        assert rc == 0
        mock_plan.assert_called_once_with(prefer_wasapi=False)


class TestIntegrationSmoke:
    """Smoke tests that don't require ffmpeg."""

    def test_import_succeeds(self):
        # Verify the module loads without errors
        import bin.audio_loopback
        assert hasattr(bin.audio_loopback, "AudioCaptureMode")
        assert hasattr(bin.audio_loopback, "AudioCapturePlan")
        assert hasattr(bin.audio_loopback, "plan_audio_capture")
        assert hasattr(bin.audio_loopback, "build_ffmpeg_args")

    def test_module_docstring_mentions_privacy(self):
        import bin.audio_loopback
        doc = bin.audio_loopback.__doc__
        assert "privacy" in doc.lower() or "G279" in doc or "B4" in doc
