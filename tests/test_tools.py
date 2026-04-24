"""Tool-use subsystem — Tool / ToolProvider / runner integration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oyster_agent_runner.environments.base import MockEnvironment
from oyster_agent_runner.runner import AgentRunner, RunnerConfig
from oyster_agent_runner.schema import AgentTask
from oyster_agent_runner.tools import (
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    SimpleToolProvider,
    Tool,
    tool_catalog_prompt,
)

# --- Tool / SimpleToolProvider unit tests ------------------------------------


def test_tool_dataclass_is_frozen_and_serializable() -> None:
    import dataclasses

    tool = Tool(name="echo", description="Echo args", handler=lambda args: args)
    # Frozen = can't reassign.
    with pytest.raises(dataclasses.FrozenInstanceError):
        tool.name = "changed"  # type: ignore[misc]
    # Handler is callable and returns input.
    assert tool.handler({"x": 1}) == {"x": 1}


def test_simple_tool_provider_register_and_call() -> None:
    tp = SimpleToolProvider()
    tp.register(Tool(name="add", description="Add a+b", handler=lambda args: args["a"] + args["b"]))
    assert tp.call("add", {"a": 2, "b": 3}) == 5


def test_simple_tool_provider_initial_list() -> None:
    tp = SimpleToolProvider(
        [
            Tool(name="a", description="A", handler=lambda _args: 1),
            Tool(name="b", description="B", handler=lambda _args: 2),
        ]
    )
    names = [t.name for t in tp.list_tools()]
    assert names == ["a", "b"]


def test_simple_tool_provider_unknown_tool_raises_keyerror() -> None:
    tp = SimpleToolProvider()
    with pytest.raises(KeyError, match="not registered"):
        tp.call("ghost", {})


def test_tool_catalog_prompt_lists_tools() -> None:
    tp = SimpleToolProvider(
        [
            Tool(name="find_resource", description="Locate a block", handler=lambda _: {}),
            Tool(name="craft_item", description="Craft recipe", handler=lambda _: {}),
        ]
    )
    prompt = tool_catalog_prompt(tp.list_tools())
    assert "find_resource" in prompt
    assert "Locate a block" in prompt
    assert "craft_item" in prompt
    assert "call_tool" in prompt


def test_tool_catalog_prompt_empty_when_no_tools() -> None:
    assert tool_catalog_prompt([]) == ""


# --- Runner integration ------------------------------------------------------


class _ToolThenNoopProvider:
    """Issues a call_tool action on step 0, then noops to let the env terminate."""

    def __init__(self) -> None:
        self.call_count = 0

    def chat(self, system, messages, temperature):
        self.call_count += 1
        if self.call_count == 1:
            action = {"op": "call_tool", "tool": "sum", "args": {"a": 10, "b": 20}}
        else:
            action = {"op": "noop"}
        return f"step {self.call_count}\n<action>{json.dumps(action)}</action>"


def test_runner_dispatches_tool_and_logs_events(tmp_path: Path) -> None:
    """Full loop: agent calls `sum`, result flows back, trajectory contains both events."""
    tp = SimpleToolProvider(
        [
            Tool(
                name="sum", description="Add two ints", handler=lambda args: args["a"] + args["b"]
            ),
        ]
    )
    task = AgentTask(
        task_id="tool-e2e",
        natural_language_instruction="use the sum tool",
        max_steps=5,
        environment="mock",
        required_provider_model="mock",
    )
    env = MockEnvironment(done_after_steps=3)
    provider = _ToolThenNoopProvider()
    runner = AgentRunner(RunnerConfig(write_frames=False, max_consecutive_errors=None))
    result = runner.run(task, env, provider, tmp_path / "run", tools=tp)

    # Env terminates after 3 env steps — we had 1 tool call + 3 env steps = 4 chat calls.
    assert provider.call_count == 4
    assert result.success is True
    assert result.termination_reason == "success"

    # Trajectory should have exactly one TOOL_CALL + one TOOL_RESULT event.
    events = [
        json.loads(ln) for ln in Path(result.trajectory_path).read_text().splitlines() if ln.strip()
    ]
    calls = [e for e in events if e["event_type"] == EVENT_TOOL_CALL]
    results = [e for e in events if e["event_type"] == EVENT_TOOL_RESULT]
    assert len(calls) == 1
    assert len(results) == 1
    assert calls[0]["event_args"]["tool"] == "sum"
    assert calls[0]["event_args"]["args"] == {"a": 10, "b": 20}
    assert results[0]["event_args"]["result"] == 30
    assert results[0]["event_args"]["error"] is None

    # AGENT_STEP for the tool call + 3 env steps = 4 total.
    agent_steps = [e for e in events if e["event_type"] == "AGENT_STEP"]
    assert len(agent_steps) == 4


def test_runner_records_unknown_tool_error(tmp_path: Path) -> None:
    """Calling a non-existent tool returns an error in the TOOL_RESULT event."""

    class _GhostToolProvider:
        def chat(self, system, messages, temperature):
            return "reason\n<action>" '{"op": "call_tool", "tool": "nope", "args": {}}' "</action>"

    tp = SimpleToolProvider(
        [
            Tool(name="real_tool", description="Exists", handler=lambda _args: "ok"),
        ]
    )
    task = AgentTask(
        task_id="tool-err",
        natural_language_instruction="call ghost tool",
        max_steps=2,
        environment="mock",
        required_provider_model="mock",
    )
    runner = AgentRunner(RunnerConfig(write_frames=False, max_consecutive_errors=None))
    result = runner.run(
        task,
        MockEnvironment(done_after_steps=100),
        _GhostToolProvider(),
        tmp_path / "run",
        tools=tp,
    )

    events = [
        json.loads(ln) for ln in Path(result.trajectory_path).read_text().splitlines() if ln.strip()
    ]
    tool_results = [e for e in events if e["event_type"] == EVENT_TOOL_RESULT]
    assert len(tool_results) >= 1
    assert "unknown_tool" in tool_results[0]["event_args"]["error"]


def test_runner_feeds_tool_result_back_to_provider(tmp_path: Path) -> None:
    """Provider receives the tool result as a user message on the next turn."""
    seen_messages: list[list[dict]] = []

    class _InspectProvider:
        def chat(self, system, messages, temperature):
            # Capture a shallow copy each turn for inspection.
            seen_messages.append([dict(m) for m in messages])
            if len(seen_messages) == 1:
                act = {"op": "call_tool", "tool": "greet", "args": {"who": "world"}}
            else:
                act = {"op": "noop"}
            return f"<action>{json.dumps(act)}</action>"

    tp = SimpleToolProvider(
        [
            Tool(name="greet", description="Say hi", handler=lambda args: f"hello {args['who']}"),
        ]
    )
    task = AgentTask(
        task_id="tool-feedback",
        natural_language_instruction="use greet",
        max_steps=3,
        environment="mock",
        required_provider_model="mock",
    )
    env = MockEnvironment(done_after_steps=2)
    runner = AgentRunner(RunnerConfig(write_frames=False, max_consecutive_errors=None))
    runner.run(task, env, _InspectProvider(), tmp_path / "run", tools=tp)

    # Turn 0: just the initial observation.
    # Turn 1: observation + assistant(tool_call) + user(tool result).
    # The second turn's `messages` must include a user message containing
    # "[tool:greet] result:" from the tool result we fed back.
    assert len(seen_messages) >= 2
    second_turn = seen_messages[1]
    tool_user_messages = [
        m for m in second_turn if m.get("role") == "user" and "[tool:greet]" in m.get("content", "")
    ]
    assert len(tool_user_messages) == 1
    assert "hello world" in tool_user_messages[0]["content"]


def test_runner_without_tools_ignores_call_tool_action(tmp_path: Path) -> None:
    """If no ToolProvider is passed, call_tool actions hit the env as-is."""

    class _CallToolProvider:
        def chat(self, system, messages, temperature):
            return (
                "reason\n<action>" '{"op": "call_tool", "tool": "anything", "args": {}}' "</action>"
            )

    task = AgentTask(
        task_id="no-tools",
        natural_language_instruction="x",
        max_steps=3,
        environment="mock",
        required_provider_model="mock",
    )
    runner = AgentRunner(RunnerConfig(write_frames=False, max_consecutive_errors=None))
    # No `tools=` kwarg → call_tool is treated like any other action; the env
    # just records it and advances.
    result = runner.run(
        task, MockEnvironment(done_after_steps=3), _CallToolProvider(), tmp_path / "run"
    )

    assert result.success is True
    # No TOOL_CALL events should have been emitted.
    events = [
        json.loads(ln) for ln in Path(result.trajectory_path).read_text().splitlines() if ln.strip()
    ]
    assert not any(e["event_type"] == EVENT_TOOL_CALL for e in events)
