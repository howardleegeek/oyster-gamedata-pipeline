"""Pydantic v2 models defining the L4 trajectory contract.

`AgentTask` is the input spec to the runner. `TrajectoryEntry` is one step of
the agent's interaction with the environment. `TaskResult` is the terminal
summary written after the run completes.

`TrajectoryEvent` is the wire format we write to disk — a thin `{timestamp,
event_type, event_args}` envelope that matches the event shape consumed by
`oyster-enrichment`'s input-log validator (see
`gamedata-recorder/check_input_log.py`). This lets downstream tools ingest
agent trajectories with zero schema changes.
"""

from __future__ import annotations

from datetime import datetime, timezone

UTC = timezone.utc
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# --- Event type tags ----------------------------------------------------------

# Agent-step events we emit. These are additive to the recorder's enum
# (MOUSE_MOVE, MOUSE_BUTTON, KEYBOARD, START, END, VIDEO_START, VIDEO_END) —
# the validator treats unknown types as generic events and only *requires*
# KEYBOARD / MOUSE_* when `--require-*` flags are set, so L4 trajectories
# parse cleanly by default.
EVENT_START: Literal["START"] = "START"
EVENT_END: Literal["END"] = "END"
EVENT_AGENT_STEP: Literal["AGENT_STEP"] = "AGENT_STEP"
EVENT_OBSERVATION: Literal["OBSERVATION"] = "OBSERVATION"
EVENT_LLM_REASONING: Literal["LLM_REASONING"] = "LLM_REASONING"
EVENT_LLM_THINKING: Literal["LLM_THINKING"] = "LLM_THINKING"
EVENT_ACTION: Literal["ACTION"] = "ACTION"
EVENT_REWARD: Literal["REWARD"] = "REWARD"


# --- Input spec ---------------------------------------------------------------


class AgentTask(BaseModel):
    """A task spec handed to the runner.

    Example
    -------
    >>> AgentTask(
    ...     task_id="mc-001",
    ...     natural_language_instruction="Build a 3x3 wooden house",
    ...     success_criteria=["4 walls placed", "roof placed", "door installed"],
    ...     environment="minecraft",
    ...     required_provider_model="claude-sonnet-4-5",
    ... )
    """

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, description="Unique identifier for this task run.")
    natural_language_instruction: str = Field(
        min_length=1,
        description="Plain-English instruction handed to the LLM agent.",
    )
    success_criteria: list[str] = Field(
        default_factory=list,
        description=(
            "Testable criteria that mark the task as complete. Evaluated by the "
            "environment's `step(...)` `done` flag + optional external checker."
        ),
    )
    max_steps: int = Field(
        default=1000,
        ge=1,
        description="Hard cap on agent steps before the runner terminates the run.",
    )
    target_hours: float | None = Field(
        default=None,
        ge=0.0,
        description=(
            "Optional wall-clock target. Runner emits periodic progress events but "
            "only terminates on done / max_steps."
        ),
    )
    environment: str = Field(
        min_length=1,
        description="Environment key, e.g. 'minecraft', 'factorio', 'gym:MountainCar-v0'.",
    )
    required_provider_model: str = Field(
        min_length=1,
        description="LLM model id the runner must use, e.g. 'claude-sonnet-4-5'.",
    )


# --- Per-step record ----------------------------------------------------------


class TrajectoryEntry(BaseModel):
    """One step of the agent's interaction with the environment.

    Captured in-memory by the runner and flushed to disk as one or more
    `TrajectoryEvent` lines (see `to_events(...)`).
    """

    model_config = ConfigDict(extra="forbid")

    step: int = Field(ge=0, description="Zero-based step index within the run.")
    timestamp_sec: float = Field(
        ge=0.0,
        description="Seconds since run start (monotonic clock).",
    )
    observation: str | dict[str, Any] = Field(
        description=(
            "Environment observation, either a serialized summary (str) or a dict of "
            "structured fields. Images MUST be stored by path, not inlined."
        ),
    )
    llm_reasoning: str = Field(
        description="Full text of the LLM's reasoning / chain-of-thought for this step.",
    )
    action: dict[str, Any] = Field(
        description="Environment-specific action dispatched to `env.step(...)`.",
    )
    reward: float | None = Field(
        default=None,
        description="Scalar reward returned by the environment (None if not emitted).",
    )
    success_flag: bool = Field(
        default=False,
        description="True once the environment signals the task is complete.",
    )

    def to_events(self) -> list[TrajectoryEvent]:
        """Expand this entry into the enrichment-compatible event-log lines."""
        events: list[TrajectoryEvent] = [
            TrajectoryEvent(
                timestamp=self.timestamp_sec,
                event_type=EVENT_AGENT_STEP,
                event_args={"step": self.step, "success": self.success_flag},
            ),
            TrajectoryEvent(
                timestamp=self.timestamp_sec,
                event_type=EVENT_OBSERVATION,
                event_args={"value": self.observation},
            ),
            TrajectoryEvent(
                timestamp=self.timestamp_sec,
                event_type=EVENT_LLM_REASONING,
                event_args={"text": self.llm_reasoning},
            ),
            TrajectoryEvent(
                timestamp=self.timestamp_sec,
                event_type=EVENT_ACTION,
                event_args=self.action,
            ),
        ]
        if self.reward is not None:
            events.append(
                TrajectoryEvent(
                    timestamp=self.timestamp_sec,
                    event_type=EVENT_REWARD,
                    event_args={"value": self.reward},
                )
            )
        return events


# --- Terminal summary ---------------------------------------------------------


class TaskResult(BaseModel):
    """Summary written at the end of a run."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    environment: str
    provider_model: str
    total_steps: int = Field(ge=0)
    wall_clock_sec: float = Field(ge=0.0)
    success: bool
    termination_reason: Literal["success", "max_steps", "error", "timeout"]
    final_reward: float | None = None
    error_message: str | None = None
    trajectory_path: str = Field(description="Path to the JSONL trajectory file.")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# --- Wire-format event --------------------------------------------------------


class TrajectoryEvent(BaseModel):
    """One line of the trajectory JSONL output.

    Schema-compatible with `oyster-enrichment` and `gamedata-recorder`'s
    input-log validator: `{timestamp: float, event_type: str, event_args: any}`.
    """

    model_config = ConfigDict(extra="forbid")

    timestamp: float = Field(ge=0.0)
    event_type: str = Field(min_length=1)
    event_args: Any = None

    @field_validator("event_type")
    @classmethod
    def _upper_snake(cls, v: str) -> str:
        # Keep the existing recorder convention (upper-snake) so mixed logs
        # remain greppable; don't force — just reject obvious whitespace.
        if v != v.strip() or not v:
            raise ValueError("event_type must be non-empty and whitespace-trimmed")
        return v


__all__ = [
    "EVENT_ACTION",
    "EVENT_AGENT_STEP",
    "EVENT_END",
    "EVENT_LLM_REASONING",
    "EVENT_LLM_THINKING",
    "EVENT_OBSERVATION",
    "EVENT_REWARD",
    "EVENT_START",
    "AgentTask",
    "TaskResult",
    "TrajectoryEntry",
    "TrajectoryEvent",
]
