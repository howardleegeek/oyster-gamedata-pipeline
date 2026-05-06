"""Unit tests for R16 — depth frame count vs video duration consistency.

Spec: docs/SPEC_R13_MULTIMODAL.md § R16. Covers PASS, ±2 boundary, FAIL,
and the two IL10 ABSTAIN gates (missing depth_dir, missing duration).
"""
from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from bin.v1_claude_residuals import ResidualResult, r16_depth_count


def _make_exr_files(dir_path: Path, count: int) -> None:
    """Create ``count`` empty ``depth_<idx>.exr`` files for fixture setup."""
    for i in range(count):
        (dir_path / f"depth_{i:05d}.exr").touch()


class TestR16DepthCount(unittest.TestCase):
    """Five fixtures cover PASS / boundary / FAIL / two ABSTAIN reasons."""

    def test_exact_match_passes(self) -> None:
        """60 .exr at 10 s × 6 fps → diff 0, PASS."""
        with tempfile.TemporaryDirectory() as tmp:
            _make_exr_files(Path(tmp), 60)
            result = r16_depth_count({}, depth_dir=tmp, video_duration_sec=10.0)
        self.assertIsInstance(result, ResidualResult)
        self.assertEqual(result.name, "R16")
        self.assertTrue(result.passed)
        self.assertEqual(result.residual, 0.0)

    def test_within_tolerance_passes(self) -> None:
        """58 .exr at 10 s (expected 60) → diff 2 == threshold, PASS."""
        with tempfile.TemporaryDirectory() as tmp:
            _make_exr_files(Path(tmp), 58)
            result = r16_depth_count({}, depth_dir=tmp, video_duration_sec=10.0)
        self.assertTrue(result.passed)
        self.assertEqual(result.residual, 2.0)

    def test_dropout_fails(self) -> None:
        """30 .exr at 10 s (expected 60) → diff 30, FAIL (every-other drop)."""
        with tempfile.TemporaryDirectory() as tmp:
            _make_exr_files(Path(tmp), 30)
            result = r16_depth_count({}, depth_dir=tmp, video_duration_sec=10.0)
        self.assertFalse(result.passed)
        self.assertEqual(result.residual, 30.0)
        self.assertIn("expected=60", result.note)
        self.assertIn("actual=30", result.note)

    def test_missing_depth_dir_abstains(self) -> None:
        """depth_dir=None → ABSTAIN (note prefix marks vote-skip)."""
        result = r16_depth_count({}, depth_dir=None, video_duration_sec=10.0)
        self.assertFalse(result.passed)
        self.assertEqual(result.residual, math.inf)
        self.assertTrue(result.note.startswith("ABSTAIN:"))
        self.assertIn("no_depth_dir", result.note)

    def test_zero_duration_abstains(self) -> None:
        """duration ≤ 0 sentinel for missing ffprobe → ABSTAIN, not FAIL."""
        with tempfile.TemporaryDirectory() as tmp:
            # 0 .exr files + 0 duration: must abstain on duration, not silently pass.
            result = r16_depth_count({}, depth_dir=tmp, video_duration_sec=0.0)
        self.assertFalse(result.passed)
        self.assertTrue(result.note.startswith("ABSTAIN:"))
        self.assertIn("no_video_duration", result.note)


if __name__ == "__main__":
    unittest.main()
