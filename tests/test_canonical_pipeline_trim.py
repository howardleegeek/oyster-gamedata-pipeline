from __future__ import annotations

import json
import pathlib

import pytest

from bin import canonical_pipeline


def _make_session(tmp_path: pathlib.Path) -> pathlib.Path:
    sess = tmp_path / "session"
    sess.mkdir()
    (sess / "recording.mp4").write_bytes(b"original mp4 bytes")
    (sess / "metadata.json").write_text(
        json.dumps(
            {
                "session_id": "test-session",
                "recording_dur_sec": 999.0,
                "recording_duration_sec": 999.0,
                "preserved": {"nested": True},
            }
        )
    )
    return sess


def _forbid_ffmpeg(cmd: list[str], check: bool = True) -> None:
    pytest.fail(f"ffmpeg trim should not run for this fixture: {cmd}")


def test_step2_skips_trim_when_mp4_shorter_than_trim_window(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sess = _make_session(tmp_path)
    monkeypatch.setattr(canonical_pipeline, "ffprobe_frames", lambda mp4: (14_377, 479.25))
    monkeypatch.setattr(canonical_pipeline, "run", _forbid_ffmpeg)

    canonical_pipeline.step2_trim_mp4(sess, start_offset=180, target_dur=300)

    out = capsys.readouterr().out
    assert "SHORT INPUT SKIP" in out
    assert (sess / "recording.mp4").read_bytes() == b"original mp4 bytes"
    assert not (sess / "_recording_trim.mp4").exists()

    metadata = json.loads((sess / "metadata.json").read_text())
    assert metadata["recording_dur_sec"] == pytest.approx(479.25)
    assert metadata["recording_duration_sec"] == pytest.approx(479.25)
    assert metadata["preserved"] == {"nested": True}


def test_step2_keeps_idempotent_target_duration_skip(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sess = _make_session(tmp_path)
    monkeypatch.setattr(canonical_pipeline, "ffprobe_frames", lambda mp4: (9_012, 300.4))
    monkeypatch.setattr(canonical_pipeline, "run", _forbid_ffmpeg)

    canonical_pipeline.step2_trim_mp4(sess, start_offset=180, target_dur=300)

    out = capsys.readouterr().out
    assert "IDEMPOTENT SKIP" in out
    assert (sess / "recording.mp4").read_bytes() == b"original mp4 bytes"

    metadata = json.loads((sess / "metadata.json").read_text())
    assert metadata["recording_dur_sec"] == pytest.approx(300.4)
    assert metadata["recording_duration_sec"] == pytest.approx(300.4)
