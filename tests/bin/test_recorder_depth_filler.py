#!/usr/bin/env python3
"""Tests for bin/recorder_depth_filler.py (spec G261)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ),
)

from bin.recorder_depth_filler import (  # noqa: E402
    DEFAULT_FPS,
    EXPECTED_FRAME_COUNT,
    fill_clip_depth,
    find_clip_video,
    main,
    run_depth_inference,
)


def test_constants_match_prd() -> None:
    """6 fps × 300 s = 1800 expected frames per PRD buyer-grade spec."""
    assert DEFAULT_FPS == 6
    assert EXPECTED_FRAME_COUNT == 1800


def test_find_clip_video_canonical(tmp_path: Path) -> None:
    canonical = tmp_path / "video.mp4"
    canonical.write_bytes(b"x")
    assert find_clip_video(tmp_path) == canonical.resolve()


def test_find_clip_video_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        find_clip_video(tmp_path)


def test_run_depth_inference_delegates(tmp_path: Path) -> None:
    """run_depth_inference forwards args to bin.real_depth_filler.infer_batch."""
    rgb = tmp_path / "rgb"
    rgb.mkdir()
    out = tmp_path / "depth"
    with mock.patch("bin.real_depth_filler.infer_batch", return_value=42) as ib:
        n = run_depth_inference(rgb, out, batch_size=8)
    assert n == 42
    ib.assert_called_once()
    assert out.is_dir()
    kwargs = ib.call_args.kwargs
    assert kwargs["batch_size"] == 8
    assert kwargs["rgb_dir"] == str(rgb)
    assert kwargs["out_dir"] == str(out)


def test_fill_clip_depth_invalid_dir() -> None:
    with pytest.raises(NotADirectoryError):
        fill_clip_depth(Path("/nonexistent_xyz_for_test"))


def test_fill_clip_depth_pipeline(tmp_path: Path) -> None:
    """Smoke test: stub ffmpeg + DA-V2 and verify depth dir is returned."""
    video = tmp_path / "video.mp4"
    video.write_bytes(b"\x00" * 64)

    def _fake_extract(video_path, rgb_dir, fps=6, ffmpeg_bin="ffmpeg"):
        Path(rgb_dir).mkdir(parents=True, exist_ok=True)
        for i in range(3):
            (Path(rgb_dir) / f"frame_{i:06d}.png").write_bytes(b"P")
        return 3

    def _fake_inference(rgb_dir, depth_dir, *args, **kwargs):
        Path(depth_dir).mkdir(parents=True, exist_ok=True)
        return 3

    with mock.patch(
        "bin.recorder_depth_filler.extract_frames", side_effect=_fake_extract
    ), mock.patch(
        "bin.recorder_depth_filler.run_depth_inference", side_effect=_fake_inference
    ):
        out = fill_clip_depth(tmp_path, expected_frames=3)

    assert out == tmp_path / "depth"
    assert out.is_dir()


def test_main_missing_clip_returns_nonzero(tmp_path: Path) -> None:
    rc = main(["--clip-dir", str(tmp_path / "missing")])
    assert rc == 2
