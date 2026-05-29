from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from bin import raw_quality_gate

REPO_ROOT = Path(__file__).resolve().parent.parent


def _ffmpeg_or_skip() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg not available")
    return ffmpeg


def _make_video(session_dir: Path, *, frozen: bool) -> None:
    ffmpeg = _ffmpeg_or_skip()
    source = "color=c=blue:s=64x64:r=10:d=2" if frozen else "testsrc=s=64x64:r=10:d=2"
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            source,
            "-frames:v",
            "20",
            "-c:v",
            "mpeg4",
            "-q:v",
            "2",
            str(session_dir / "video.mp4"),
        ],
        check=True,
        capture_output=True,
    )


def _write_moving_game_state(session_dir: Path) -> None:
    rows = [
        {"position": {"x": 0.0, "y": 64.0, "z": 0.0}},
        {"position": {"x": 12.0, "y": 64.0, "z": 4.0}},
        {"position": {"x": 30.0, "y": 64.0, "z": 12.0}},
        {"position": {"x": 60.0, "y": 65.0, "z": 20.0}},
    ]
    (session_dir / "game_state.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def _write_mouse_inputs(session_dir: Path) -> None:
    (session_dir / "inputs.jsonl").write_text(
        json.dumps({"type": "mouse_move", "dx": 4, "dy": -2}) + "\n",
        encoding="utf-8",
    )


def _make_session(tmp_path: Path, *, frozen: bool, game_state: bool = True) -> Path:
    session_dir = tmp_path / ("frozen" if frozen else "live")
    session_dir.mkdir()
    _make_video(session_dir, frozen=frozen)
    if game_state:
        _write_moving_game_state(session_dir)
    _write_mouse_inputs(session_dir)
    return session_dir


def test_frozen_video_while_player_moved_is_hard_fail(tmp_path: Path) -> None:
    session_dir = _make_session(tmp_path, frozen=True)

    exit_code = raw_quality_gate.main([str(session_dir), "--json", "--samples", "8"])
    result = json.loads((session_dir / "raw_quality.json").read_text(encoding="utf-8"))

    assert exit_code == 1
    assert result["verdict"] == "FAIL"
    assert result["score"] == 0.0
    assert result["score_10"] == 0.0
    assert result["video_live"] is False
    assert result["game_state_live"] is True
    assert result["frozen_while_moving"] is True
    assert result["game_state_path_blocks"] > 20
    assert "broken video capture while player was moving" in result["reasons"]


def test_varying_video_with_moving_game_state_passes(tmp_path: Path) -> None:
    session_dir = _make_session(tmp_path, frozen=False)

    result = raw_quality_gate.evaluate_session(session_dir, sample_count=8)

    assert result["verdict"] == "PASS"
    assert result["score"] == 100.0
    assert result["score_10"] == 10.0
    assert result["video_live"] is True
    assert result["game_state_live"] is True
    assert result["frozen_while_moving"] is False
    assert result["video_unique_frame_ratio"] > 0.7
    assert result["mouse_present"] is True


def test_missing_game_state_fails_with_reason(tmp_path: Path) -> None:
    session_dir = _make_session(tmp_path, frozen=False, game_state=False)

    result = raw_quality_gate.evaluate_session(session_dir, sample_count=6)

    assert result["verdict"] == "FAIL"
    assert result["game_state_live"] is False
    assert any("game_state.jsonl missing" in reason for reason in result["reasons"])


def test_cli_json_prints_valid_json_and_writes_raw_quality(tmp_path: Path) -> None:
    session_dir = _make_session(tmp_path, frozen=False)

    proc = subprocess.run(
        [sys.executable, "bin/raw_quality_gate.py", str(session_dir), "--json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0
    printed = json.loads(proc.stdout)
    written = json.loads((session_dir / "raw_quality.json").read_text(encoding="utf-8"))
    assert printed["verdict"] == "PASS"
    assert written == printed
