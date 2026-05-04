#!/usr/bin/env python3
"""Tests for bin/video_metadata_extractor.py."""

import json
import os
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

from video_metadata_extractor import extract_metadata


class TestExtractMetadata(unittest.TestCase):
    """Unit tests using mocked ffprobe output."""

    def _mock_ffprobe(self, streams, format_info=None):
        """Helper to build a mock ffprobe JSON response."""
        data = {"streams": streams}
        if format_info:
            data["format"] = format_info
        return json.dumps(data)

    @patch("video_metadata_extractor.subprocess.run")
    def test_basic_video(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout=self._mock_ffprobe(
                streams=[{
                    "codec_type": "video",
                    "width": 1920,
                    "height": 1080,
                    "r_frame_rate": "30/1",
                    "codec_name": "h264",
                    "duration": "10.5",
                }],
                format_info={"duration": "10.5"},
            )
        )
        result = extract_metadata("dummy.mp4")
        self.assertEqual(result["width"], 1920)
        self.assertEqual(result["height"], 1080)
        self.assertEqual(result["fps"], 30.0)
        self.assertEqual(result["codec"], "h264")
        self.assertEqual(result["duration"], 10.5)

    @patch("video_metadata_extractor.subprocess.run")
    def test_fractional_fps(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout=self._mock_ffprobe(
                streams=[{
                    "codec_type": "video",
                    "width": 1280,
                    "height": 720,
                    "r_frame_rate": "24000/1001",
                    "codec_name": "vp9",
                    "duration": "5.0",
                }]
            )
        )
        result = extract_metadata("dummy.webm")
        self.assertAlmostEqual(result["fps"], 23.98, places=2)
        self.assertEqual(result["codec"], "vp9")

    @patch("video_metadata_extractor.subprocess.run")
    def test_duration_fallback_to_format(self, mock_run):
        """When stream has no duration, fall back to format duration."""
        mock_run.return_value = MagicMock(
            stdout=self._mock_ffprobe(
                streams=[{
                    "codec_type": "video",
                    "width": 640,
                    "height": 480,
                    "r_frame_rate": "25/1",
                    "codec_name": "mpeg4",
                }],
                format_info={"duration": "30.0"},
            )
        )
        result = extract_metadata("dummy.avi")
        self.assertEqual(result["duration"], 30.0)

    @patch("video_metadata_extractor.subprocess.run")
    def test_no_video_stream_raises(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout=self._mock_ffprobe(
                streams=[{"codec_type": "audio"}]
            )
        )
        with self.assertRaises(ValueError):
            extract_metadata("audio_only.mp3")


class TestIntegration(unittest.TestCase):
    """Integration test with a real video file."""

    @classmethod
    def setUpClass(cls):
        """Create a small test video."""
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", "color=c=red:s=320x240:r=25:d=1",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "/tmp/test_meta.mp4",
            ],
            capture_output=True,
        )

    def test_real_video(self):
        result = extract_metadata("/tmp/test_meta.mp4")
        self.assertEqual(result["width"], 320)
        self.assertEqual(result["height"], 240)
        self.assertEqual(result["fps"], 25.0)
        self.assertEqual(result["codec"], "h264")
        self.assertGreater(result["duration"], 0)


if __name__ == "__main__":
    unittest.main()
