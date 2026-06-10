from __future__ import annotations

import json
from pathlib import Path

from bin import canonical_pipeline
from bin.prd_compliance_audit import audit_group_q_operator
from bin.transform_game_state_to_action_camera import main as transform_main


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def _game_state_row(timestamp_ms: int) -> dict:
    return {
        "timestamp_ms": timestamp_ms,
        "x": 0.0,
        "y": 64.0,
        "z": 0.0,
        "yaw_deg": 0.0,
        "pitch_deg": 0.0,
        "velocity_x": 0.0,
        "velocity_y": 0.0,
        "velocity_z": 0.0,
    }


def test_transform_merges_mouse_raw_delta_into_action_camera(tmp_path: Path) -> None:
    session = tmp_path / "session"
    session.mkdir()
    _write_jsonl(
        session / "game_state.jsonl",
        [_game_state_row(1_000), _game_state_row(1_050), _game_state_row(1_100)],
    )
    _write_jsonl(
        session / "inputs.jsonl",
        [
            {"event_type": "mouse_raw_delta", "timestamp_ms": 10, "dx": 7, "dy": -3},
            {"event_type": "mouse_raw_delta", "timestamp_ms": 20, "dx": -2, "dy": 4},
            {"event_type": "mouse_raw_delta", "timestamp_ms": 40, "dx": 5, "dy": 6},
        ],
    )

    assert transform_main(["transform_game_state_to_action_camera.py", str(session)]) == 0

    rows = json.loads((session / "action_camera.json").read_text(encoding="utf-8"))
    assert rows[0]["mouse_dx"] == 5.0
    assert rows[0]["mouse_dy"] == 1.0
    assert rows[1]["mouse_dx"] == 5.0
    assert rows[1]["mouse_dy"] == 6.0


def test_canonical_pipeline_denormalizes_mouse_raw_delta(tmp_path: Path) -> None:
    session = tmp_path / "session"
    session.mkdir()
    _write_jsonl(
        session / "inputs.jsonl",
        [{"event_type": "mouse_raw_delta", "timestamp_ms": 12, "dx": 17, "dy": -4}],
    )

    canonical_pipeline.step4_denormalize_inputs(session)

    [event] = [
        json.loads(line)
        for line in (session / "inputs.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert event["timestamp_ns"] == 12_000_000
    assert event["mouse_dx"] == 17
    assert event["mouse_dy"] == -4


def test_prd_audit_counts_mouse_raw_delta_as_valid_input_signal(tmp_path: Path) -> None:
    session = tmp_path / "session"
    session.mkdir()
    _write_jsonl(session / "game_state.jsonl", [_game_state_row(1_000)])
    (session / "action_camera.json").write_text(
        json.dumps([{"frame": 0, "_paused": False}]),
        encoding="utf-8",
    )
    _write_jsonl(
        session / "inputs.jsonl",
        [
            {"event_type": "mouse_raw_delta", "timestamp_ms": idx, "dx": 1, "dy": 0}
            for idx in range(50)
        ],
    )

    results = {item["id"]: item for item in audit_group_q_operator(session)}

    assert results["Q1"]["status"] == "PASS"
    assert results["Q2"]["status"] == "PASS"
    assert "valid_input_events=50" in results["Q1"]["evidence"]
