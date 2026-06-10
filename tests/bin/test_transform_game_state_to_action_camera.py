from __future__ import annotations

import json
from pathlib import Path

from bin.transform_game_state_to_action_camera import (
    load_game_resolution,
    main,
    transform_tick_to_action_camera_row,
)


def _tick(**overrides: object) -> dict[str, object]:
    tick: dict[str, object] = {
        "timestamp_ms": 1000,
        "x": 0.0,
        "y": 64.0,
        "z": 0.0,
        "yaw_deg": 0.0,
        "pitch_deg": 0.0,
        "velocity_x": 0.0,
        "velocity_y": 0.0,
        "velocity_z": 0.0,
    }
    tick.update(overrides)
    return tick


def test_raw_mouse_pixels_are_normalized_by_screen_resolution() -> None:
    row = transform_tick_to_action_camera_row(
        _tick(mouse_x=960, mouse_y=270),
        frame_idx=0,
        screen_width=1920,
        screen_height=1080,
    )

    assert row["mouse_x"] == 0.5
    assert row["mouse_y"] == 0.25


def test_raw_mouse_pixels_are_clamped_to_unit_interval() -> None:
    row = transform_tick_to_action_camera_row(
        _tick(mouse_x=3840, mouse_y=-10),
        frame_idx=0,
        screen_width=1920,
        screen_height=1080,
    )

    assert row["mouse_x"] == 1.0
    assert row["mouse_y"] == 0.0


def test_load_game_resolution_reads_metadata_game_resolution(tmp_path: Path) -> None:
    session = tmp_path / "session"
    session.mkdir()
    (session / "metadata.json").write_text(
        json.dumps({"game_resolution": [2560, 1440]}),
        encoding="utf-8",
    )

    assert load_game_resolution(session) == (2560, 1440)


def test_cli_uses_metadata_resolution_for_mouse_normalization(tmp_path: Path) -> None:
    session = tmp_path / "session"
    session.mkdir()
    (session / "metadata.json").write_text(
        json.dumps({"game_resolution": [1920, 1080]}),
        encoding="utf-8",
    )
    (session / "game_state.jsonl").write_text(
        json.dumps(_tick(mouse_x=1920, mouse_y=540)) + "\n",
        encoding="utf-8",
    )

    assert main(["transform_game_state_to_action_camera.py", str(session)]) == 0

    rows = json.loads((session / "action_camera.json").read_text(encoding="utf-8"))
    assert len(rows) == 1
    assert rows[0]["mouse_x"] == 1.0
    assert rows[0]["mouse_y"] == 0.5
