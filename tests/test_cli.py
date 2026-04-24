"""CLI smoke tests — list-envs / list-providers / validate-task."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from oyster_agent_runner.cli import app

runner = CliRunner()


# --- list-envs ---------------------------------------------------------------


def test_list_envs_prints_all_registered_keys() -> None:
    result = runner.invoke(app, ["list-envs"])
    assert result.exit_code == 0, result.output
    # Expected keys appear in the rendered table.
    for key in ("mock", "minecraft", "factorio", "gym:<env_id>"):
        assert key in result.output


def test_list_envs_json_is_structured() -> None:
    result = runner.invoke(app, ["list-envs", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, list)
    keys = {row["key"] for row in data}
    assert {"mock", "minecraft", "factorio", "gym:<env_id>"}.issubset(keys)
    for row in data:
        assert "description" in row
        assert "status" in row


# --- list-providers ----------------------------------------------------------


def test_list_providers_prints_all_registered_keys() -> None:
    result = runner.invoke(app, ["list-providers"])
    assert result.exit_code == 0, result.output
    for key in ("mock", "claude", "openai", "claude-vision", "openai-vision"):
        assert key in result.output


def test_list_providers_json_is_structured() -> None:
    result = runner.invoke(app, ["list-providers", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, list)
    keys = {row["key"] for row in data}
    assert {"mock", "claude", "openai", "claude-vision", "openai-vision"}.issubset(keys)


# --- validate-task -----------------------------------------------------------


def _write_json(path: Path, obj: dict) -> Path:
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


def test_validate_task_accepts_valid_task(tmp_path: Path) -> None:
    path = _write_json(
        tmp_path / "ok.json",
        {
            "task_id": "t-1",
            "natural_language_instruction": "do a thing",
            "success_criteria": ["step 1", "step 2"],
            "max_steps": 50,
            "environment": "mock",
            "required_provider_model": "mock",
        },
    )
    result = runner.invoke(app, ["validate-task", str(path)])
    assert result.exit_code == 0, result.output
    assert "AgentTask (valid)" in result.output or "t-1" in result.output


def test_validate_task_json_output(tmp_path: Path) -> None:
    path = _write_json(
        tmp_path / "ok.json",
        {
            "task_id": "t-json",
            "natural_language_instruction": "x",
            "environment": "mock",
            "required_provider_model": "mock",
        },
    )
    result = runner.invoke(app, ["validate-task", str(path), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["task_id"] == "t-json"
    assert data["environment"] == "mock"


def test_validate_task_rejects_missing_required_field(tmp_path: Path) -> None:
    path = _write_json(
        tmp_path / "bad.json",
        {
            # missing task_id
            "natural_language_instruction": "x",
            "environment": "mock",
            "required_provider_model": "mock",
        },
    )
    result = runner.invoke(app, ["validate-task", str(path)])
    assert result.exit_code == 1
    assert "validation failed" in result.output
    assert "task_id" in result.output  # pydantic error mentions the field


def test_validate_task_rejects_extra_fields(tmp_path: Path) -> None:
    """extra='forbid' must still be in effect."""
    path = _write_json(
        tmp_path / "extra.json",
        {
            "task_id": "t",
            "natural_language_instruction": "x",
            "environment": "mock",
            "required_provider_model": "mock",
            "bogus_extra_field": 123,
        },
    )
    result = runner.invoke(app, ["validate-task", str(path)])
    assert result.exit_code == 1
    assert "validation failed" in result.output


def test_validate_task_rejects_non_json_input(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("not actually json", encoding="utf-8")
    result = runner.invoke(app, ["validate-task", str(path)])
    assert result.exit_code == 1
    assert "invalid JSON" in result.output


def test_validate_task_missing_file_is_error(tmp_path: Path) -> None:
    result = runner.invoke(app, ["validate-task", str(tmp_path / "nope.json")])
    # typer bubbles OSError → exit 2 (our convention).
    assert result.exit_code != 0


# --- existing commands still work -------------------------------------------


def test_schema_command_emits_all_three_models() -> None:
    result = runner.invoke(app, ["schema"])
    assert result.exit_code == 0
    assert "AgentTask" in result.output
    assert "TrajectoryEntry" in result.output
    assert "TaskResult" in result.output


def test_run_command_mock_end_to_end(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "run",
            "--env",
            "mock",
            "--task",
            "do some noops",
            "--provider",
            "mock",
            "--max-steps",
            "3",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Result" in result.output
