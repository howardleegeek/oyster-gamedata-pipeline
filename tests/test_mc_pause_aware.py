from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BIN_DIR = REPO_ROOT / "bin"
sys.path.insert(0, str(BIN_DIR))

from prd_compliance_audit import audit_group_q_operator  # noqa: E402
from transform_game_state_to_action_camera import (  # noqa: E402
    transform_tick_to_action_camera_row,
)


def _sample(tick: int, *, paused: bool = False) -> dict:
    return {
        "tick": tick,
        "timestamp_ms": 1_779_977_223_420 + tick * 50,
        "x": float(tick),
        "y": 64.0,
        "z": 0.0,
        "yaw_deg": 90.0,
        "pitch_deg": 0.0,
        "look_x": 0.0,
        "look_y": 0.0,
        "look_z": 1.0,
        "velocity_x": 0.0,
        "velocity_y": 0.0,
        "velocity_z": 0.0,
        "on_ground": True,
        "sneaking": False,
        "sprinting": False,
        "paused": paused,
        "dimension": "minecraft:overworld",
        "game_mode": "SURVIVAL",
    }


def test_transform_propagates_paused_flag_to_action_camera() -> None:
    paused_row = transform_tick_to_action_camera_row(_sample(1, paused=True), 0)
    active_row = transform_tick_to_action_camera_row(_sample(2, paused=False), 1)

    assert paused_row["_paused"] is True
    assert active_row["_paused"] is False


def test_prd_audit_q3_fails_when_paused_samples_exceed_5_percent(tmp_path: Path) -> None:
    session = tmp_path
    game_state_rows = [_sample(i, paused=i < 6) for i in range(100)]
    (session / "game_state.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in game_state_rows),
        encoding="utf-8",
    )
    action_camera_rows = [{"frame": i, "_paused": i < 6} for i in range(100)]
    (session / "action_camera.json").write_text(
        json.dumps(action_camera_rows),
        encoding="utf-8",
    )

    q3 = next(item for item in audit_group_q_operator(session) if item["id"] == "Q3")

    assert q3["status"] == "FAIL"
    assert "6/100" in q3["evidence"]
    assert "max 5%" in q3["evidence"]


def test_mc_mod_source_marks_only_client_pause_state_in_sample_contract() -> None:
    capture = (
        REPO_ROOT / "mc-mod/src/main/java/world/oyster/recorder/GameStateCapture.java"
    ).read_text(encoding="utf-8")
    sample = (
        REPO_ROOT / "mc-mod/src/main/java/world/oyster/recorder/GameStateSample.java"
    ).read_text(encoding="utf-8")
    server_capture = (
        REPO_ROOT / "mc-mod/src/main/java/world/oyster/recorder/server/ServerStateCapture.java"
    ).read_text(encoding="utf-8")

    assert "currentScreen" not in capture
    assert "boolean paused = client.isPaused();" in capture
    assert "boolean paused" in sample
    assert '"paused"' in sample
    assert "player.isSprinting(),\n            false," in server_capture
