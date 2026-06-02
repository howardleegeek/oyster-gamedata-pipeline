#!/usr/bin/env python3
"""Fail-closed integrity tests for bin/score_session.py (ISC-FAILCLOSED).

These pin the integrity hole closed: a session whose video is NON-DECODABLE
(corrupt/garbage bytes, empty, missing, or an implausibly tiny stub) must
FAIL-CLOSED — ``passed=false`` / ``verdict="NOT-PASSED"`` / ``prd_passed=false``
with NO high ``prd_score_percent`` — instead of slipping through with a high
partial score that downstream ``ingest_worker`` could accept and pay for.

The guard is tied to a GENUINE decode of the video (the same
``ffprobe_truth`` STAGE 0 already uses): the run only proceeds when BOTH
``fps > 0`` AND ``frame_count > 0`` come back from a real decode. A real,
decodable session must NOT trip the guard — its verdict stays driven by the
PRD stage exactly as before.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# --- Import the module under test from bin/ (sibling of tests/). -------------
# (Adding bin/ to sys.path — mirrors test_build_action_camera_alignment.py —
# registers the module in sys.modules so its dataclasses resolve correctly.)
REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = REPO_ROOT / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

import score_session  # noqa: E402

# --- Skip the whole module if the real decoder isn't available. --------------
# The guard is built on a genuine ffprobe/ffmpeg decode; without them on PATH
# these tests cannot exercise (or verify the absence of) a real decode.
pytestmark = pytest.mark.skipif(
    shutil.which("ffprobe") is None or shutil.which("ffmpeg") is None,
    reason="ffprobe/ffmpeg required to exercise the real-decode fail-closed guard",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_MINIMAL_META = {
    "session_id": "failclosed-fixture",
    "recorder_version": "2.6.14",
    "game_resolution": [960, 544],
    "capture_resolution": [960, 544],
    "fps_effective": 24.0,
    "average_fps": 24.0,
    "frame_count": 100,
    "duration": 4.0,
    "start_timestamp": 1.0,
    "end_timestamp": 5.0,
    "hardware_specs": {
        "cpu": {"brand": "Intel Test"},
        "gpus": [{"name": "GPU Test"}],
        "system": {
            "os_name": "Windows",
            "os_version": "11",
            "total_memory_gb": 16.0,
        },
    },
}


def _write_minimal_session(session_dir: Path) -> None:
    """Write metadata.json + minimal game_state/inputs so earlier stages don't
    crash *before* the guard. The video is written separately per-test."""
    (session_dir / "metadata.json").write_text(json.dumps(_MINIMAL_META, indent=2))
    with (session_dir / "game_state.jsonl").open("w") as f:
        for i in range(10):
            f.write(
                json.dumps(
                    {
                        "timestamp": i * 0.1,
                        "x": float(i),
                        "y": 64.0,
                        "z": 0.0,
                        "yaw": 0.0,
                        "pitch": 0.0,
                    }
                )
                + "\n"
            )
    with (session_dir / "inputs.jsonl").open("w") as f:
        for i in range(10):
            f.write(
                json.dumps({"timestamp": i * 0.1, "type": "key", "key": "w", "state": "down"})
                + "\n"
            )


def _make_garbage_session(tmp_path: Path, *, video_bytes: bytes) -> Path:
    """A session whose recording.mp4 is *video_bytes* (non-decodable garbage)."""
    session = tmp_path / "garbage_session"
    session.mkdir()
    _write_minimal_session(session)
    (session / "recording.mp4").write_bytes(video_bytes)
    return session


def _make_decodable_session(tmp_path: Path) -> Path:
    """A session with a tiny but GENUINELY decodable video (ffmpeg testsrc).

    320x240 @ 10fps for 2s → 20 real frames. Decodes cleanly, so the guard must
    NOT fire on it. Small and cheap (no large fixture, no GPU)."""
    session = tmp_path / "decodable_session"
    session.mkdir()
    _write_minimal_session(session)
    out = session / "recording.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=2:size=320x240:rate=10",
            "-pix_fmt",
            "yuv420p",
            str(out),
            "-y",
        ],
        check=True,
        capture_output=True,
    )
    assert out.is_file() and out.stat().st_size > 0
    return session


# ---------------------------------------------------------------------------
# The authoritative guard, in isolation
# ---------------------------------------------------------------------------


def test_guard_helper_exists():
    """The fail-closed guard must be exposed as a callable on the module."""
    assert hasattr(
        score_session, "assert_video_decodable"
    ), "score_session must expose an authoritative decode guard"


def test_guard_flags_garbage_video(tmp_path):
    """A genuine decode of garbage bytes yields no fps/frame_count → guard fails."""
    session = _make_garbage_session(tmp_path, video_bytes=os.urandom(2048))
    guard = score_session.assert_video_decodable(session)
    assert guard.status == "fail"
    assert "non-decodable" in (guard.error or "").lower()
    # Reason is recorded somewhere a human/auditor will see it.
    blob = json.dumps(guard.to_dict()).lower()
    assert "fail-closed" in blob


def test_guard_flags_missing_video(tmp_path):
    """No video at all → fail-closed (cannot decode what isn't there)."""
    session = tmp_path / "no_video"
    session.mkdir()
    _write_minimal_session(session)
    guard = score_session.assert_video_decodable(session)
    assert guard.status == "fail"


def test_guard_flags_empty_video(tmp_path):
    """A zero-byte recording.mp4 → fail-closed."""
    session = _make_garbage_session(tmp_path, video_bytes=b"")
    guard = score_session.assert_video_decodable(session)
    assert guard.status == "fail"


def test_guard_flags_tiny_stub(tmp_path):
    """An implausibly tiny stub (a few bytes) → fail-closed."""
    session = _make_garbage_session(tmp_path, video_bytes=b"\x00\x00\x00\x18ftyp")
    guard = score_session.assert_video_decodable(session)
    assert guard.status == "fail"


def test_guard_passes_decodable_video(tmp_path):
    """A genuinely decodable video → guard does NOT fire (status ok)."""
    session = _make_decodable_session(tmp_path)
    guard = score_session.assert_video_decodable(session)
    assert guard.status == "ok", f"guard wrongly fired: {guard.to_dict()}"
    # And it must have obtained real, positive fps + frame_count from the decode.
    assert (guard.detail.get("fps") or 0) > 0
    assert (guard.detail.get("frame_count") or 0) > 0


# ---------------------------------------------------------------------------
# The orchestrator (end-to-end) on garbage — must short-circuit fail-closed
# ---------------------------------------------------------------------------


def test_score_session_garbage_fails_closed(tmp_path):
    """The WHOLE orchestrator on garbage: NOT-PASSED, no high partial score.

    This is the core integrity assertion. Before the fix the run emitted a HIGH
    partial ``prd_score_percent`` (~83-88%) even on non-decodable garbage; that
    is the fail-OPEN hole ingest_worker could pay for. Now it must fail-closed.
    """
    session = _make_garbage_session(tmp_path, video_bytes=os.urandom(2048))
    report = score_session.score_session(session, repo_root=REPO_ROOT, skip_depth=True)

    # Verdict is an unambiguous refusal.
    assert report["passed"] is False
    assert report["verdict"] == "NOT-PASSED"
    assert report["prd_passed"] is False

    # No high partial score leaks downstream. (0.0 or None — never a near-pass.)
    score = report["prd_score_percent"]
    assert score in (0.0, 0, None), f"garbage must not emit a score, got {score!r}"

    # An integrity stage is present, marked failed, with a clear reason.
    stages = report.get("stages", [])
    integrity = [s for s in stages if s.get("status") == "fail"]
    assert integrity, "expected a failed integrity stage in the report"
    reason_blob = json.dumps(integrity).lower()
    assert "non-decodable" in reason_blob
    assert "fail-closed" in reason_blob

    # Short-circuit: the downstream score-fabricating stages must NOT have run.
    stage_names = {s["name"] for s in stages}
    assert score_session.STAGE_PRD not in stage_names
    assert score_session.STAGE_ACTION_CAM not in stage_names


def test_score_session_decodable_runs_past_guard(tmp_path):
    """On a decodable video the guard must NOT short-circuit the pipeline.

    We only assert the guard let the run PROCEED past STAGE 0 (it ran the real
    stages, not just an integrity refusal). The PRD verdict itself is exercised
    by the real-session no-regression check (run_failclosed manually); here we
    only prove the guard does not wrongly fire on real frames.
    """
    session = _make_decodable_session(tmp_path)
    report = score_session.score_session(session, repo_root=REPO_ROOT, skip_depth=True)
    stage_names = {s["name"] for s in report.get("stages", [])}
    # If the guard had fired, these real stages would have been skipped.
    assert score_session.STAGE_FPS in stage_names
    assert score_session.STAGE_PRD in stage_names
    # The fps stage really decoded the synthetic video (positive truth values).
    fps_stage = next(s for s in report["stages"] if s["name"] == score_session.STAGE_FPS)
    assert (fps_stage["detail"].get("real_fps") or 0) > 0
    assert (fps_stage["detail"].get("real_frame_count") or 0) > 0
    # The integrity gate ran but PASSED (status ok) — it must not have refused a
    # genuinely decodable session, and it must not be flagged as a failure.
    integrity = next(s for s in report["stages"] if s["name"] == score_session.STAGE_INTEGRITY)
    assert integrity["status"] == "ok", "guard wrongly fired on a decodable video"
    assert integrity["detail"].get("decodable") is True
    assert report.get("fail_closed") is not True
    # And the run was NOT short-circuited as fail-closed.
    assert score_session.STAGE_INTEGRITY not in report.get("stage_failures", [])
