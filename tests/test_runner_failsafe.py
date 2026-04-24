"""Runner fail-safe: abort after N consecutive errors rather than burning tokens."""

from __future__ import annotations

import json
from pathlib import Path

from oyster_agent_runner.environments.base import MockEnvironment
from oyster_agent_runner.providers.base import MockLLMProvider
from oyster_agent_runner.runner import AgentRunner, RunnerConfig
from oyster_agent_runner.schema import AgentTask


class _FlakyEnv(MockEnvironment):
    """Env whose step() raises the first N times, then succeeds."""

    def __init__(self, fail_n: int, *, done_after_steps: int = 100) -> None:
        super().__init__(done_after_steps=done_after_steps)
        self.fail_n = fail_n
        self._failed = 0

    def step(self, action):
        if self._failed < self.fail_n:
            self._failed += 1
            raise RuntimeError(f"flaky_step_{self._failed}")
        return super().step(action)


def test_runner_aborts_on_max_consecutive_errors(tmp_path: Path) -> None:
    """If the env throws `max_consecutive_errors` times in a row, stop the run."""
    task = AgentTask(
        task_id="failsafe-1",
        natural_language_instruction="trigger the fail-safe",
        max_steps=100,
        environment="mock",
        required_provider_model="mock",
    )
    runner = AgentRunner(RunnerConfig(write_frames=False, max_consecutive_errors=3))
    # Env that fails every single step → we hit the cap.
    env = _FlakyEnv(fail_n=10)
    result = runner.run(task, env, MockLLMProvider(), tmp_path / "run")

    assert result.success is False
    assert result.termination_reason == "error"
    assert result.error_message is not None
    assert "aborted after 3 consecutive errors" in result.error_message
    assert "flaky_step_3" in result.error_message
    # No successful step was logged — the trajectory has no AGENT_STEP events.
    with open(result.trajectory_path) as f:
        events = [json.loads(ln) for ln in f if ln.strip()]
    assert not any(e["event_type"] == "AGENT_STEP" for e in events)


def test_runner_recovers_below_error_threshold(tmp_path: Path) -> None:
    """A few transient errors shouldn't kill the run."""
    task = AgentTask(
        task_id="failsafe-2",
        natural_language_instruction="survive flakiness",
        max_steps=100,
        environment="mock",
        required_provider_model="mock",
    )
    runner = AgentRunner(RunnerConfig(write_frames=False, max_consecutive_errors=5))
    # Fails 2 steps, then env terminates in 3 more.
    env = _FlakyEnv(fail_n=2, done_after_steps=3)
    result = runner.run(task, env, MockLLMProvider(), tmp_path / "run")

    assert result.success is True
    assert result.termination_reason == "success"
    assert result.error_message is None


def test_runner_disables_failsafe_when_cap_is_none(tmp_path: Path) -> None:
    """`max_consecutive_errors=None` restores legacy per-exception handling."""

    class ExplodingEnv(MockEnvironment):
        def step(self, action):
            raise RuntimeError("boom")

    task = AgentTask(
        task_id="failsafe-3",
        natural_language_instruction="no fail-safe",
        max_steps=10,
        environment="mock",
        required_provider_model="mock",
    )
    runner = AgentRunner(RunnerConfig(write_frames=False, max_consecutive_errors=None))
    result = runner.run(task, ExplodingEnv(), MockLLMProvider(), tmp_path / "run")

    # With the cap disabled, we just loop through max_steps soft-failing.
    # We never raise from inside the loop, so termination_reason is "max_steps".
    assert result.termination_reason == "max_steps"
    assert result.total_steps == 0  # every step was soft-skipped


def test_failsafe_counter_resets_on_success(tmp_path: Path) -> None:
    """Alternating success/fail pattern must not accumulate past the cap."""

    class AlternatingEnv(MockEnvironment):
        def __init__(self) -> None:
            super().__init__(done_after_steps=100)
            self._parity = 0

        def step(self, action):
            self._parity += 1
            if self._parity % 2 == 1:
                # odd steps fail
                raise RuntimeError(f"transient_{self._parity}")
            return super().step(action)

    task = AgentTask(
        task_id="failsafe-4",
        natural_language_instruction="alternating failures",
        max_steps=20,
        environment="mock",
        required_provider_model="mock",
    )
    runner = AgentRunner(RunnerConfig(write_frames=False, max_consecutive_errors=2))
    result = runner.run(task, AlternatingEnv(), MockLLMProvider(), tmp_path / "run")

    # Counter hits 1 each odd step then resets on the even step's success →
    # we never hit 2-in-a-row and reach max_steps cleanly.
    assert result.termination_reason == "max_steps"
    assert result.total_steps > 0

    # Sanity: roughly half of the loop iterations should have logged an
    # AGENT_STEP (only the even-parity ones succeeded).
    with open(result.trajectory_path) as f:
        events = [json.loads(ln) for ln in f if ln.strip()]
    agent_steps = [e for e in events if e["event_type"] == "AGENT_STEP"]
    # With max_steps=20 alternating, exactly 10 should have succeeded.
    assert len(agent_steps) == 10
