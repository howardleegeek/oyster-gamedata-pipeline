"""Unit tests for R23 video codec/format residual.

Mocks ``subprocess.run`` and ``shutil.which`` so the tests run on any
host without ffprobe installed and without touching real video files.
"""

from __future__ import annotations

import json
import math
import os
import unittest
from unittest.mock import patch

from bin.v1_claude_residuals.r23_video_codec import r23_video_codec


def _ffprobe_stdout(codec: str, width: int, height: int) -> str:
    return json.dumps(
        {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": codec,
                    "width": width,
                    "height": height,
                }
            ],
        }
    )


class _FakeCompleted:
    def __init__(self, stdout: str, returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


class TestR23VideoCodec(unittest.TestCase):
    def setUp(self) -> None:
        self.video_path = "/tmp/_r23_fake_video.mp4"
        with open(self.video_path, "wb") as f:
            f.write(b"\x00")

    def tearDown(self) -> None:
        from contextlib import suppress
        with suppress(FileNotFoundError):
            os.remove(self.video_path)

    def test_pass_when_hevc_1920x1080(self) -> None:
        with (
            patch(
                "bin.v1_claude_residuals.r23_video_codec.shutil.which",
                return_value="/usr/bin/ffprobe",
            ),
            patch(
                "bin.v1_claude_residuals.r23_video_codec.subprocess.run",
                return_value=_FakeCompleted(_ffprobe_stdout("hevc", 1920, 1080)),
            ),
        ):
            res = r23_video_codec({}, video_path=self.video_path)
        self.assertTrue(res.passed, msg=res.note)
        self.assertEqual(res.residual, 0.0)
        self.assertEqual(res.name, "R23")

    def test_fail_when_h264_codec(self) -> None:
        with (
            patch(
                "bin.v1_claude_residuals.r23_video_codec.shutil.which",
                return_value="/usr/bin/ffprobe",
            ),
            patch(
                "bin.v1_claude_residuals.r23_video_codec.subprocess.run",
                return_value=_FakeCompleted(_ffprobe_stdout("h264", 1920, 1080)),
            ),
        ):
            res = r23_video_codec({}, video_path=self.video_path)
        self.assertFalse(res.passed)
        self.assertEqual(res.residual, 1.0)
        self.assertIn("h264", res.note)
        self.assertIn("hevc", res.note)

    def test_fail_when_wrong_resolution(self) -> None:
        with (
            patch(
                "bin.v1_claude_residuals.r23_video_codec.shutil.which",
                return_value="/usr/bin/ffprobe",
            ),
            patch(
                "bin.v1_claude_residuals.r23_video_codec.subprocess.run",
                return_value=_FakeCompleted(_ffprobe_stdout("hevc", 1280, 720)),
            ),
        ):
            res = r23_video_codec({}, video_path=self.video_path)
        self.assertFalse(res.passed)
        self.assertEqual(res.residual, 2.0)
        self.assertIn("width=1280", res.note)
        self.assertIn("height=720", res.note)

    def test_abstain_when_video_path_none(self) -> None:
        res = r23_video_codec({}, video_path=None)
        self.assertFalse(res.passed)
        self.assertTrue(math.isnan(res.residual))
        self.assertTrue(res.note.startswith("ABSTAIN:"), msg=res.note)
        self.assertIn("no_video_file", res.note)

    def test_abstain_when_ffprobe_missing(self) -> None:
        with patch("bin.v1_claude_residuals.r23_video_codec.shutil.which", return_value=None):
            res = r23_video_codec({}, video_path=self.video_path)
        self.assertFalse(res.passed)
        self.assertTrue(math.isnan(res.residual))
        self.assertIn("ffprobe_unavailable", res.note)


if __name__ == "__main__":
    unittest.main()
