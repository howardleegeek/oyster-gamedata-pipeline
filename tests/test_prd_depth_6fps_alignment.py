#!/usr/bin/env python3
"""Tests for bin/prd_test_depth_6fps_alignment.py"""

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "bin" / "prd_test_depth_6fps_alignment.py"


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        capture_output=True,
        text=True,
        timeout=10,
    )


def _make_frame_dirs(video_indices: list[int], depth_indices: list[int]) -> tuple[Path, Path]:
    """Create temp directories with video and depth frame files."""
    import tempfile as tf
    video_dir = Path(tf.mkdtemp(prefix="video_frames_"))
    depth_dir = Path(tf.mkdtemp(prefix="depth_frames_"))
    for idx in video_indices:
        (video_dir / f"frame_{idx:06d}.jpg").touch()
    for idx in depth_indices:
        (depth_dir / f"depth_{idx:06d}.exr").touch()
    return video_dir, depth_dir


# ---------------------------------------------------------------------------
# Unit tests via import
# ---------------------------------------------------------------------------

class TestExtractIndex:
    """Tests for extract_index function."""

    def _extract(self, name: str) -> int:
        import importlib.util
        spec = importlib.util.spec_from_file_location("depth", str(SCRIPT))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.extract_index(Path(name))

    def test_simple_index(self):
        """Extract index from simple filename."""
        assert self._extract("frame_000001") == 1

    def test_large_index(self):
        """Extract large index."""
        assert self._extract("frame_000120") == 120

    def test_no_index_raises(self):
        """Filename with no trailing number should raise."""
        with pytest.raises(ValueError):
            self._extract("frame_abc")


class TestNaturalSortKey:
    """Tests for natural_sort_key function."""

    def _sort_key(self, name: str):
        import importlib.util
        spec = importlib.util.spec_from_file_location("depth", str(SCRIPT))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.natural_sort_key(Path(name))

    def test_numeric_sorting(self):
        """Numeric parts should sort numerically, not lexicographically."""
        key1 = self._sort_key("frame_000002")
        key2 = self._sort_key("frame_000010")
        assert key1 < key2


class TestValidateAlignment:
    """Tests for validate_alignment function."""

    def _validate(self, video_dir: Path, depth_dir: Path, verbose=False):
        import importlib.util
        spec = importlib.util.spec_from_file_location("depth", str(SCRIPT))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.validate_alignment(video_dir, depth_dir, verbose)

    def test_perfect_5_to_1_alignment(self):
        """Depth frames at 0,1,2 should map to video frames at 0,5,10."""
        video_dir, depth_dir = _make_frame_dirs(
            video_indices=[0, 5, 10, 15, 20],
            depth_indices=[0, 1, 2, 3, 4],
        )
        try:
            ok, msg = self._validate(video_dir, depth_dir)
            assert ok is True
        finally:
            import shutil
            shutil.rmtree(video_dir)
            shutil.rmtree(depth_dir)

    def test_missing_video_frame_fails(self):
        """Missing video frame at expected position should fail."""
        video_dir, depth_dir = _make_frame_dirs(
            video_indices=[0, 10],  # missing 5
            depth_indices=[0, 1, 2],
        )
        try:
            ok, msg = self._validate(video_dir, depth_dir)
            assert ok is False
            assert "Missing" in msg
        finally:
            import shutil
            shutil.rmtree(video_dir)
            shutil.rmtree(depth_dir)

    def test_no_video_frames_fails(self):
        """Empty video directory should fail."""
        import tempfile as tf
        video_dir = Path(tf.mkdtemp(prefix="empty_video_"))
        depth_dir = Path(tf.mkdtemp(prefix="depth_"))
        (depth_dir / "depth_000000.exr").touch()
        try:
            ok, msg = self._validate(video_dir, depth_dir)
            assert ok is False
            assert "No video frames" in msg
        finally:
            import shutil
            shutil.rmtree(video_dir)
            shutil.rmtree(depth_dir)

    def test_no_depth_frames_fails(self):
        """Empty depth directory should fail."""
        import tempfile as tf
        video_dir = Path(tf.mkdtemp(prefix="video_"))
        depth_dir = Path(tf.mkdtemp(prefix="empty_depth_"))
        (video_dir / "frame_000000.jpg").touch()
        try:
            ok, msg = self._validate(video_dir, depth_dir)
            assert ok is False
            assert "No depth" in msg
        finally:
            import shutil
            shutil.rmtree(video_dir)
            shutil.rmtree(depth_dir)

    def test_extra_video_frames_ok(self):
        """Extra video frames beyond depth alignment should be OK."""
        video_dir, depth_dir = _make_frame_dirs(
            video_indices=[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
            depth_indices=[0, 1, 2],
        )
        try:
            ok, msg = self._validate(video_dir, depth_dir)
            assert ok is True
        finally:
            import shutil
            shutil.rmtree(video_dir)
            shutil.rmtree(depth_dir)

    def test_png_video_frames(self):
        """PNG video frames should be detected."""
        video_dir, depth_dir = _make_frame_dirs(
            video_indices=[0, 5, 10],
            depth_indices=[0, 1, 2],
        )
        # Rename jpg to png
        for f in video_dir.glob("*.jpg"):
            f.rename(f.with_suffix(".png"))
        try:
            ok, msg = self._validate(video_dir, depth_dir)
            assert ok is True
        finally:
            import shutil
            shutil.rmtree(video_dir)
            shutil.rmtree(depth_dir)


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------

class TestDepth6fpsAlignmentCLI:
    """Integration tests for the CLI entry point."""

    def test_help(self):
        """--help should show usage."""
        result = _run(["--help"])
        assert result.returncode == 0
        assert "usage" in result.stdout.lower() or "Usage" in result.stdout

    def test_missing_args_errors(self):
        """Missing required args should error."""
        result = _run([])
        assert result.returncode == 2

    def test_valid_alignment_passes(self):
        """Properly aligned frames should pass."""
        video_dir, depth_dir = _make_frame_dirs(
            video_indices=[0, 5, 10, 15, 20],
            depth_indices=[0, 1, 2, 3, 4],
        )
        try:
            result = _run(["-v", str(video_dir), "-d", str(depth_dir)])
            assert result.returncode == 0
            assert "Valid" in result.stdout
        finally:
            import shutil
            shutil.rmtree(video_dir)
            shutil.rmtree(depth_dir)

    def test_misaligned_fails(self):
        """Misaligned frames should fail."""
        video_dir, depth_dir = _make_frame_dirs(
            video_indices=[0, 10],  # missing frame 5
            depth_indices=[0, 1, 2],
        )
        try:
            result = _run(["-v", str(video_dir), "-d", str(depth_dir)])
            assert result.returncode == 1
        finally:
            import shutil
            shutil.rmtree(video_dir)
            shutil.rmtree(depth_dir)

    def test_verbose_output(self):
        """--verbose should show frame counts."""
        video_dir, depth_dir = _make_frame_dirs(
            video_indices=[0, 5, 10],
            depth_indices=[0, 1, 2],
        )
        try:
            result = _run(["-v", str(video_dir), "-d", str(depth_dir), "--verbose"])
            assert result.returncode == 0
            assert "Video:" in result.stdout
            assert "Depth:" in result.stdout
        finally:
            import shutil
            shutil.rmtree(video_dir)
            shutil.rmtree(depth_dir)
