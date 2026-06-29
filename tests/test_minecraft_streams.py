"""Tests for the Phase 1 multi-stream writer + run-mc CLI demuxer.

`MinecraftStreamWriter` fans a single stream of `TrajectoryEvent` objects
into:
  - cot.jsonl       — LLM thinking + reasoning + actions
  - metadata.jsonl  — observations + agent_step + reward
  - inputs.jsonl    — actions only
  - manifest.json   — session metadata + alignment proof

The `run-mc` CLI command additionally exercises the demuxer against a
real `trajectory.jsonl` produced by the runner (with mock env + mock
provider, so no Minecraft server required).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

UTC = timezone.utc

from pathlib import Path

import pytest
from typer.testing import CliRunner

from oyster_agent_runner.cli import app
from oyster_agent_runner.minecraft_streams import (
    COT_FILENAME,
    INPUTS_FILENAME,
    MANIFEST_FILENAME,
    METADATA_FILENAME,
    MinecraftStreamWriter,
)
from oyster_agent_runner.schema import (
    EVENT_ACTION,
    EVENT_AGENT_STEP,
    EVENT_END,
    EVENT_LLM_REASONING,
    EVENT_LLM_THINKING,
    EVENT_OBSERVATION,
    EVENT_START,
    TrajectoryEvent,
)

# --- Multi-stream writer ----------------------------------------------------


def test_writer_creates_three_files(tmp_path: Path) -> None:
    with MinecraftStreamWriter(tmp_path) as w:
        w.write(
            TrajectoryEvent(
                timestamp=0.0,
                event_type=EVENT_START,
                event_args={"task_id": "T1"},
            )
        )
    assert (tmp_path / COT_FILENAME).exists()
    assert (tmp_path / METADATA_FILENAME).exists()
    assert (tmp_path / INPUTS_FILENAME).exists()


def test_writer_demuxes_events_to_correct_streams(tmp_path: Path) -> None:
    """Each event lands in the right file. ACTION lands in BOTH cot + inputs."""
    with MinecraftStreamWriter(tmp_path) as w:
        w.write(TrajectoryEvent(timestamp=0.0, event_type=EVENT_START, event_args={"x": 1}))
        w.write(
            TrajectoryEvent(
                timestamp=0.1,
                event_type=EVENT_LLM_THINKING,
                event_args={"step": 0, "text": "deliberating"},
            )
        )
        w.write(
            TrajectoryEvent(
                timestamp=0.2,
                event_type=EVENT_LLM_REASONING,
                event_args={"text": "answer"},
            )
        )
        w.write(
            TrajectoryEvent(
                timestamp=0.3,
                event_type=EVENT_OBSERVATION,
                event_args={"value": "obs0"},
            )
        )
        w.write(
            TrajectoryEvent(
                timestamp=0.4,
                event_type=EVENT_ACTION,
                event_args={"op": "move_to", "target": [1, 2, 3]},
            )
        )
        w.write(
            TrajectoryEvent(
                timestamp=0.5,
                event_type=EVENT_AGENT_STEP,
                event_args={"step": 0, "success": False},
            )
        )
        w.write(
            TrajectoryEvent(
                timestamp=0.6,
                event_type=EVENT_END,
                event_args={"success": True, "total_steps": 1, "reason": "success"},
            )
        )

    cot = _read_jsonl(tmp_path / COT_FILENAME)
    md = _read_jsonl(tmp_path / METADATA_FILENAME)
    inp = _read_jsonl(tmp_path / INPUTS_FILENAME)

    cot_types = {e["event_type"] for e in cot}
    md_types = {e["event_type"] for e in md}
    inp_types = {e["event_type"] for e in inp}

    # cot.jsonl gets thinking + reasoning + actions + framing markers.
    assert {"START", "END", "LLM_THINKING", "LLM_REASONING", "ACTION"}.issubset(cot_types)
    # cot.jsonl does NOT contain raw observations.
    assert "OBSERVATION" not in cot_types

    # metadata.jsonl gets observations + agent_step + framing markers.
    assert {"START", "END", "OBSERVATION", "AGENT_STEP"}.issubset(md_types)
    # metadata.jsonl does NOT contain LLM thinking text.
    assert "LLM_THINKING" not in md_types
    assert "LLM_REASONING" not in md_types

    # inputs.jsonl is action-focused.
    assert "ACTION" in inp_types
    assert "OBSERVATION" not in inp_types
    assert "LLM_THINKING" not in inp_types


def test_writer_metadata_tick_emits_to_metadata_only(tmp_path: Path) -> None:
    """write_metadata_tick lands in metadata.jsonl, NOT cot/inputs."""
    with MinecraftStreamWriter(tmp_path) as w:
        w.write_metadata_tick(
            observation={"position": [1, 64, 1], "health": 20},
            timestamp_sec=0.05,
        )

    md = _read_jsonl(tmp_path / METADATA_FILENAME)
    cot = _read_jsonl(tmp_path / COT_FILENAME)
    inp = _read_jsonl(tmp_path / INPUTS_FILENAME)
    assert any(e["event_type"] == "TICK" for e in md)
    assert not cot
    assert not inp


def test_writer_finalize_writes_manifest(tmp_path: Path) -> None:
    anchor = datetime(2026, 4, 27, 18, 30, 0, tzinfo=UTC)
    with MinecraftStreamWriter(tmp_path) as w:
        w.write(
            TrajectoryEvent(timestamp=0.0, event_type=EVENT_START, event_args={"task_id": "T1"})
        )
        w.write(
            TrajectoryEvent(
                timestamp=1.5,
                event_type=EVENT_LLM_THINKING,
                event_args={"step": 0, "text": "x"},
            )
        )
        w.finalize_manifest(
            task_id="T1",
            model="claude-sonnet-4-5",
            provider="claude-thinking",
            environment="minecraft",
            anchor_utc=anchor,
            success=True,
            termination_reason="success",
            total_steps=1,
            wall_clock_sec=2.5,
            thinking_budget_tokens=16000,
        )

    manifest_path = tmp_path / MANIFEST_FILENAME
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text())

    assert manifest["task_id"] == "T1"
    assert manifest["model"] == "claude-sonnet-4-5"
    assert manifest["provider"] == "claude-thinking"
    assert manifest["license"] == "train-only"
    assert manifest["thinking_budget_tokens"] == 16000
    assert manifest["session_start_wall_clock_utc"] == "2026-04-27T18:30:00Z"
    assert manifest["alignment"]["video_fps"] is None  # Phase 1 — no video
    assert manifest["alignment"]["video_frame_count"] is None
    assert manifest["alignment"]["max_observed_drift_ms"] is None
    assert manifest["alignment"]["cot_event_count"] >= 2
    assert manifest["outputs"]["video"] is None
    assert manifest["outputs"]["frames"] is None
    assert manifest["outputs"]["cot"] == COT_FILENAME
    assert manifest["outputs"]["metadata"] == METADATA_FILENAME
    assert manifest["outputs"]["inputs"] == INPUTS_FILENAME


def test_writer_raises_when_used_without_open(tmp_path: Path) -> None:
    w = MinecraftStreamWriter(tmp_path)
    with pytest.raises(RuntimeError, match="not open"):
        w.write(TrajectoryEvent(timestamp=0.0, event_type=EVENT_START, event_args={}))


# --- run-mc CLI integration -------------------------------------------------


def test_run_mc_cli_demuxes_to_phase1_files(tmp_path: Path) -> None:
    """End-to-end: run-mc with mock provider + mock env produces all 4 files.

    We use `--provider mock` and patch the env factory so the runner uses
    `MockEnvironment` (which is vision-capable AND deterministic). This
    verifies the CLI command's plumbing: load task, run, demux,
    finalize manifest.
    """
    # Write a minimal task file (must validate against AgentTask).
    task_file = tmp_path / "task.json"
    task_file.write_text(
        json.dumps(
            {
                "task_id": "MC-cli-test-001",
                "natural_language_instruction": "do 3 noops",
                "success_criteria": ["env signals done"],
                "max_steps": 5,
                "environment": "minecraft",
                "required_provider_model": "claude-sonnet-4-5",
                "thinking_budget_tokens": 16000,
                "world_seed": 42,
            }
        )
    )
    output_dir = tmp_path / "out"

    # Monkey-patch the env factory so `run-mc` uses MockEnvironment instead
    # of spawning a Mineflayer subprocess (which would need `npm install`).
    import oyster_agent_runner.cli as cli_module
    from oyster_agent_runner.environments.base import MockEnvironment

    original = cli_module._make_minecraft_environment
    cli_module._make_minecraft_environment = lambda host, port, username: MockEnvironment(
        done_after_steps=3
    )
    try:
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "run-mc",
                "--task-file",
                str(task_file),
                "--output-dir",
                str(output_dir),
                "--provider",
                "mock",
                "--minecraft-server",
                "localhost:25565",
                "--max-steps",
                "5",
            ],
        )
    finally:
        cli_module._make_minecraft_environment = original

    assert result.exit_code == 0, f"CLI failed:\n{result.output}"

    # All four files exist.
    assert (output_dir / COT_FILENAME).exists()
    assert (output_dir / METADATA_FILENAME).exists()
    assert (output_dir / INPUTS_FILENAME).exists()
    assert (output_dir / MANIFEST_FILENAME).exists()

    # Manifest reflects the task.
    manifest = json.loads((output_dir / MANIFEST_FILENAME).read_text())
    assert manifest["task_id"] == "MC-cli-test-001"
    assert manifest["thinking_budget_tokens"] == 16000
    assert manifest["extra"]["world_seed"] == 42

    # cot.jsonl should have ACTION + LLM_REASONING (mock provider doesn't
    # emit thinking, so no LLM_THINKING here — that's expected).
    cot = _read_jsonl(output_dir / COT_FILENAME)
    cot_types = {e["event_type"] for e in cot}
    assert "ACTION" in cot_types
    assert "LLM_REASONING" in cot_types

    # metadata.jsonl has observations.
    md = _read_jsonl(output_dir / METADATA_FILENAME)
    assert any(e["event_type"] == "OBSERVATION" for e in md)

    # inputs.jsonl has actions only (and START/END markers).
    inp = _read_jsonl(output_dir / INPUTS_FILENAME)
    inp_types = {e["event_type"] for e in inp}
    assert "ACTION" in inp_types
    assert "OBSERVATION" not in inp_types
    assert "LLM_REASONING" not in inp_types


# --- Helpers ----------------------------------------------------------------


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]
