"""Schema round-trips and validation behavior."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from oyster_agent_runner.schema import (
    AgentTask,
    TaskResult,
    TrajectoryEntry,
    TrajectoryEvent,
)


def test_schema_roundtrip() -> None:
    """AgentTask + TrajectoryEntry survive JSON round-trip byte-for-byte."""
    task = AgentTask(
        task_id="mc-001",
        natural_language_instruction="Build a 3x3 wooden house",
        success_criteria=["walls", "roof", "door"],
        max_steps=200,
        target_hours=2.0,
        environment="minecraft",
        required_provider_model="claude-sonnet-4-5",
    )
    task_json = task.model_dump_json()
    task_back = AgentTask.model_validate_json(task_json)
    assert task_back == task

    entry = TrajectoryEntry(
        step=0,
        timestamp_sec=0.5,
        observation={"inventory": [], "x": 0.0, "y": 64.0, "z": 0.0},
        llm_reasoning="I should first punch a tree for wood.",
        action={"op": "attack", "target": "oak_log"},
        reward=0.0,
        success_flag=False,
    )
    entry_back = TrajectoryEntry.model_validate_json(entry.model_dump_json())
    assert entry_back == entry

    # TrajectoryEntry also accepts a string observation (e.g. a text-adventure obs).
    text_entry = TrajectoryEntry(
        step=1,
        timestamp_sec=1.0,
        observation="You are in a dark room. There is a door to the north.",
        llm_reasoning="I'll go north.",
        action={"op": "move", "dir": "north"},
    )
    assert TrajectoryEntry.model_validate_json(text_entry.model_dump_json()) == text_entry


def test_entry_expands_to_enrichment_compatible_events() -> None:
    """Every TrajectoryEntry.to_events() output must validate as TrajectoryEvent."""
    entry = TrajectoryEntry(
        step=3,
        timestamp_sec=1.23,
        observation={"hp": 20},
        llm_reasoning="Stand still.",
        action={"op": "noop"},
        reward=0.1,
        success_flag=False,
    )
    events = entry.to_events()
    # Exactly one of each core event type + reward (since reward is not None).
    types = [e.event_type for e in events]
    assert types == ["AGENT_STEP", "OBSERVATION", "LLM_REASONING", "ACTION", "REWARD"]
    for e in events:
        # Round-trip each event through JSON.
        assert TrajectoryEvent.model_validate_json(e.model_dump_json()) == e
        assert e.timestamp == 1.23


def test_entry_without_reward_skips_reward_event() -> None:
    entry = TrajectoryEntry(
        step=0,
        timestamp_sec=0.0,
        observation={},
        llm_reasoning="",
        action={"op": "noop"},
    )
    types = [e.event_type for e in entry.to_events()]
    assert "REWARD" not in types
    assert types == ["AGENT_STEP", "OBSERVATION", "LLM_REASONING", "ACTION"]


def test_agent_task_rejects_empty_id() -> None:
    with pytest.raises(ValidationError):
        AgentTask(
            task_id="",
            natural_language_instruction="x",
            environment="mock",
            required_provider_model="claude-sonnet-4-5",
        )


def test_agent_task_rejects_zero_max_steps() -> None:
    with pytest.raises(ValidationError):
        AgentTask(
            task_id="t",
            natural_language_instruction="x",
            max_steps=0,
            environment="mock",
            required_provider_model="claude-sonnet-4-5",
        )


def test_task_result_roundtrip() -> None:
    result = TaskResult(
        task_id="r-1",
        environment="mock",
        provider_model="claude-sonnet-4-5",
        total_steps=42,
        wall_clock_sec=12.34,
        success=True,
        termination_reason="success",
        final_reward=0.7,
        trajectory_path="/tmp/runs/r-1/trajectory.jsonl",
    )
    back = TaskResult.model_validate_json(result.model_dump_json())
    assert back.task_id == result.task_id
    assert back.termination_reason == "success"
    # created_at round-trips via datetime isoformat.
    assert back.created_at == result.created_at


def test_trajectory_event_rejects_whitespace_event_type() -> None:
    with pytest.raises(ValidationError):
        TrajectoryEvent(timestamp=0.0, event_type=" BAD ", event_args=None)


def test_trajectory_event_accepts_any_event_args() -> None:
    """event_args is `Any` — list, dict, scalar, null all allowed."""
    for args in ([1, 2, 3], {"k": "v"}, "scalar", 42, 3.14, None, True):
        ev = TrajectoryEvent(timestamp=0.0, event_type="X", event_args=args)
        # Round-trip via JSON to ensure serializable.
        loaded = json.loads(ev.model_dump_json())
        assert loaded["event_args"] == args
