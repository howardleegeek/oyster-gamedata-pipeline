"""Trajectory logger output must be ingestible by oyster-enrichment.

The downstream contract we pin is `gamedata-recorder/check_input_log.py`'s
JSONL parser: each non-empty line is a JSON object with exactly the keys
`timestamp` (float), `event_type` (str), `event_args` (any). We
re-implement that parser inline to keep this test hermetic (no cross-repo
import at test time).
"""

from __future__ import annotations

import json
from pathlib import Path

from oyster_agent_runner.schema import TrajectoryEntry
from oyster_agent_runner.trajectory_logger import TrajectoryLogger


def _parse_jsonl_line_as_recorder_does(line: str) -> dict | None:
    """Line-for-line reimplementation of `_parse_jsonl_line` from
    gamedata-recorder/check_input_log.py. Kept here so this test is hermetic.
    """
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    try:
        return {
            "timestamp": float(obj["timestamp"]),
            "event_type": str(obj["event_type"]),
            "event_args": obj.get("event_args"),
        }
    except (KeyError, ValueError, TypeError):
        return None


def test_trajectory_logger_compatible_with_enrichment_schema(tmp_path: Path) -> None:
    """Every line written by the logger must parse via the recorder's validator."""
    entry1 = TrajectoryEntry(
        step=0,
        timestamp_sec=0.1,
        observation={"x": 1, "y": 2},
        llm_reasoning="First step.",
        action={"op": "forward"},
        reward=0.0,
        success_flag=False,
    )
    entry2 = TrajectoryEntry(
        step=1,
        timestamp_sec=0.2,
        observation="free-form text observation",
        llm_reasoning="Second step.",
        action={"op": "jump"},
        reward=1.0,
        success_flag=True,
    )

    with TrajectoryLogger(tmp_path, write_frames=True) as log:
        log.start(task_id="t-1", environment="mock", provider_model="mock")
        log.append(entry1, frame_png=b"\x89PNG\r\n\x1a\n\x00fakePNG")
        log.append(entry2, frame_png=None)
        log.end(success=True, total_steps=2, reason="success")

    traj_path = tmp_path / "trajectory.jsonl"
    assert traj_path.exists()

    lines = [ln for ln in traj_path.read_text().splitlines() if ln.strip()]
    assert len(lines) > 0

    parsed = [_parse_jsonl_line_as_recorder_does(ln) for ln in lines]

    # Zero malformed — this IS the acceptance criterion.
    assert all(p is not None for p in parsed), (
        "recorder's validator would reject some lines: "
        f"{[(i, ln) for i, (p, ln) in enumerate(zip(parsed, lines)) if p is None]}"
    )

    # Spot-check required markers — START + END are recorder convention.
    types = [p["event_type"] for p in parsed]
    assert "START" in types
    assert "END" in types
    # First step has an inline frame → RENDER event must be present.
    assert "RENDER" in types
    # Second step had no frame → we should have exactly one RENDER total.
    assert types.count("RENDER") == 1

    # Timestamps are monotonic non-decreasing (matches recorder's check).
    ts = [p["timestamp"] for p in parsed]
    assert ts == sorted(ts), f"non-monotonic: {ts}"


def test_trajectory_logger_writes_frame_bytes_and_sha(tmp_path: Path) -> None:
    """PNG bytes hit disk under frames/<step>.png and RENDER event hashes them."""
    import hashlib

    png_bytes = b"\x89PNG\r\n\x1a\nsome-fake-bytes"
    entry = TrajectoryEntry(
        step=7,
        timestamp_sec=1.0,
        observation={},
        llm_reasoning="",
        action={"op": "noop"},
    )
    with TrajectoryLogger(tmp_path) as log:
        log.start(task_id="t", environment="mock", provider_model="mock")
        log.append(entry, frame_png=png_bytes)
        log.end(success=False, total_steps=1, reason="max_steps")

    frame_path = tmp_path / "frames" / "000007.png"
    assert frame_path.read_bytes() == png_bytes

    render_events = [
        json.loads(ln)
        for ln in (tmp_path / "trajectory.jsonl").read_text().splitlines()
        if json.loads(ln)["event_type"] == "RENDER"
    ]
    assert len(render_events) == 1
    args = render_events[0]["event_args"]
    assert args["path"] == "frames/000007.png"
    assert args["sha256"] == hashlib.sha256(png_bytes).hexdigest()
    assert args["bytes"] == len(png_bytes)


def test_trajectory_logger_skips_frame_write_when_disabled(tmp_path: Path) -> None:
    entry = TrajectoryEntry(
        step=0, timestamp_sec=0.0, observation={}, llm_reasoning="", action={"op": "noop"}
    )
    with TrajectoryLogger(tmp_path, write_frames=False) as log:
        log.start(task_id="t", environment="mock", provider_model="mock")
        log.append(entry, frame_png=b"\x89PNG\r\n\x1a\n")
        log.end(success=False, total_steps=1, reason="max_steps")

    # No frames dir, no RENDER event.
    assert not (tmp_path / "frames").exists()
    types = [
        json.loads(ln)["event_type"]
        for ln in (tmp_path / "trajectory.jsonl").read_text().splitlines()
    ]
    assert "RENDER" not in types


def test_trajectory_logger_without_context_manager_raises(tmp_path: Path) -> None:
    import pytest

    log = TrajectoryLogger(tmp_path)
    with pytest.raises(RuntimeError, match="not open"):
        log.start(task_id="t", environment="mock", provider_model="mock")
