"""Regression test: surface silent error in tarball_authenticity_check.

The bare ``except Exception: pass`` around the frame-sampling best-effort
block was replaced with ``logger.debug(...)`` so a missing ffmpeg/ffprobe
install (or any other sampling error) is no longer invisible to
operators. Control flow is unchanged: a sampling failure still falls
through to the "REAL, multi-frame variation OK" verdict at the end of
``_classify_video`` (the comment explicitly says frame-sampling is
best-effort).

This test asserts:
  1. When frame-sampling raises, the function returns the "REAL,
     multi-frame variation OK" verdict (control flow preserved).
  2. A DEBUG log record is emitted on the module logger binding the
     exception (silence is surfaced, not swallowed).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from bin import tarball_authenticity_check  # noqa: E402


# A minimal valid ffprobe JSON response — empty format/streams so the
# function falls through the encoder-tag and real-capture-stamp checks
# and proceeds to the frame-sampling block.
_VALID_FFPROBE = {
    "format": {"tags": {}},
    "streams": [],
}


def test_frame_sampling_failure_falls_through_to_real(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Sampling exception → preserved fallback verdict + DEBUG log emitted."""
    video = tmp_path / "video.mp4"
    video.write_bytes(b"\x00\x00\x00\x00")  # content doesn't matter

    class _FakeResult:
        def __init__(self, stdout: str) -> None:
            self.stdout = stdout

    import json as _json

    def _fake_run(cmd, *args, **kwargs):
        # First call: ffprobe — return a valid (empty) JSON envelope so
        # the function falls through to the frame-sampling block.
        if cmd and cmd[0] == "ffprobe":
            return _FakeResult(_json.dumps(_VALID_FFPROBE))
        # All other calls (ffmpeg frame sampling) — simulate missing
        # ffmpeg / ffmpeg crash so the `except Exception` arm fires.
        raise OSError("simulated ffmpeg failure")

    monkeypatch.setattr(tarball_authenticity_check.subprocess, "run", _fake_run)

    with caplog.at_level(logging.DEBUG, logger="bin.tarball_authenticity_check"):
        verdict, reason = tarball_authenticity_check._classify_video(video)

    # Control flow preserved: sampling failure is best-effort, so the
    # final verdict is the "REAL, multi-frame variation OK" fallback
    # (per the original comment in the source).
    assert verdict == tarball_authenticity_check.REAL
    assert "multi-frame variation" in reason

    # Silence surfaced: a DEBUG log record binds the exception.
    debug_records = [
        r for r in caplog.records if r.levelno == logging.DEBUG
    ]
    assert debug_records, "expected a DEBUG log record surfacing the sampling error"
    msg = debug_records[0].getMessage()
    assert "frame-sampling" in msg.lower()
    assert str(video) in msg


def test_missing_file_short_circuits_without_logging(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Missing video → UNKNOWN, no DEBUG log (no exception was swallowed)."""
    video = tmp_path / "nope.mp4"
    with caplog.at_level(logging.DEBUG, logger="bin.tarball_authenticity_check"):
        verdict, reason = tarball_authenticity_check._classify_video(video)
    assert verdict == tarball_authenticity_check.UNKNOWN
    assert reason == "missing"
