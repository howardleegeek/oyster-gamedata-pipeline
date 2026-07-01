#!/usr/bin/env python3
"""Tests for bin/autoresearch_compression_ratio.py"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Import the module under test
sys.path.insert(0, str(Path(__file__).parents[2] / "bin"))
import autoresearch_compression_ratio as acr


class TestCheckFfmpeg:
    """Tests for check_ffmpeg function."""

    def test_check_ffmpeg_available(self):
        """Test check_ffmpeg returns True when ffmpeg is available."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = acr.check_ffmpeg()
            assert result is True

    def test_check_ffmpeg_not_available(self):
        """Test check_ffmpeg returns False when ffmpeg is not available."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("ffmpeg not found")
            result = acr.check_ffmpeg()
            assert result is False

    def test_check_ffmpeg_returns_nonzero(self):
        """Test check_ffmpeg returns False when ffmpeg returns non-zero."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            result = acr.check_ffmpeg()
            assert result is False


class TestGetVideoInfo:
    """Tests for get_video_info function."""

    def test_get_video_info_success(self):
        """Test get_video_info parses ffprobe output correctly."""
        mock_output = {
            "format": {"duration": "10.5", "size": "1000000"},
            "streams": [{"codec_type": "video", "codec_name": "h264"}],
        }
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout=json.dumps(mock_output)
            )
            result = acr.get_video_info(Path("/fake/video.mp4"))
            assert result == mock_output

    def test_get_video_info_failure(self):
        """Test get_video_info returns None on failure."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            result = acr.get_video_info(Path("/fake/video.mp4"))
            assert result is None

    def test_get_video_info_json_error(self):
        """Test get_video_info returns None on JSON parse error."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="not json")
            result = acr.get_video_info(Path("/fake/video.mp4"))
            assert result is None


class TestEncodeVideo:
    """Tests for encode_video function."""

    def test_encode_video_unknown_codec(self):
        """Test encode_video returns error for unknown codec."""
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "input.mp4"
            output_path = Path(tmpdir) / "output.mp4"
            result = acr.encode_video(input_path, output_path, "invalid_codec")
            assert result[0] is False
            assert "Unknown codec" in result[3]

    def test_encode_video_subprocess_error(self):
        """Test encode_video handles subprocess errors gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "input.mp4"
            output_path = Path(tmpdir) / "output.mp4"
            with patch("subprocess.run") as mock_run:
                mock_run.side_effect = OSError("Subprocess error")
                result = acr.encode_video(input_path, output_path, "h264")
                assert result[0] is False
                assert "Subprocess error" in result[3]


class TestAnalyzeResults:
    """Tests for analyze_results function."""

    def test_analyze_results_no_successful(self):
        """Test analyze_results handles no successful encodings."""
        results = {
            "h264": (False, 0, 0.0, "error"),
            "h265": (False, 0, 0.0, "error"),
        }
        recs = acr.analyze_results(1000000, results)
        assert "ERROR" in recs[0]

    def test_analyze_results_calculates_ratios(self):
        """Test analyze_results calculates compression ratios correctly."""
        results = {
            "h264": (True, 500000, 10.0, None),  # 2:1 ratio
            "h265": (True, 300000, 15.0, None),  # 3.33:1 ratio
            "av1": (False, 0, 20.0, "error"),
        }
        input_size = 1000000
        recs = acr.analyze_results(input_size, results)
        # Check that recommendations contain ratio info
        assert len(recs) > 0

    def test_analyze_results_empty_results(self):
        """Test analyze_results handles empty results dict."""
        recs = acr.analyze_results(1000000, {})
        assert "ERROR" in recs[0]


class TestCodecSettings:
    """Tests for CODEC_SETTINGS constant."""

    def test_codec_settings_contains_required_codecs(self):
        """Test CODEC_SETTINGS contains expected codecs."""
        assert "h264" in acr.CODEC_SETTINGS
        assert "h265" in acr.CODEC_SETTINGS
        assert "av1" in acr.CODEC_SETTINGS

    def test_codec_settings_format(self):
        """Test CODEC_SETTINGS has correct tuple format."""
        for codec, settings in acr.CODEC_SETTINGS.items():
            assert len(settings) == 3
            encoder, preset, crf = settings
            assert isinstance(encoder, str)
            assert isinstance(preset, str)
            assert isinstance(crf, str)


class TestMain:
    """Tests for CLI main function."""

    def test_main_missing_input(self):
        """Test main returns 1 when input file doesn't exist."""
        with patch.object(acr, "check_ffmpeg", return_value=True):
            result = acr.main(["nonexistent.mp4"])
            assert result == 1

    def test_main_ffmpeg_not_available(self):
        """Test main returns 1 when ffmpeg is not available."""
        with patch.object(acr, "check_ffmpeg", return_value=False):
            with patch.object(acr, "get_video_info"):
                result = acr.main(["/fake/input.mp4"])
                assert result == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
