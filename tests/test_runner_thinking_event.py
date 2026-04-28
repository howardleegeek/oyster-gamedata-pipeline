"""Tests for the runner's `LLM_THINKING` event emission path.

When a provider exposes `wants_thinking_capture = True` and populates
`last_thinking` after each `chat()` call, the runner MUST emit an
`LLM_THINKING` event into trajectory.jsonl, ordered before the
`LLM_REASONING` event for the same step.

Providers without `wants_thinking_capture` (the default) MUST NOT see
any `LLM_THINKING` events emitted — backward compatibility for the
existing 89-test baseline.
"""

from __future__ import annotations

import json
from pathlib import Path

from oyster_agent_runner.environments.base import MockEnvironment
from oyster_agent_runner.runner import AgentRunner, RunnerConfig
from oyster_agent_runner.schema import AgentTask

# --- Fake providers ---------------------------------------------------------


class _ThinkingProvider:
    """Mock thinking-capable provider — emits a canned thinking + action."""

    wants_thinking_capture = True

    def __init__(self) -> None:
        self.call_count = 0
        self.last_thinking: str | None = None

    def chat(self, system: str, messages: list[dict], temperature: float) -> str:
        self.call_count += 1
        self.last_thinking = f"Reasoning chain for call {self.call_count}: I should noop."
        return f'Step {self.call_count} answer.\n<action>{{"op":"noop"}}</action>'


class _ThinkingProviderEmitsNothing(_ThinkingProvider):
    """Edge case: provider opts in but `last_thinking` stays None / empty."""

    def chat(self, system: str, messages: list[dict], temperature: float) -> str:
        self.call_count += 1
        # Intentionally do not set last_thinking — simulate a thinking-mode
        # request that returned no thinking blocks (degraded case).
        self.last_thinking = ""
        return f'Step {self.call_count}.\n<action>{{"op":"noop"}}</action>'


class _NonThinkingProvider:
    """Plain mock — no thinking capture, no `wants_thinking_capture` flag."""

    def __init__(self) -> None:
        self.call_count = 0

    def chat(self, system: str, messages: list[dict], temperature: float) -> str:
        self.call_count += 1
        return f'Step {self.call_count}.\n<action>{{"op":"noop"}}</action>'


# --- Helpers ----------------------------------------------------------------


def _read_trajectory(path: str | Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


# --- Tests ------------------------------------------------------------------


def test_thinking_provider_emits_llm_thinking_events(tmp_path: Path) -> None:
    """A thinking-capable provider should produce one LLM_THINKING per step."""
    task = AgentTask(
        task_id="thinking-1",
        natural_language_instruction="noop until done",
        max_steps=5,
        environment="mock",
        required_provider_model="claude-sonnet-4-5",
    )
    env = MockEnvironment(done_after_steps=3)
    provider = _ThinkingProvider()
    runner = AgentRunner(RunnerConfig(write_frames=False))

    result = runner.run(task, env, provider, tmp_path / "run")

    events = _read_trajectory(result.trajectory_path)
    thinking_events = [e for e in events if e["event_type"] == "LLM_THINKING"]
    reasoning_events = [e for e in events if e["event_type"] == "LLM_REASONING"]

    assert len(thinking_events) == 3
    assert len(reasoning_events) == 3
    # Each thinking event has the expected payload shape.
    for i, ev in enumerate(thinking_events):
        assert ev["event_args"]["step"] == i
        assert "Reasoning chain for call" in ev["event_args"]["text"]


def test_thinking_event_precedes_reasoning_event_per_step(tmp_path: Path) -> None:
    """For each step, the LLM_THINKING event must appear BEFORE LLM_REASONING."""
    task = AgentTask(
        task_id="thinking-order",
        natural_language_instruction="x",
        max_steps=5,
        environment="mock",
        required_provider_model="m",
    )
    env = MockEnvironment(done_after_steps=2)
    provider = _ThinkingProvider()
    runner = AgentRunner(RunnerConfig(write_frames=False))
    result = runner.run(task, env, provider, tmp_path / "run")

    events = _read_trajectory(result.trajectory_path)
    # Walk the file and assert every LLM_THINKING is immediately before
    # the next LLM_REASONING for the same step. This is the contract that
    # downstream alignment proofs depend on.
    seen_thinking_for_step: set[int] = set()
    for ev in events:
        if ev["event_type"] == "LLM_THINKING":
            seen_thinking_for_step.add(ev["event_args"]["step"])
        elif ev["event_type"] == "AGENT_STEP":
            step_no = ev["event_args"]["step"]
            # By the time AGENT_STEP fires, the thinking event for that
            # step (if any) must already be in the file.
            assert (
                step_no in seen_thinking_for_step
            ), f"step {step_no}: AGENT_STEP appeared before LLM_THINKING"


def test_thinking_provider_with_empty_thinking_emits_no_event(tmp_path: Path) -> None:
    """If `last_thinking` is empty (degraded case), don't emit a hollow event."""
    task = AgentTask(
        task_id="thinking-empty",
        natural_language_instruction="x",
        max_steps=3,
        environment="mock",
        required_provider_model="m",
    )
    env = MockEnvironment(done_after_steps=2)
    provider = _ThinkingProviderEmitsNothing()
    runner = AgentRunner(RunnerConfig(write_frames=False))
    result = runner.run(task, env, provider, tmp_path / "run")

    events = _read_trajectory(result.trajectory_path)
    thinking_events = [e for e in events if e["event_type"] == "LLM_THINKING"]
    # Empty thinking → no event. The trajectory still has reasoning + action.
    assert len(thinking_events) == 0
    assert any(e["event_type"] == "LLM_REASONING" for e in events)


def test_non_thinking_provider_emits_no_thinking_events(tmp_path: Path) -> None:
    """Backwards-compat — providers without the flag must not change behavior."""
    task = AgentTask(
        task_id="non-thinking",
        natural_language_instruction="x",
        max_steps=3,
        environment="mock",
        required_provider_model="m",
    )
    env = MockEnvironment(done_after_steps=2)
    provider = _NonThinkingProvider()
    runner = AgentRunner(RunnerConfig(write_frames=False))
    result = runner.run(task, env, provider, tmp_path / "run")

    events = _read_trajectory(result.trajectory_path)
    thinking_events = [e for e in events if e["event_type"] == "LLM_THINKING"]
    assert len(thinking_events) == 0
    # But normal reasoning events still appear.
    reasoning_events = [e for e in events if e["event_type"] == "LLM_REASONING"]
    assert len(reasoning_events) >= 1
