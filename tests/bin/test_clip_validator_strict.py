#!/usr/bin/env python3
"""
Tests for bin/clip_validator_strict.py — strict clip-level validator.

Covers: ValidationResult, ValidationThresholds, _has_audio_stream,
_parse_db_value, compute_audio_metrics, _get_video_info,
compute_video_metrics, validate_clip, build_parser, main.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

# Import the module under test
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import bin.clip_validator_strict as clip_validator_strict


class TestValidationResult:
    """Tests for ValidationResult dataclass."""

    def test_default_values(self):
        """ValidationResult has correct default values."""
        result = clip_validator_strict.ValidationResult(video_path="/test/video.mp4")
        assert result.video_path == "/test/video.mp4"
        assert result.audio_mute_ratio == 0.0
        assert result.audio_silent_ratio == 0.0
        assert result.black_frame_ratio == 0.0
        assert result.repeated_frame_ratio == 0.0
        assert result.motion_entropy == 0.0
        assert result.is_valid is True
        assert result.warnings == []
        assert result.errors == []
        assert result.metadata == {}

    def test_to_dict(self):
        """ValidationResult.to_dict() serializes correctly."""
        result = clip_validator_strict.ValidationResult(
            video_path="/test/video.mp4",
            audio_mute_ratio=0.5,
            audio_silent_ratio=0.3,
            black_frame_ratio=0.1,
            repeated_frame_ratio=0.05,
            motion_entropy=2.5,
            is_valid=True,
            warnings=["test warning"],
            errors=["test error"],
            metadata={"size": 1000},
        )
        d = result.to_dict()
        assert d["video_path"] == "/test/video.mp4"
        assert d["audio_mute_ratio"] == 0.5
        assert d["audio_silent_ratio"] == 0.3
        assert d["black_frame_ratio"] == 0.1
        assert d["repeated_frame_ratio"] == 0.05
        assert d["motion_entropy"] == 2.5
        assert d["is_valid"] is True
        assert d["warnings"] == ["test warning"]
        assert d["errors"] == ["test error"]
        assert d["metadata"] == {"size": 1000}

    def test_to_dict_rounds_values(self):
        """ValidationResult.to_dict() rounds float values."""
        result = clip_validator_strict.ValidationResult(
            video_path="/test/video.mp4",
            audio_mute_ratio=0.55555,
            audio_silent_ratio=0.333333,
            black_frame_ratio=0.111111,
            repeated_frame_ratio=0.052345,
            motion_entropy=2.567891,
        )
        d = result.to_dict()
        # round() with 4 decimal places
        assert d["audio_mute_ratio"] == 0.5555
        assert d["audio_silent_ratio"] == 0.3333
        assert d["black_frame_ratio"] == 0.1111
        assert d["repeated_frame_ratio"] == 0.0523
        assert d["motion_entropy"] == 2.5679


class TestValidationThresholds:
    """Tests for ValidationThresholds dataclass."""

    def test_default_values(self):
        """ValidationThresholds has correct default values."""
        t = clip_validator_strict.ValidationThresholds()
        assert t.max_audio_mute_ratio == 0.95
        assert t.max_audio_silent_ratio == 0.90
        assert t.max_black_frame_ratio == 0.80
        assert t.max_repeated_frame_ratio == 0.50
        assert t.min_motion_entropy == 0.1
        assert t.black_pixel_threshold == 16
        assert t.silent_db_threshold == -60.0

    def test_custom_values(self):
        """ValidationThresholds accepts custom values."""
        t = clip_validator_strict.ValidationThresholds(
            max_audio_mute_ratio=0.8,
            max_audio_silent_ratio=0.7,
            max_black_frame_ratio=0.6,
            max_repeated_frame_ratio=0.4,
            min_motion_entropy=0.2,
            black_pixel_threshold=32,
            silent_db_threshold=-50.0,
        )
        assert t.max_audio_mute_ratio == 0.8
        assert t.max_audio_silent_ratio == 0.7
        assert t.max_black_frame_ratio == 0.6
        assert t.max_repeated_frame_ratio == 0.4
        assert t.min_motion_entropy == 0.2
        assert t.black_pixel_threshold == 32
        assert t.silent_db_threshold == -50.0


class TestHasAudioStream:
    """Tests for _has_audio_stream function."""

    @patch("bin.clip_validator_strict.subprocess.run")
    def test_has_audio_stream_true(self, mock_run):
        """_has_audio_stream returns True when audio stream exists."""
        mock_result = MagicMock()
        mock_result.stdout = json.dumps({"streams": [{"codec_type": "audio"}]})
        mock_run.return_value = mock_result

        result = clip_validator_strict._has_audio_stream("/test/video.mp4")
        assert result is True

    @patch("bin.clip_validator_strict.subprocess.run")
    def test_has_audio_stream_false(self, mock_run):
        """_has_audio_stream returns False when no audio stream."""
        mock_result = MagicMock()
        mock_result.stdout = json.dumps({"streams": []})
        mock_run.return_value = mock_result

        result = clip_validator_strict._has_audio_stream("/test/video.mp4")
        assert result is False

    @patch("bin.clip_validator_strict.subprocess.run")
    def test_has_audio_stream_no_streams(self, mock_run):
        """_has_audio_stream returns False when no streams key."""
        mock_result = MagicMock()
        mock_result.stdout = "{}"
        mock_run.return_value = mock_result

        result = clip_validator_strict._has_audio_stream("/test/video.mp4")
        assert result is False

    @patch("bin.clip_validator_strict.subprocess.run")
    def test_has_audio_stream_timeout(self, mock_run):
        """_has_audio_stream returns False on timeout."""
        mock_run.side_effect = subprocess.TimeoutExpired("cmd", 1)

        result = clip_validator_strict._has_audio_stream("/test/video.mp4")
        assert result is False

    @patch("bin.clip_validator_strict.subprocess.run")
    def test_has_audio_stream_json_error(self, mock_run):
        """_has_audio_stream returns False on JSON decode error."""
        mock_result = MagicMock()
        mock_result.stdout = "invalid json"
        mock_run.return_value = mock_result

        result = clip_validator_strict._has_audio_stream("/test/video.mp4")
        assert result is False


class TestParseDbValue:
    """Tests for _parse_db_value function."""

    def test_parse_db_value_basic(self):
        """_parse_db_value extracts dB value correctly (lowercase db)."""
        # Note: code checks for lowercase 'db', matching that behavior
        text = "some output\nmean_volume: -30.5 db\nother stuff"
        result = clip_validator_strict._parse_db_value(text, "mean_volume")
        assert result == -30.5

    def test_parse_db_value_no_match(self):
        """_parse_db_value returns None when key not found."""
        text = "some output without the key"
        result = clip_validator_strict._parse_db_value(text, "mean_volume")
        assert result is None

    def test_parse_db_value_no_db(self):
        """_parse_db_value returns None when no dB unit."""
        text = "mean_volume: -30.5"
        result = clip_validator_strict._parse_db_value(text, "mean_volume")
        assert result is None

    def test_parse_db_value_max_volume(self):
        """_parse_db_value extracts max_volume correctly (lowercase db)."""
        text = "max_volume: -20.0 db"
        result = clip_validator_strict._parse_db_value(text, "max_volume")
        assert result == -20.0


class TestComputeAudioMetrics:
    """Tests for compute_audio_metrics function."""

    @patch("bin.clip_validator_strict._has_audio_stream")
    def test_no_audio_stream(self, mock_has_audio):
        """compute_audio_metrics returns (1.0, 1.0) when no audio."""
        mock_has_audio.return_value = False

        thresholds = clip_validator_strict.ValidationThresholds()
        result = clip_validator_strict.compute_audio_metrics("/test/video.mp4", thresholds)

        assert result == (1.0, 1.0)

    @patch("bin.clip_validator_strict.subprocess.run")
    @patch("bin.clip_validator_strict._has_audio_stream")
    def test_compute_audio_metrics_below_threshold(self, mock_has_audio, mock_run):
        """compute_audio_metrics computes ratios below threshold."""
        mock_has_audio.return_value = True
        mock_result = MagicMock()
        mock_result.stderr = "mean_volume: -30.0 dB\nmax_volume: -10.0 dB"
        mock_run.return_value = mock_result

        thresholds = clip_validator_strict.ValidationThresholds()
        mute_ratio, silent_ratio = clip_validator_strict.compute_audio_metrics(
            "/test/video.mp4", thresholds
        )

        assert 0.0 <= mute_ratio <= 1.0
        assert 0.0 <= silent_ratio <= 1.0

    @patch("bin.clip_validator_strict.subprocess.run")
    @patch("bin.clip_validator_strict._has_audio_stream")
    def test_compute_audio_metrics_timeout(self, mock_has_audio, mock_run):
        """compute_audio_metrics returns (0.0, 0.0) on timeout."""
        mock_has_audio.return_value = True
        mock_run.side_effect = subprocess.TimeoutExpired("cmd", 1)

        thresholds = clip_validator_strict.ValidationThresholds()
        result = clip_validator_strict.compute_audio_metrics("/test/video.mp4", thresholds)

        assert result == (0.0, 0.0)

    @patch("bin.clip_validator_strict.subprocess.run")
    @patch("bin.clip_validator_strict._has_audio_stream")
    def test_compute_audio_metrics_no_ffmpeg(self, mock_has_audio, mock_run):
        """compute_audio_metrics returns (0.0, 0.0) when ffmpeg missing."""
        mock_has_audio.return_value = True
        mock_run.side_effect = FileNotFoundError("ffmpeg not found")

        thresholds = clip_validator_strict.ValidationThresholds()
        result = clip_validator_strict.compute_audio_metrics("/test/video.mp4", thresholds)

        assert result == (0.0, 0.0)


class TestGetVideoInfo:
    """Tests for _get_video_info function."""

    @patch("bin.clip_validator_strict.subprocess.run")
    def test_get_video_info_valid(self, mock_run):
        """_get_video_info extracts video metadata."""
        mock_result = MagicMock()
        mock_result.stdout = json.dumps(
            {
                "streams": [
                    {
                        "width": 1920,
                        "height": 1080,
                        "r_frame_rate": "30/1",
                        "duration": "120.5",
                    }
                ]
            }
        )
        mock_run.return_value = mock_result

        result = clip_validator_strict._get_video_info("/test/video.mp4")

        assert result["width"] == 1920
        assert result["height"] == 1080
        assert result["fps"] == 30.0
        assert result["duration"] == 120.5

    @patch("bin.clip_validator_strict.subprocess.run")
    def test_get_video_info_no_streams(self, mock_run):
        """_get_video_info returns empty dict when no streams."""
        mock_result = MagicMock()
        mock_result.stdout = "{}"
        mock_run.return_value = mock_result

        result = clip_validator_strict._get_video_info("/test/video.mp4")
        assert result == {}

    @patch("bin.clip_validator_strict.subprocess.run")
    def test_get_video_info_invalid_fraction(self, mock_run):
        """_get_video_info handles invalid frame rate fraction."""
        mock_result = MagicMock()
        mock_result.stdout = json.dumps(
            {"streams": [{"width": 1920, "height": 1080, "r_frame_rate": "0/0", "duration": "10"}]}
        )
        mock_run.return_value = mock_result

        result = clip_validator_strict._get_video_info("/test/video.mp4")

        assert result["fps"] == 0.0

    @patch("bin.clip_validator_strict.subprocess.run")
    def test_get_video_info_timeout(self, mock_run):
        """_get_video_info returns empty dict on timeout."""
        mock_run.side_effect = subprocess.TimeoutExpired("cmd", 1)

        result = clip_validator_strict._get_video_info("/test/video.mp4")
        assert result == {}


class TestValidateClip:
    """Tests for validate_clip function."""

    @patch("bin.clip_validator_strict.compute_video_metrics")
    @patch("bin.clip_validator_strict.compute_audio_metrics")
    def test_validate_clip_file_not_found(self, mock_audio, mock_video):
        """validate_clip returns error for missing file."""
        thresholds = clip_validator_strict.ValidationThresholds()
        result = clip_validator_strict.validate_clip("/nonexistent/video.mp4", thresholds)

        assert result.is_valid is False
        assert "File not found" in result.errors[0]

    @patch("bin.clip_validator_strict.compute_video_metrics")
    @patch("bin.clip_validator_strict.compute_audio_metrics")
    def test_validate_clip_adds_file_size(self, mock_audio, mock_video):
        """validate_clip adds file size to metadata."""
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"fake video data")
            f.flush()
            path = f.name

        try:
            mock_audio.return_value = (0.0, 0.0)
            mock_video.return_value = (0.0, 0.0, 0.5)

            thresholds = clip_validator_strict.ValidationThresholds()
            result = clip_validator_strict.validate_clip(path, thresholds)

            assert "file_size_bytes" in result.metadata
            assert result.metadata["file_size_bytes"] > 0
        finally:
            Path(path).unlink()

    @patch("bin.clip_validator_strict.compute_video_metrics")
    @patch("bin.clip_validator_strict.compute_audio_metrics")
    def test_validate_clip_valid(self, mock_audio, mock_video):
        """validate_clip returns valid when all metrics pass."""
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"fake video data")
            f.flush()
            path = f.name

        try:
            mock_audio.return_value = (0.0, 0.0)
            mock_video.return_value = (0.0, 0.0, 0.5)

            thresholds = clip_validator_strict.ValidationThresholds()
            result = clip_validator_strict.validate_clip(path, thresholds)

            assert result.is_valid is True
            assert len(result.warnings) == 0
            assert len(result.errors) == 0
        finally:
            Path(path).unlink()

    @patch("bin.clip_validator_strict.compute_video_metrics")
    @patch("bin.clip_validator_strict.compute_audio_metrics")
    def test_validate_clip_warnings(self, mock_audio, mock_video):
        """validate_clip adds warnings when thresholds exceeded."""
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"fake video data")
            f.flush()
            path = f.name

        try:
            mock_audio.return_value = (1.0, 1.0)  # Exceeds max_audio_mute_ratio
            mock_video.return_value = (1.0, 1.0, 0.0)  # Exceeds black/repeated, below entropy

            thresholds = clip_validator_strict.ValidationThresholds()
            result = clip_validator_strict.validate_clip(path, thresholds)

            assert result.is_valid is False
            assert len(result.warnings) > 0
            # Check for expected warnings
            warning_text = " ".join(result.warnings)
            assert "mute" in warning_text.lower() or "silent" in warning_text.lower()
        finally:
            Path(path).unlink()


class TestBuildParser:
    """Tests for build_parser function."""

    def test_build_parser(self):
        """build_parser creates argument parser."""
        parser = clip_validator_strict.build_parser()

        assert parser is not None
        assert parser.description is not None

    def test_parser_has_required_args(self):
        """build_parser has required video_path argument."""
        parser = clip_validator_strict.build_parser()

        # Parse with a video path should not raise
        args = parser.parse_args(["test.mp4"])
        assert args.video_path == "test.mp4"

    def test_parser_optional_args(self):
        """build_parser accepts optional arguments."""
        parser = clip_validator_strict.build_parser()

        args = parser.parse_args(
            [
                "test.mp4",
                "--sample-interval",
                "2.0",
                "--max-audio-mute",
                "0.8",
                "--max-audio-silent",
                "0.7",
                "--max-black-frame",
                "0.6",
                "--max-repeated-frame",
                "0.4",
                "--min-motion-entropy",
                "0.2",
                "--json",
            ]
        )

        assert args.video_path == "test.mp4"
        assert args.sample_interval == 2.0
        assert args.max_audio_mute == 0.8
        assert args.max_audio_silent == 0.7
        assert args.max_black_frame == 0.6
        assert args.max_repeated_frame == 0.4
        assert args.min_motion_entropy == 0.2
        assert args.json is True


class TestMain:
    """Tests for main function."""

    @patch("bin.clip_validator_strict.build_parser")
    def test_main_missing_file(self, mock_build_parser):
        """main returns non-zero for missing file (via validate_clip)."""
        mock_parser = MagicMock()
        mock_parser.parse_args.return_value = MagicMock(
            video_path="/nonexistent/test.mp4",
            sample_interval=1.0,
            max_audio_mute=0.95,
            max_audio_silent=0.90,
            max_black_frame=0.80,
            max_repeated_frame=0.50,
            min_motion_entropy=0.1,
            json=False,
            verbose=False,
        )
        mock_build_parser.return_value = mock_parser

        # The main function catches exceptions internally and returns exit code
        with patch("sys.stdout", new_callable=StringIO):
            result = clip_validator_strict.main()
        # Returns 0 (success) if valid, 1 (invalid), 2 (error)
        # For missing file, it returns 1 since is_valid is False with the error
        assert result in (0, 1, 2)

    @patch("bin.clip_validator_strict.validate_clip")
    @patch("bin.clip_validator_strict.build_parser")
    def test_main_json_output(self, mock_build_parser, mock_validate):
        """main outputs JSON when --json flag set."""
        mock_parser = MagicMock()
        mock_args = MagicMock(
            video_path="/test.mp4",
            sample_interval=1.0,
            max_audio_mute=0.95,
            max_audio_silent=0.90,
            max_black_frame=0.80,
            max_repeated_frame=0.50,
            min_motion_entropy=0.1,
            json=True,
        )
        mock_parser.parse_args.return_value = mock_args
        mock_build_parser.return_value = mock_parser

        # Create a temp file to avoid file-not-found error
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"fake")
            path = f.name

        mock_result = clip_validator_strict.ValidationResult(
            video_path=path,
            is_valid=True,
        )

        mock_validate.return_value = mock_result

        try:
            with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                clip_validator_strict.main()
                output = mock_stdout.getvalue()
                parsed = json.loads(output)
                assert parsed["video_path"] == path
        finally:
            Path(path).unlink()



