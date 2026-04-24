"""End-to-end runner test with mock LLM + mock environment.

No network, no game install, no tokens spent. Runs the full
observation-reasoning-action loop for a bounded number of steps and
verifies the trajectory file is well-formed.
"""

from __future__ import annotations

import json
from pathlib import Path

from oyster_agent_runner.environments.base import MockEnvironment
from oyster_agent_runner.providers.base import MockLLMProvider
from oyster_agent_runner.runner import AgentRunner, RunnerConfig
from oyster_agent_runner.schema import AgentTask


def _read_jsonl(path: Path) -> list[dict]:
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def test_runner_with_mock_env_completes_10_steps(tmp_path: Path) -> None:
    """Full loop runs, 10 AGENT_STEP events emitted, terminates on `done`."""
    task = AgentTask(
        task_id="mock-10",
        natural_language_instruction="Do ten noops.",
        success_criteria=["env signals done after 10 steps"],
        max_steps=50,  # room to spare — mock env terminates at 10
        environment="mock",
        required_provider_model="mock",
    )
    env = MockEnvironment(done_after_steps=10, reward_per_step=0.25)
    provider = MockLLMProvider(canned_action={"op": "noop"})
    runner = AgentRunner(RunnerConfig(temperature=0.0, write_frames=True))

    result = runner.run(task, env, provider, tmp_path / "run")

    assert result.success is True
    assert result.termination_reason == "success"
    assert result.total_steps == 10
    assert provider.call_count == 10
    # Reward on the final step is emitted — mock returns 0.25 per step.
    assert result.final_reward == 0.25

    traj = _read_jsonl(Path(result.trajectory_path))
    # 1 START + (4 or 5 events * 10 steps) + 1 RENDER * 10 + 1 END
    starts = [e for e in traj if e["event_type"] == "START"]
    ends = [e for e in traj if e["event_type"] == "END"]
    agent_steps = [e for e in traj if e["event_type"] == "AGENT_STEP"]
    observations = [e for e in traj if e["event_type"] == "OBSERVATION"]
    actions = [e for e in traj if e["event_type"] == "ACTION"]
    renders = [e for e in traj if e["event_type"] == "RENDER"]

    assert len(starts) == 1
    assert len(ends) == 1
    assert len(agent_steps) == 10
    assert len(observations) == 10
    assert len(actions) == 10
    assert len(renders) == 10

    # END marker reflects success.
    assert ends[0]["event_args"]["success"] is True
    assert ends[0]["event_args"]["total_steps"] == 10
    assert ends[0]["event_args"]["reason"] == "success"

    # Each RENDER points to a file that actually exists.
    run_dir = Path(result.trajectory_path).parent
    for r in renders:
        frame_path = run_dir / r["event_args"]["path"]
        assert frame_path.exists(), f"missing frame: {frame_path}"
        assert frame_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_runner_respects_max_steps(tmp_path: Path) -> None:
    """If env never signals done, runner terminates cleanly at max_steps."""
    task = AgentTask(
        task_id="mock-cap",
        natural_language_instruction="Loop until max_steps.",
        max_steps=5,
        environment="mock",
        required_provider_model="mock",
    )
    env = MockEnvironment(done_after_steps=1_000)  # never in 5 steps
    provider = MockLLMProvider()
    runner = AgentRunner(RunnerConfig(write_frames=False))

    result = runner.run(task, env, provider, tmp_path / "run")

    assert result.success is False
    assert result.termination_reason == "max_steps"
    assert result.total_steps == 5  # no +1 (we didn't hit the break)
    assert provider.call_count == 5


def test_runner_captures_error_in_task_result(tmp_path: Path) -> None:
    """An exception in env.step is captured on TaskResult, not propagated."""

    class ExplodingEnv(MockEnvironment):
        def step(self, action):
            raise RuntimeError("boom")

    task = AgentTask(
        task_id="mock-err",
        natural_language_instruction="X",
        max_steps=10,
        environment="mock",
        required_provider_model="mock",
    )
    runner = AgentRunner(RunnerConfig(write_frames=False))
    result = runner.run(task, ExplodingEnv(), MockLLMProvider(), tmp_path / "run")

    assert result.success is False
    assert result.termination_reason == "error"
    assert result.error_message is not None
    assert "boom" in result.error_message

    # The logger still wrote START + END.
    traj = _read_jsonl(Path(result.trajectory_path))
    assert any(e["event_type"] == "START" for e in traj)
    assert any(e["event_type"] == "END" for e in traj)


def test_runner_parses_malformed_action_gracefully(tmp_path: Path) -> None:
    """Provider that forgets the <action> tag shouldn't crash the run."""

    class NoTagProvider:
        def chat(self, system, messages, temperature):
            return "I forgot to emit a tag."

    task = AgentTask(
        task_id="mock-noparse",
        natural_language_instruction="X",
        max_steps=2,
        environment="mock",
        required_provider_model="mock",
    )
    runner = AgentRunner(RunnerConfig(write_frames=False))
    result = runner.run(task, MockEnvironment(done_after_steps=100), NoTagProvider(), tmp_path / "run")

    traj = _read_jsonl(Path(result.trajectory_path))
    actions = [e for e in traj if e["event_type"] == "ACTION"]
    assert len(actions) == 2
    # Every action should be the noop fallback with a parse-error marker.
    for a in actions:
        assert a["event_args"]["op"] == "noop"
        assert "_parse_error" in a["event_args"]
