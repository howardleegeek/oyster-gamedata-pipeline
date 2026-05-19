#!/usr/bin/env python3
"""Tests for bin/recorder_audio_postprocess.py (spec G260)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
)

from bin.recorder_audio_postprocess import (  # noqa: E402
    find_clip_video,
    main,
    postprocess_clip,
    run_event_classifier,
)


def test_find_clip_video_canonical(tmp_path: Path) -> None:
    """Returns video.mp4 when present."""
    canonical = tmp_path / "video.mp4"
    canonical.write_bytes(b"fake")
    other = tmp_path / "extra.mp4"
    other.write_bytes(b"fake")
    assert find_clip_video(tmp_path) == canonical.resolve()


def test_find_clip_video_fallback(tmp_path: Path) -> None:
    """Falls back to single .mp4 when canonical missing."""
    fallback = tmp_path / "clip-20260505.mp4"
    fallback.write_bytes(b"fake")
    assert find_clip_video(tmp_path) == fallback.resolve()


def test_find_clip_video_missing(tmp_path: Path) -> None:
    """Raises FileNotFoundError when no .mp4 exists."""
    with pytest.raises(FileNotFoundError):
        find_clip_video(tmp_path)


def test_postprocess_clip_invalid_dir() -> None:
    """Raises NotADirectoryError for non-existent clip dir."""
    with pytest.raises(NotADirectoryError):
        postprocess_clip(Path("/nonexistent_dir_for_test_xyz"))


def test_postprocess_clip_pipeline(tmp_path: Path) -> None:
    """Smoke test: stub ffmpeg + classifier and verify JSON written."""
    video = tmp_path / "video.mp4"
    video.write_bytes(b"\x00" * 1024)

    fake_events = {"frames": [{"t": 0.0, "peak": 0.1, "label": "silence"}]}

    def _fake_ffmpeg(video_path, out_wav, sample_rate=16000, ffmpeg_bin="ffmpeg"):
        Path(out_wav).write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")
        return Path(out_wav)

    with (
        mock.patch(
            "bin.recorder_audio_postprocess.extract_audio_track",
            side_effect=_fake_ffmpeg,
        ),
        mock.patch(
            "bin.recorder_audio_postprocess.run_event_classifier",
            return_value=fake_events,
        ),
    ):
        out = postprocess_clip(tmp_path, frame_ms=50)

    assert out.name == "audio_events.json"
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["source_video"] == "video.mp4"
    assert payload["frame_ms"] == 50
    assert payload["events"] == fake_events


def test_main_missing_clip_returns_nonzero(tmp_path: Path) -> None:
    """CLI returns non-zero on bad clip dir."""
    rc = main(["--clip-dir", str(tmp_path / "does_not_exist")])
    assert rc == 2


def test_run_event_classifier_calls_process_audio(tmp_path: Path) -> None:
    """run_event_classifier delegates to bin.audio_event_track.process_audio."""
    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"x")
    sentinel = {"frames": []}
    with mock.patch("bin.audio_event_track.process_audio", return_value=sentinel) as patched:
        out = run_event_classifier(wav, frame_ms=25)
    assert out is sentinel
    patched.assert_called_once()
