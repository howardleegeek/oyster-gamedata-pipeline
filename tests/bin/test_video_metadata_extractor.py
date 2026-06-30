#!/usr/bin/env python3
"""Tests for bin/video_metadata_extractor.py"""

from __future__ import annotations

import json
import subprocess

# Import the module under test
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from bin.video_metadata_extractor import extract_metadata, main


class TestExtractMetadata:
    """Test extract_metadata() function."""

    def test_extract_metadata_success(self):
        """Test successful metadata extraction with mock ffprobe output."""
        mock_output = {
            "streams": [
                {
                    "codec_type": "video",
                    "duration": "120.5",
                    "width": 1920,
                    "height": 1080,
                    "r_frame_rate": "30/1",
                    "codec_name": "h264",
                }
            ],
            "format": {"duration": "120.5"},
        }

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = json.dumps(mock_output)
            mock_run.return_value.returncode = 0

            result = extract_metadata("test.mp4")

        assert result["duration"] == 120.5
        assert result["width"] == 1920
        assert result["height"] == 1080
        assert result["fps"] == 30.0
        assert result["codec"] == "h264"

    def test_extract_metadata_uses_format_duration_as_fallback(self):
        """Test fallback to format-level duration when stream duration is 0."""
        mock_output = {
            "streams": [
                {
                    "codec_type": "video",
                    "duration": "0",
                    "width": 1920,
                    "height": 1080,
                    "r_frame_rate": "60/1",
                    "codec_name": "h264",
                }
            ],
            "format": {"duration": "240.0"},
        }

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = json.dumps(mock_output)
            mock_run.return_value.returncode = 0

            result = extract_metadata("test.mp4")

        assert result["duration"] == 240.0

    def test_extract_metadata_fractional_fps(self):
        """Test FPS calculation with fractional values like 29.97/1."""
        mock_output = {
            "streams": [
                {
                    "codec_type": "video",
                    "duration": "60.0",
                    "width": 1280,
                    "height": 720,
                    "r_frame_rate": "30000/1001",
                    "codec_name": "h264",
                }
            ],
            "format": {},
        }

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = json.dumps(mock_output)
            mock_run.return_value.returncode = 0

            result = extract_metadata("test.mp4")

        # 30000/1001 ≈ 29.97
        assert result["fps"] == 29.97

    def test_extract_metadata_no_video_stream_raises(self):
        """Test ValueError when no video stream is found."""
        mock_output = {
            "streams": [{"codec_type": "audio", "codec_name": "aac"}],
            "format": {},
        }

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = json.dumps(mock_output)
            mock_run.return_value.returncode = 0

            with pytest.raises(ValueError, match="No video stream found"):
                extract_metadata("test.mp4")

    def test_extract_metadata_empty_streams_raises(self):
        """Test ValueError when streams list is empty."""
        mock_output = {"streams": [], "format": {}}

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = json.dumps(mock_output)
            mock_run.return_value.returncode = 0

            with pytest.raises(ValueError, match="No video stream found"):
                extract_metadata("test.mp4")

    def test_extract_metadata_subprocess_error(self):
        """Test CalledProcessError handling."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                1, "ffprobe", stderr="file not found"
            )

            with pytest.raises(subprocess.CalledProcessError):
                extract_metadata("nonexistent.mp4")

    def test_extract_metadata_file_not_found(self):
        """Test FileNotFoundError when ffprobe is not available."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("ffprobe not found")

            with pytest.raises(FileNotFoundError):
                extract_metadata("test.mp4")


class TestMain:
    """Test main() CLI function."""

    def test_main_success(self, capsys):
        """Test successful CLI execution with mock."""
        mock_output = {
            "streams": [
                {
                    "codec_type": "video",
                    "duration": "60.0",
                    "width": 1920,
                    "height": 1080,
                    "r_frame_rate": "30/1",
                    "codec_name": "h264",
                }
            ],
            "format": {},
        }

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = json.dumps(mock_output)
            mock_run.return_value.returncode = 0

            with patch("sys.argv", ["video_metadata_extractor.py", "--video", "test.mp4"]):
                main()

        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["duration"] == 60.0
        assert result["width"] == 1920
        assert result["height"] == 1080

    def test_main_ffprobe_error(self, capsys):
        """Test CLI handles ffprobe errors."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                1, "ffprobe", stderr="error message"
            )

            with patch("sys.argv", ["video_metadata_extractor.py", "--video", "bad.mp4"]):
                with pytest.raises(SystemExit) as exc_info:
                    main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "ffprobe error" in captured.err

    def test_main_value_error(self, capsys):
        """Test CLI handles ValueError (no video stream)."""
        mock_output = {"streams": [], "format": {}}

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = json.dumps(mock_output)
            mock_run.return_value.returncode = 0

            with patch("sys.argv", ["video_metadata_extractor.py", "--video", "no_video.mp4"]):
                with pytest.raises(SystemExit) as exc_info:
                    main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Error:" in captured.err

    def test_main_file_not_found(self, capsys):
        """Test CLI handles FileNotFoundError."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("ffprobe not found")

            with patch("sys.argv", ["video_metadata_extractor.py", "--video", "test.mp4"]):
                with pytest.raises(SystemExit) as exc_info:
                    main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Error:" in captured.err
