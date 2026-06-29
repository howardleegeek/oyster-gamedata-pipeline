#!/usr/bin/env python3
"""
Tests for bin/prd_test_depth_6fps_alignment.py

PRD p4 #5: Validate depth EXR 6fps alignment with 30fps video (5:1 ratio).
"""

import tempfile
from pathlib import Path

import pytest

from bin.prd_test_depth_6fps_alignment import (
    extract_index,
    find_frames,
    natural_sort_key,
    validate_alignment,
)


class TestNaturalSortKey:
    """Tests for natural_sort_key function."""

    def test_simple_numbers(self):
        """Test sorting with simple numeric filenames."""
        paths = [
            Path("frame10.png"),
            Path("frame2.png"),
            Path("frame1.png"),
        ]
        sorted_paths = sorted(paths, key=natural_sort_key)
        assert [p.name for p in sorted_paths] == ["frame1.png", "frame2.png", "frame10.png"]

    def test_mixed_alphanumeric(self):
        """Test sorting with mixed alphanumeric filenames."""
        paths = [
            Path("scene_10_frame.png"),
            Path("scene_2_frame.png"),
            Path("scene_1_frame.png"),
        ]
        sorted_paths = sorted(paths, key=natural_sort_key)
        assert [p.name for p in sorted_paths] == [
            "scene_1_frame.png",
            "scene_2_frame.png",
            "scene_10_frame.png",
        ]

    def test_no_numbers(self):
        """Test sorting with no numbers in filenames."""
        paths = [Path("abc.png"), Path("xyz.png")]
        sorted_paths = sorted(paths, key=natural_sort_key)
        assert [p.name for p in sorted_paths] == ["abc.png", "xyz.png"]


class TestExtractIndex:
    """Tests for extract_index function."""

    def test_simple_index(self):
        """Test extracting index from simple filename."""
        assert extract_index(Path("frame000.png")) == 0
        assert extract_index(Path("frame001.png")) == 1
        assert extract_index(Path("frame099.png")) == 99
        assert extract_index(Path("frame100.png")) == 100

    def test_no_trailing_index(self):
        """Test ValueError when no trailing index."""
        with pytest.raises(ValueError, match="Cannot extract index"):
            extract_index(Path("frame.png"))

    def test_index_at_end(self):
        """Test index at end of filename with prefix."""
        assert extract_index(Path("video_000123.jpg")) == 123
        assert extract_index(Path("depth_000050.exr")) == 50


class TestFindFrames:
    """Tests for find_frames function."""

    def test_find_video_frames(self):
        """Test finding video frame files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            (tmp / "frame000.jpg").touch()
            (tmp / "frame001.png").touch()
            (tmp / "frame002.bmp").touch()
            (tmp / "notafile.txt").touch()

            frames = find_frames(tmp, [".jpg", ".png", ".bmp"])
            assert len(frames) == 3
            names = [f.name for f in frames]
            assert "frame000.jpg" in names
            assert "frame001.png" in names
            assert "frame002.bmp" in names

    def test_find_depth_exr(self):
        """Test finding depth EXR files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            (tmp / "depth000.exr").touch()
            (tmp / "depth001.exr").touch()

            frames = find_frames(tmp, [".exr"])
            assert len(frames) == 2

    def test_not_a_directory(self):
        """Test NotADirectoryError when path is not a directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir) / "file.txt"
            tmp.touch()
            with pytest.raises(NotADirectoryError):
                find_frames(tmp, [".jpg"])


class TestValidateAlignment:
    """Tests for validate_alignment function."""

    def test_valid_alignment(self):
        """Test valid 5:1 ratio alignment."""
        with tempfile.TemporaryDirectory() as tmpdir:
            video_dir = Path(tmpdir) / "video"
            depth_dir = Path(tmpdir) / "depth"
            video_dir.mkdir()
            depth_dir.mkdir()

            # Video at 0, 5, 10, 15 (30fps)
            for i in range(0, 20, 5):
                (video_dir / f"frame{i:03d}.jpg").touch()

            # Depth at 0, 1, 2, 3 (6fps)
            for i in range(4):
                (depth_dir / f"depth{i:03d}.exr").touch()

            ok, msg = validate_alignment(video_dir, depth_dir)
            assert ok is True
            assert "Valid:" in msg

    def test_missing_alignment(self):
        """Test detection of missing alignment."""
        with tempfile.TemporaryDirectory() as tmpdir:
            video_dir = Path(tmpdir) / "video"
            depth_dir = Path(tmpdir) / "depth"
            video_dir.mkdir()
            depth_dir.mkdir()

            # Video: only frames 0, 10 (missing 5, 15)
            (video_dir / "frame000.jpg").touch()
            (video_dir / "frame010.jpg").touch()

            # Depth: frame 1 requires video frame 5 (missing)
            (depth_dir / "depth001.exr").touch()

            ok, msg = validate_alignment(video_dir, depth_dir)
            assert ok is False
            assert "Missing depth alignment" in msg

    def test_no_video_frames(self):
        """Test error when no video frames found."""
        with tempfile.TemporaryDirectory() as tmpdir:
            video_dir = Path(tmpdir) / "video"
            depth_dir = Path(tmpdir) / "depth"
            video_dir.mkdir()
            depth_dir.mkdir()
            (depth_dir / "depth000.exr").touch()

            ok, msg = validate_alignment(video_dir, depth_dir)
            assert ok is False
            assert "No video frames" in msg

    def test_no_depth_frames(self):
        """Test error when no depth frames found."""
        with tempfile.TemporaryDirectory() as tmpdir:
            video_dir = Path(tmpdir) / "video"
            depth_dir = Path(tmpdir) / "depth"
            video_dir.mkdir()
            depth_dir.mkdir()
            (video_dir / "frame000.jpg").touch()

            ok, msg = validate_alignment(video_dir, depth_dir)
            assert ok is False
            assert "No depth EXR frames" in msg

    def test_verbose_output(self, capsys):
        """Test verbose output includes frame counts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            video_dir = Path(tmpdir) / "video"
            depth_dir = Path(tmpdir) / "depth"
            video_dir.mkdir()
            depth_dir.mkdir()

            for i in range(0, 15, 5):
                (video_dir / f"frame{i:03d}.jpg").touch()
            for i in range(3):
                (depth_dir / f"depth{i:03d}.exr").touch()

            ok, msg = validate_alignment(video_dir, depth_dir, verbose=True)
            captured = capsys.readouterr()
            assert "Video:" in captured.out
            assert "Depth:" in captured.out
            assert ok is True

    def test_empty_video_dir(self):
        """Test error with empty video directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            video_dir = Path(tmpdir) / "video"
            depth_dir = Path(tmpdir) / "depth"
            video_dir.mkdir()
            depth_dir.mkdir()

            ok, msg = validate_alignment(video_dir, depth_dir)
            assert ok is False

    def test_single_frame_alignment(self):
        """Test alignment with single frame pair."""
        with tempfile.TemporaryDirectory() as tmpdir:
            video_dir = Path(tmpdir) / "video"
            depth_dir = Path(tmpdir) / "depth"
            video_dir.mkdir()
            depth_dir.mkdir()

            # Video frame 0 aligns with depth frame 0
            (video_dir / "frame000.jpg").touch()
            (depth_dir / "depth000.exr").touch()

            ok, msg = validate_alignment(video_dir, depth_dir)
            assert ok is True
