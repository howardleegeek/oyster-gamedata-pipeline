#!/usr/bin/env python3
"""Tests for bin/recorder_audio_loopback.py — WASAPI loopback audio recording utility."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

# Ensure the bin module is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from bin import recorder_audio_loopback


class TestCheckWasapiAvailable:
    """Verify WASAPI availability detection."""

    def test_wasapi_available_when_loopback_in_output(self):
        """check_wasapi_available returns True when loopback device found."""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.MagicMock(
                returncode=0,
                stderr="DirectShow Audio Devices: ... loopback ...",
            )
            result = recorder_audio_loopback.check_wasapi_available()
            assert result is True

    def test_wasapi_available_when_wasapi_in_output(self):
        """check_wasapi_available returns True when WASAPI device found."""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.MagicMock(
                returncode=0,
                stderr="DirectShow Audio Devices: ... wasapi ...",
            )
            result = recorder_audio_loopback.check_wasapi_available()
            assert result is True

    def test_wasapi_not_available_when_no_loopback(self):
        """check_wasapi_available returns False when no loopback device."""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.MagicMock(
                returncode=0,
                stderr="DirectShow Audio Devices: microphone",
            )
            result = recorder_audio_loopback.check_wasapi_available()
            assert result is False

    def test_wasapi_not_available_on_subprocess_error(self):
        """check_wasapi_available returns False on SubprocessError."""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.side_effect = recorder_audio_loopback.subprocess.SubprocessError()
            result = recorder_audio_loopback.check_wasapi_available()
            assert result is False

    def test_wasapi_not_available_on_file_not_found(self):
        """check_wasapi_available returns False when ffmpeg not found."""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("ffmpeg not found")
            result = recorder_audio_loopback.check_wasapi_available()
            assert result is False


class TestBuildFfmpegCommand:
    """Verify ffmpeg command building."""

    def test_wasapi_mode_default(self):
        """build_ffmpeg_command uses WASAPI loopback by default."""
        output = Path("output.mp3")
        cmd = recorder_audio_loopback.build_ffmpeg_command(output)
        assert cmd[0] == "ffmpeg"
        assert cmd[1] == "-y"
        assert "-f" in cmd
        ffmpeg_input_idx = cmd.index("-f") + 1
        assert cmd[ffmpeg_input_idx] == "dshow"
        # The device string is "audio=loopback"
        assert any("loopback" in arg for arg in cmd)

    def test_dshow_mode_when_use_wasapi_false(self):
        """build_ffmpeg_command uses dshow microphone when use_wasapi=False."""
        output = Path("output.mp3")
        cmd = recorder_audio_loopback.build_ffmpeg_command(output, use_wasapi=False)
        # Device string is "audio=麦克风"
        assert not any("loopback" in arg for arg in cmd)
        assert any("麦克风" in arg for arg in cmd)

    def test_includes_duration_when_specified(self):
        """build_ffmpeg_command includes -t when duration is provided."""
        output = Path("output.mp3")
        cmd = recorder_audio_loopback.build_ffmpeg_command(output, duration=60)
        assert "-t" in cmd
        t_idx = cmd.index("-t") + 1
        assert cmd[t_idx] == "60"

    def test_no_duration_when_none(self):
        """build_ffmpeg_command omits -t when duration is None."""
        output = Path("output.mp3")
        cmd = recorder_audio_loopback.build_ffmpeg_command(output, duration=None)
        assert "-t" not in cmd

    def test_output_path_at_end(self):
        """build_ffmpeg_command places output path at the end."""
        output = Path("/path/to/recording.mp3")
        cmd = recorder_audio_loopback.build_ffmpeg_command(output)
        assert cmd[-1] == str(output)

    def test_uses_libmp3lame_codec(self):
        """build_ffmpeg_command uses libmp3lame codec."""
        output = Path("output.mp3")
        cmd = recorder_audio_loopback.build_ffmpeg_command(output)
        assert "-acodec" in cmd
        acodec_idx = cmd.index("-acodec") + 1
        assert cmd[acodec_idx] == "libmp3lame"

    def test_uses_192k_bitrate(self):
        """build_ffmpeg_command uses 192k bitrate."""
        output = Path("output.mp3")
        cmd = recorder_audio_loopback.build_ffmpeg_command(output)
        assert "-ab" in cmd
        ab_idx = cmd.index("-ab") + 1
        assert cmd[ab_idx] == "192k"


class TestRecordAudio:
    """Verify audio recording function."""

    def test_uses_wasapi_when_available(self):
        """record_audio uses WASAPI when check_wasapi_available returns True."""
        with mock.patch(
            "bin.recorder_audio_loopback.check_wasapi_available"
        ) as mock_check, mock.patch(
            "bin.recorder_audio_loopback.build_ffmpeg_command"
        ) as mock_build, mock.patch(
            "subprocess.run"
        ) as mock_run:
            mock_check.return_value = True
            mock_build.return_value = ["ffmpeg", "-y", "-i", "loopback", "out.mp3"]
            mock_run.return_value = mock.MagicMock(returncode=0)

            result = recorder_audio_loopback.record_audio(
                Path("out.mp3"), prefer_wasapi=True
            )

            assert result == 0
            mock_check.assert_called_once()
            mock_build.assert_called_once_with(Path("out.mp3"), None, True)

    def test_falls_back_to_dshow_when_wasapi_unavailable(self):
        """record_audio falls back to dshow when WASAPI unavailable."""
        with mock.patch(
            "bin.recorder_audio_loopback.check_wasapi_available"
        ) as mock_check, mock.patch(
            "bin.recorder_audio_loopback.build_ffmpeg_command"
        ) as mock_build, mock.patch(
            "subprocess.run"
        ) as mock_run:
            mock_check.return_value = False
            mock_build.return_value = ["ffmpeg", "-y", "-i", "麦克风", "out.mp3"]
            mock_run.return_value = mock.MagicMock(returncode=0)

            result = recorder_audio_loopback.record_audio(
                Path("out.mp3"), prefer_wasapi=True
            )

            assert result == 0
            # use_wasapi=False when WASAPI unavailable
            mock_build.assert_called_once_with(Path("out.mp3"), None, False)

    def test_respects_no_wasapi_flag(self):
        """record_audio respects prefer_wasapi=False to skip WASAPI check."""
        with mock.patch(
            "bin.recorder_audio_loopback.check_wasapi_available"
        ) as mock_check, mock.patch(
            "bin.recorder_audio_loopback.build_ffmpeg_command"
        ) as mock_build, mock.patch(
            "subprocess.run"
        ) as mock_run:
            mock_build.return_value = ["ffmpeg", "-y", "-i", "麦克风", "out.mp3"]
            mock_run.return_value = mock.MagicMock(returncode=0)

            result = recorder_audio_loopback.record_audio(
                Path("out.mp3"), prefer_wasapi=False
            )

            # Should not call check_wasapi_available when prefer_wasapi=False
            mock_check.assert_not_called()
            # Should call with use_wasapi=False
            mock_build.assert_called_once_with(Path("out.mp3"), None, False)

    def test_returns_nonzero_on_subprocess_error(self):
        """record_audio returns 1 on SubprocessError."""
        with mock.patch(
            "bin.recorder_audio_loopback.check_wasapi_available"
        ) as mock_check, mock.patch(
            "bin.recorder_audio_loopback.build_ffmpeg_command"
        ) as mock_build:
            mock_check.return_value = True
            mock_build.return_value = ["ffmpeg", "-y", "-i", "loopback", "out.mp3"]

            with mock.patch("subprocess.run") as mock_run:
                mock_run.side_effect = recorder_audio_loopback.subprocess.SubprocessError(
                    "Boom"
                )

                result = recorder_audio_loopback.record_audio(Path("out.mp3"))

                assert result == 1

    def test_passes_duration_to_command(self):
        """record_audio passes duration parameter to build_ffmpeg_command."""
        with mock.patch(
            "bin.recorder_audio_loopback.check_wasapi_available"
        ) as mock_check, mock.patch(
            "bin.recorder_audio_loopback.build_ffmpeg_command"
        ) as mock_build, mock.patch(
            "subprocess.run"
        ) as mock_run:
            mock_check.return_value = True
            mock_build.return_value = ["ffmpeg", "-y", "-i", "loopback", "out.mp3"]
            mock_run.return_value = mock.MagicMock(returncode=0)

            recorder_audio_loopback.record_audio(
                Path("out.mp3"), duration=120, prefer_wasapi=True
            )

            mock_build.assert_called_once_with(Path("out.mp3"), 120, True)


class TestMain:
    """Verify CLI argument parsing."""

    def test_default_output(self):
        """main uses recording.mp3 as default output."""
        with mock.patch("bin.recorder_audio_loopback.record_audio") as mock_record:
            mock_record.return_value = 0

            recorder_audio_loopback.main([])

            mock_record.assert_called_once()
            call_kwargs = mock_record.call_args[1]
            assert call_kwargs["output_path"] == Path("recording.mp3")

    def test_custom_output(self):
        """main respects --output argument."""
        with mock.patch("bin.recorder_audio_loopback.record_audio") as mock_record:
            mock_record.return_value = 0

            recorder_audio_loopback.main(["--output", "/tmp/my_audio.mp3"])

            mock_record.assert_called_once()
            call_kwargs = mock_record.call_args[1]
            assert call_kwargs["output_path"] == Path("/tmp/my_audio.mp3")

    def test_custom_output_short_flag(self):
        """main respects -o short flag."""
        with mock.patch("bin.recorder_audio_loopback.record_audio") as mock_record:
            mock_record.return_value = 0

            recorder_audio_loopback.main(["-o", "/tmp/test.mp3"])

            call_kwargs = mock_record.call_args[1]
            assert call_kwargs["output_path"] == Path("/tmp/test.mp3")

    def test_duration_argument(self):
        """main passes --duration to record_audio."""
        with mock.patch("bin.recorder_audio_loopback.record_audio") as mock_record:
            mock_record.return_value = 0

            recorder_audio_loopback.main(["--duration", "300"])

            call_kwargs = mock_record.call_args[1]
            assert call_kwargs["duration"] == 300

    def test_duration_short_flag(self):
        """main passes -d to record_audio."""
        with mock.patch("bin.recorder_audio_loopback.record_audio") as mock_record:
            mock_record.return_value = 0

            recorder_audio_loopback.main(["-d", "60"])

            call_kwargs = mock_record.call_args[1]
            assert call_kwargs["duration"] == 60

    def test_no_wasapi_flag(self):
        """main passes --no-wasapi to record_audio."""
        with mock.patch("bin.recorder_audio_loopback.record_audio") as mock_record:
            mock_record.return_value = 0

            recorder_audio_loopback.main(["--no-wasapi"])

            call_kwargs = mock_record.call_args[1]
            assert call_kwargs["prefer_wasapi"] is False

    def test_verbose_flag_enables_debug(self):
        """main sets DEBUG log level when --verbose is passed."""
        # Just verify it parses without error and calls record_audio
        with mock.patch("bin.recorder_audio_loopback.record_audio") as mock_record:
            mock_record.return_value = 0

            recorder_audio_loopback.main(["--verbose"])

            mock_record.assert_called_once()

    def test_exit_code_from_record_audio(self):
        """main returns exit code from record_audio."""
        with mock.patch("bin.recorder_audio_loopback.record_audio") as mock_record:
            mock_record.return_value = 2

            exit_code = recorder_audio_loopback.main([])

            assert exit_code == 2

    def test_creates_output_directory(self):
        """main creates parent directory for output path."""
        with mock.patch(
            "bin.recorder_audio_loopback.record_audio"
        ) as mock_record, mock.patch(
            "pathlib.Path.mkdir"
        ) as mock_mkdir:
            mock_record.return_value = 0

            recorder_audio_loopback.main(["--output", "/tmp/nested/dir/out.mp3"])

            # Verify mkdir was called with parents=True, exist_ok=True
            mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)
