"""Agent runner — orchestrates the observation-reasoning-action loop.

State machine:

    INIT → RESET → (LOOP: OBSERVE → REASON → ACT → LOG)*  → TERMINATED

Termination conditions (checked in order):
    1. Environment's `done` flag       → termination_reason = "success"
    2. `max_steps` reached             → termination_reason = "max_steps"
    3. Unhandled exception in loop     → termination_reason = "error"

The runner owns two resources — the `Environment` and the
`TrajectoryLogger` — and guarantees both are shut down via a `finally`
even if the agent crashes mid-run.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from oyster_agent_runner.environments.base import (
    Action,
    Environment,
    Observation,
    has_vision,
)
from oyster_agent_runner.providers.base import LLMProvider
from oyster_agent_runner.schema import AgentTask, TaskResult, TrajectoryEntry
from oyster_agent_runner.trajectory_logger import TrajectoryLogger

# --- Prompt template ---------------------------------------------------------

SYSTEM_PROMPT_TEMPLATE = """You are an AI agent playing {environment}. Your task:

{instruction}

Success criteria:
{criteria}

For every observation you receive, respond with:
  1. Your reasoning (free text)
  2. A JSON action inside <action>...</action> tags

The action must be a single JSON object whose schema is environment-specific.
You MAY NOT leave the action empty. If you are unsure, emit {{"op": "noop"}}.
"""

_ACTION_TAG_RE = re.compile(r"<action>(.*?)</action>", re.DOTALL)


@dataclass(frozen=True)
class RunnerConfig:
    """Non-task-specific runtime knobs."""

    temperature: float = 0.7
    write_frames: bool = True
    # Seconds — best-effort wall-clock cap checked between steps. None = unlimited.
    wall_clock_cap_sec: float | None = None
    # If the env / provider raises this many times in a row, abort with
    # termination_reason="error" rather than burning tokens forever.
    # Set to None to disable the fail-safe entirely (legacy behavior).
    max_consecutive_errors: int | None = 5


class AgentRunner:
    """Drives an LLM agent through an environment and persists trajectories."""

    def __init__(self, config: RunnerConfig | None = None) -> None:
        self.config = config if config is not None else RunnerConfig()

    # Public API --------------------------------------------------------------

    def run(
        self,
        task: AgentTask,
        environment: Environment,
        provider: LLMProvider,
        output_dir: Path,
    ) -> TaskResult:
        """Execute the run and return a `TaskResult`.

        On any exception inside the loop the run terminates with
        `termination_reason="error"` and the exception is captured on
        `TaskResult.error_message` — the caller is expected to inspect
        that rather than catching.
        """
        output_dir = Path(output_dir)
        system_prompt = self._build_system_prompt(task)
        wall_start = time.monotonic()

        step = 0
        steps_executed = 0
        success = False
        termination_reason: str = "max_steps"
        error_message: str | None = None
        final_reward: float | None = None
        messages: list[dict[str, str]] = []
        consecutive_errors = 0

        with TrajectoryLogger(output_dir, write_frames=self.config.write_frames) as logger:
            logger.start(
                task_id=task.task_id,
                environment=task.environment,
                provider_model=task.required_provider_model,
            )
            try:
                obs: Observation = environment.reset(seed=None)
                for step in range(task.max_steps):
                    # Build user message from current observation.
                    messages.append(
                        {"role": "user", "content": self._format_observation(obs, step)}
                    )

                    try:
                        # Vision seam: if both the provider and env opt in,
                        # hand the current frame to the provider before
                        # `chat(...)`. Env + provider feature-detection keeps
                        # the legacy text-only path byte-identical.
                        if getattr(provider, "wants_vision", False) and has_vision(environment):
                            frame_for_llm = _safe_last_frame(environment) or self._safe_render(
                                environment
                            )
                            setter = getattr(provider, "set_next_frame", None)
                            if callable(setter):
                                setter(frame_for_llm)

                        llm_text = provider.chat(
                            system=system_prompt,
                            messages=messages,
                            temperature=self.config.temperature,
                        )
                        reasoning, action = self._parse_llm_response(llm_text)

                        # Record assistant turn so the LLM sees its own history.
                        messages.append({"role": "assistant", "content": llm_text})

                        # Capture the frame BEFORE stepping — it depicts the state
                        # the agent observed and reasoned over.
                        frame = self._safe_render(environment)

                        # Step the environment.
                        next_obs, reward, done, _info = environment.step(action)
                    except Exception as step_exc:
                        # One failed step = don't blow up the run; increment
                        # consecutive_errors and maybe abort.
                        consecutive_errors += 1
                        # Pop the user message we just added — otherwise the
                        # history will have an unanswered prompt.
                        messages.pop()
                        cap = self.config.max_consecutive_errors
                        if cap is not None and consecutive_errors >= cap:
                            termination_reason = "error"
                            error_message = (
                                f"aborted after {consecutive_errors} consecutive errors: "
                                f"{type(step_exc).__name__}: {step_exc}"
                            )
                            steps_executed = step
                            break
                        # Soft-fail: skip this step, continue the run.
                        continue

                    entry = TrajectoryEntry(
                        step=step,
                        timestamp_sec=time.monotonic() - wall_start,
                        observation=obs,
                        llm_reasoning=reasoning,
                        action=action,
                        reward=reward,
                        success_flag=done,
                    )
                    logger.append(entry, frame_png=frame)
                    final_reward = reward
                    steps_executed = step + 1
                    consecutive_errors = 0  # success → reset the counter

                    if done:
                        success = True
                        termination_reason = "success"
                        break

                    if (
                        self.config.wall_clock_cap_sec is not None
                        and (time.monotonic() - wall_start) >= self.config.wall_clock_cap_sec
                    ):
                        termination_reason = "timeout"
                        break

                    obs = next_obs
                else:
                    # Loop exhausted without break → max_steps.
                    termination_reason = "max_steps"
            except Exception as exc:
                termination_reason = "error"
                error_message = f"{type(exc).__name__}: {exc}"
            finally:
                try:
                    environment.shutdown()
                except Exception:
                    # Best-effort — we're already on the terminal path.
                    pass
                logger.end(
                    success=success,
                    total_steps=steps_executed,
                    reason=termination_reason,
                    timestamp_sec=time.monotonic() - wall_start,
                )

        return TaskResult(
            task_id=task.task_id,
            environment=task.environment,
            provider_model=task.required_provider_model,
            total_steps=steps_executed,
            wall_clock_sec=time.monotonic() - wall_start,
            success=success,
            termination_reason=termination_reason,  # type: ignore[arg-type]
            final_reward=final_reward,
            error_message=error_message,
            trajectory_path=str((output_dir / "trajectory.jsonl").resolve()),
        )

    # Internals ---------------------------------------------------------------

    @staticmethod
    def _build_system_prompt(task: AgentTask) -> str:
        criteria = (
            "\n".join(f"  - {c}" for c in task.success_criteria)
            if task.success_criteria
            else "  (none — the environment signals completion via its `done` flag)"
        )
        return SYSTEM_PROMPT_TEMPLATE.format(
            environment=task.environment,
            instruction=task.natural_language_instruction,
            criteria=criteria,
        )

    @staticmethod
    def _format_observation(obs: Observation | str, step: int) -> str:
        if isinstance(obs, str):
            obs_repr = obs
        else:
            obs_repr = json.dumps(obs, sort_keys=True, default=str)
        return f"[step {step}] observation:\n{obs_repr}"

    @staticmethod
    def _parse_llm_response(text: str) -> tuple[str, Action]:
        """Split an LLM reply into (reasoning, action_dict).

        Falls back to a noop action if the tag is missing / malformed so a
        single bad reply doesn't kill the whole run.
        """
        match = _ACTION_TAG_RE.search(text)
        if match is None:
            return text.strip(), {"op": "noop", "_parse_error": "missing_action_tag"}

        reasoning = text[: match.start()].strip()
        raw = match.group(1).strip()
        try:
            parsed: Any = json.loads(raw)
        except json.JSONDecodeError as exc:
            return reasoning, {"op": "noop", "_parse_error": f"invalid_json: {exc}"}

        if not isinstance(parsed, dict):
            return reasoning, {"op": "noop", "_parse_error": "action_not_object"}
        return reasoning, parsed

    @staticmethod
    def _safe_render(env: Environment) -> bytes | None:
        """Never let render_frame errors kill a run."""
        try:
            return env.render_frame()
        except Exception:
            return None


def _safe_last_frame(env: Environment) -> bytes | None:
    """Best-effort access to `env.last_frame()` — never raises."""
    getter = getattr(env, "last_frame", None)
    if not callable(getter):
        return None
    try:
        return getter()
    except Exception:
        return None


__all__ = ["AgentRunner", "RunnerConfig"]
