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

import contextlib
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from oyster_agent_runner.environments.base import (
    Action,
    Environment,
    Observation,
    has_vision,
)
from oyster_agent_runner.providers.base import LLMProvider
from oyster_agent_runner.schema import (
    EVENT_LLM_THINKING,
    AgentTask,
    TaskResult,
    TrajectoryEntry,
    TrajectoryEvent,
)
from oyster_agent_runner.tools import (
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    ToolProvider,
    tool_catalog_prompt,
)
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
        tools: ToolProvider | None = None,
    ) -> TaskResult:
        """Execute the run and return a `TaskResult`.

        On any exception inside the loop the run terminates with
        `termination_reason="error"` and the exception is captured on
        `TaskResult.error_message` — the caller is expected to inspect
        that rather than catching.

        If `tools` is provided, actions shaped like
        `{"op": "call_tool", "tool": "<name>", "args": {...}}` are
        dispatched to the tool provider rather than the environment.
        The tool's return value is fed back into the agent's next user
        message and also logged as `TOOL_CALL` + `TOOL_RESULT` events.
        """
        output_dir = Path(output_dir)
        system_prompt = self._build_system_prompt(task, tools=tools)
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

                        # Thinking-mode hook: providers that capture
                        # extended-thinking content expose a `last_thinking`
                        # str attribute populated by the most recent chat()
                        # call. We emit a dedicated `LLM_THINKING` event
                        # *before* the regular `LLM_REASONING` so the
                        # trajectory orders chain-of-thought (the agent's
                        # private deliberation) before the externalized
                        # reasoning + action.
                        if getattr(provider, "wants_thinking_capture", False):
                            thinking_text = getattr(provider, "last_thinking", None)
                            if thinking_text:
                                logger.write_event(
                                    TrajectoryEvent(
                                        timestamp=time.monotonic() - wall_start,
                                        event_type=EVENT_LLM_THINKING,
                                        event_args={
                                            "step": step,
                                            "text": thinking_text,
                                        },
                                    )
                                )

                        reasoning, action = self._parse_llm_response(llm_text)

                        # Record assistant turn so the LLM sees its own history.
                        messages.append({"role": "assistant", "content": llm_text})

                        # Capture the frame BEFORE stepping — it depicts the state
                        # the agent observed and reasoned over.
                        frame = self._safe_render(environment)

                        # Route tool calls without advancing the env.
                        if tools is not None and action.get("op") == "call_tool":
                            tool_result, tool_error = self._dispatch_tool(tools, action)
                            self._log_tool_events(
                                logger, action, tool_result, tool_error, wall_start
                            )
                            # Feed the result back into the next turn.
                            messages.append(
                                {
                                    "role": "user",
                                    "content": self._format_tool_result(
                                        tool_name=action.get("tool", ""),
                                        result=tool_result,
                                        error=tool_error,
                                    ),
                                }
                            )
                            # Log the "action" event for the tool call for
                            # trace continuity, but don't step or reset obs.
                            entry = TrajectoryEntry(
                                step=step,
                                timestamp_sec=time.monotonic() - wall_start,
                                observation=obs,
                                llm_reasoning=reasoning,
                                action=action,
                                reward=None,
                                success_flag=False,
                            )
                            logger.append(entry, frame_png=frame)
                            steps_executed = step + 1
                            consecutive_errors = 0
                            continue

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
                # Best-effort shutdown — we're already on the terminal path.
                with contextlib.suppress(Exception):
                    environment.shutdown()
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
    def _build_system_prompt(task: AgentTask, tools: ToolProvider | None = None) -> str:
        criteria = (
            "\n".join(f"  - {c}" for c in task.success_criteria)
            if task.success_criteria
            else "  (none — the environment signals completion via its `done` flag)"
        )
        prompt = SYSTEM_PROMPT_TEMPLATE.format(
            environment=task.environment,
            instruction=task.natural_language_instruction,
            criteria=criteria,
        )
        if tools is not None:
            catalog = tool_catalog_prompt(tools.list_tools())
            if catalog:
                prompt = f"{prompt}\n{catalog}\n"
        return prompt

    @staticmethod
    def _dispatch_tool(tools: ToolProvider, action: Action) -> tuple[Any | None, str | None]:
        """Invoke the requested tool. Returns (result, error_message)."""
        name = action.get("tool")
        args = action.get("args", {}) or {}
        if not isinstance(name, str) or not name:
            return None, "missing_tool_name"
        if not isinstance(args, dict):
            return None, "args_must_be_object"
        try:
            return tools.call(name, args), None
        except KeyError as exc:
            return None, f"unknown_tool: {exc}"
        except Exception as exc:
            return None, f"{type(exc).__name__}: {exc}"

    @staticmethod
    def _log_tool_events(
        logger: TrajectoryLogger,
        action: Action,
        result: Any,
        error: str | None,
        wall_start: float,
    ) -> None:
        """Emit TOOL_CALL + TOOL_RESULT events into the trajectory."""
        ts = time.monotonic() - wall_start
        logger.write_event(
            TrajectoryEvent(
                timestamp=ts,
                event_type=EVENT_TOOL_CALL,
                event_args={
                    "tool": action.get("tool", ""),
                    "args": action.get("args", {}),
                },
            )
        )
        # Round-trip result via repr if it's not JSON-native to avoid
        # pydantic choking on exotic return types.
        safe_result: Any
        try:
            json.dumps(result)
            safe_result = result
        except (TypeError, ValueError):
            safe_result = repr(result)
        logger.write_event(
            TrajectoryEvent(
                timestamp=ts + 1e-9,  # keep monotonic ordering CALL → RESULT
                event_type=EVENT_TOOL_RESULT,
                event_args={
                    "tool": action.get("tool", ""),
                    "result": safe_result,
                    "error": error,
                },
            )
        )

    @staticmethod
    def _format_tool_result(tool_name: str, result: Any, error: str | None) -> str:
        """Render a tool invocation result for the next user message."""
        if error is not None:
            return f"[tool:{tool_name}] error: {error}"
        try:
            body = json.dumps(result, sort_keys=True, default=str)
        except (TypeError, ValueError):
            body = repr(result)
        return f"[tool:{tool_name}] result: {body}"

    @staticmethod
    def _format_observation(obs: Observation | str, step: int) -> str:
        obs_repr = obs if isinstance(obs, str) else json.dumps(obs, sort_keys=True, default=str)
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
        except Exception as e:
            logger.warning("render_frame() failed: %s", e)
            return None


def _safe_last_frame(env: Environment) -> bytes | None:
    """Best-effort access to `env.last_frame()` — never raises."""
    getter = getattr(env, "last_frame", None)
    if not callable(getter):
        return None
    try:
        return getter()
    except Exception as e:
        logger.warning("last_frame() failed: %s", e)
        return None


__all__ = ["AgentRunner", "RunnerConfig"]
