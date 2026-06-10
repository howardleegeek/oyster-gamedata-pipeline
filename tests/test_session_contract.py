from __future__ import annotations

from pathlib import Path

from oyster_agent_runner.game_plugins import COMMON_OUTPUT_STREAMS
from oyster_agent_runner.session_contract import (
    SessionLayout,
    contract_for,
    detect_session_layout,
    is_complete_layout,
    validate_session_contract,
)


def _touch(root: Path, relative_path: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if relative_path.endswith("/"):
        path.mkdir(parents=True, exist_ok=True)
    elif path.suffix:
        path.write_text("x")
    else:
        path.mkdir(parents=True, exist_ok=True)


def test_detects_lem_recorder_layout(tmp_path: Path) -> None:
    _touch(tmp_path, "recordings/main_record.mp4")
    _touch(tmp_path, "streams/states.jsonl")
    _touch(tmp_path, "streams/actions.jsonl")

    result = detect_session_layout(tmp_path)

    assert result.layout == SessionLayout.LEM
    assert result.is_valid is True
    assert result.optional_present == ("streams/actions.jsonl",)


def test_detects_legacy_pipeline_layout(tmp_path: Path) -> None:
    _touch(tmp_path, "recording.mp4")
    _touch(tmp_path, "game_state.jsonl")
    _touch(tmp_path, "inputs.jsonl")

    result = detect_session_layout(tmp_path)

    assert result.layout == SessionLayout.LEGACY_PIPELINE
    assert result.is_valid is True
    assert set(result.present_required) == {"recording.mp4", "game_state.jsonl"}


def test_detects_phase1_agent_layout(tmp_path: Path) -> None:
    for name in ("manifest.json", "cot.jsonl", "metadata.jsonl", "inputs.jsonl"):
        _touch(tmp_path, name)

    result = detect_session_layout(tmp_path)

    assert result.layout == SessionLayout.PHASE1_AGENT
    assert result.is_valid is True


def test_detects_buyer_prd_layout(tmp_path: Path) -> None:
    for name in ("video.mp4", "systeminfo.json", "action_camera.json", "gameinfo.xlsx", "depth"):
        _touch(tmp_path, name)

    result = detect_session_layout(tmp_path)

    assert result.layout == SessionLayout.BUYER_PRD
    assert result.is_valid is True


def test_incomplete_layout_reports_missing_required_paths(tmp_path: Path) -> None:
    _touch(tmp_path, "recordings/main_record.mp4")

    result = validate_session_contract(tmp_path, SessionLayout.LEM)

    assert result.is_valid is False
    assert result.missing_required == ("streams/states.jsonl",)


def test_legacy_contract_matches_game_plugin_common_streams() -> None:
    legacy_required = {
        str(path) for path in contract_for(SessionLayout.LEGACY_PIPELINE).required_paths
    }

    assert legacy_required.issubset(set(COMMON_OUTPUT_STREAMS))
    assert "inputs.jsonl" in COMMON_OUTPUT_STREAMS
    assert "manifest.json" in COMMON_OUTPUT_STREAMS


def test_unknown_for_directory_without_known_required_paths(tmp_path: Path) -> None:
    _touch(tmp_path, "notes.txt")

    result = detect_session_layout(tmp_path)

    assert result.layout == SessionLayout.UNKNOWN
    assert result.is_valid is False
    assert is_complete_layout(tmp_path, SessionLayout.BUYER_PRD) is False
