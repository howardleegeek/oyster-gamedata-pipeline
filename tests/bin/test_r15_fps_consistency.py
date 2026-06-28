"""Unit tests for R15 fps-consistency residual.

Mocks ``subprocess.run`` and ``shutil.which`` so the tests run on any
host without ffprobe installed and without touching real video files.
"""

from __future__ import annotations

import json
import math
import os
import unittest
from unittest.mock import patch

from bin.v1_claude_residuals.r15_fps_consistency import r15_fps_consistency


def _ffprobe_stdout(rate: str) -> str:
    return json.dumps({"streams": [{"avg_frame_rate": rate}]})


class _FakeCompleted:
    def __init__(self, stdout: str, returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


class TestR15FpsConsistency(unittest.TestCase):
    def setUp(self) -> None:
        self.video_path = "/tmp/_r15_fake_video.mp4"
        # touch a real file so os.path.isfile passes
        with open(self.video_path, "wb") as f:
            f.write(b"\x00")

    def tearDown(self) -> None:
        from contextlib import suppress
        with suppress(FileNotFoundError):
            os.remove(self.video_path)

    def test_pass_when_30_over_1_matches_declared_30(self) -> None:
        rec = {"fps": 30}
        with (
            patch(
                "bin.v1_claude_residuals.r15_fps_consistency.shutil.which",
                return_value="/usr/bin/ffprobe",
            ),
            patch(
                "bin.v1_claude_residuals.r15_fps_consistency.subprocess.run",
                return_value=_FakeCompleted(_ffprobe_stdout("30/1")),
            ),
        ):
            res = r15_fps_consistency(rec, self.video_path)
        self.assertTrue(res.passed, msg=res.note)
        self.assertAlmostEqual(res.residual, 0.0, places=6)
        self.assertEqual(res.name, "R15")

    def test_pass_ntsc_2997_tolerance_against_declared_30(self) -> None:
        rec = {"fps": 30}
        with (
            patch(
                "bin.v1_claude_residuals.r15_fps_consistency.shutil.which",
                return_value="/usr/bin/ffprobe",
            ),
            patch(
                "bin.v1_claude_residuals.r15_fps_consistency.subprocess.run",
                return_value=_FakeCompleted(_ffprobe_stdout("30000/1001")),
            ),
        ):
            res = r15_fps_consistency(rec, self.video_path)
        self.assertTrue(res.passed, msg=res.note)
        # 30 - 29.970029... ≈ 0.030
        self.assertLess(res.residual, 0.05)

    def test_fail_when_25_against_declared_30(self) -> None:
        rec = {"fps": 30}
        with (
            patch(
                "bin.v1_claude_residuals.r15_fps_consistency.shutil.which",
                return_value="/usr/bin/ffprobe",
            ),
            patch(
                "bin.v1_claude_residuals.r15_fps_consistency.subprocess.run",
                return_value=_FakeCompleted(_ffprobe_stdout("25/1")),
            ),
        ):
            res = r15_fps_consistency(rec, self.video_path)
        self.assertFalse(res.passed)
        self.assertAlmostEqual(res.residual, 5.0, places=6)
        self.assertIn("declared=30.000", res.note)
        self.assertIn("probed=25.000", res.note)

    def test_abstain_when_video_path_none(self) -> None:
        rec = {"fps": 30}
        res = r15_fps_consistency(rec, None)
        self.assertFalse(res.passed)
        self.assertTrue(math.isnan(res.residual))
        self.assertTrue(res.note.startswith("ABSTAIN:"), msg=res.note)
        self.assertIn("no_video_file", res.note)


if __name__ == "__main__":
    unittest.main()
