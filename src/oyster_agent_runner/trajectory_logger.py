"""Emit JSONL trajectories compatible with oyster-enrichment.

The file format is one JSON object per line, each matching the envelope
`{timestamp: float, event_type: str, event_args: any}`. This is exactly
the shape consumed by `gamedata-recorder/check_input_log.py` and the
enrichment pipeline's ingest layer, so existing tooling ingests L4
trajectories unchanged.

Frames can optionally be persisted next to the JSONL file under a
`frames/` subdirectory — each `RENDER` event then references the frame
path rather than embedding raw bytes.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from oyster_agent_runner.schema import (
    EVENT_END,
    EVENT_START,
    TrajectoryEntry,
    TrajectoryEvent,
)

FRAMES_SUBDIR = "frames"
TRAJECTORY_FILENAME = "trajectory.jsonl"


class TrajectoryLogger:
    """Append-only JSONL writer for a single run.

    Usage
    -----
    >>> with TrajectoryLogger(Path("runs/run-001")) as log:
    ...     log.start(task_id="mc-001", environment="minecraft",
    ...               provider_model="claude-sonnet-4-5")
    ...     # ... append entries ...
    ...     log.end(success=True, total_steps=42)
    """

    def __init__(self, output_dir: Path, *, write_frames: bool = True) -> None:
        self.output_dir = Path(output_dir)
        self.write_frames = write_frames
        self.trajectory_path = self.output_dir / TRAJECTORY_FILENAME
        self.frames_dir = self.output_dir / FRAMES_SUBDIR
        self._fh: Any = None
        # Tracks the highest timestamp seen so `end()` can stay monotonic
        # without the caller having to thread the wall clock through.
        self._last_step_timestamp: float = 0.0

    # Context manager ---------------------------------------------------------

    def __enter__(self) -> TrajectoryLogger:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if self.write_frames:
            self.frames_dir.mkdir(parents=True, exist_ok=True)
        # Line-buffered so partial runs leave usable logs on crash.
        self._fh = self.trajectory_path.open("w", encoding="utf-8", buffering=1)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def close(self) -> None:
        """Flush and close the trajectory file handle if open.

        Safe to call multiple times; subsequent calls are no-ops.
        Called automatically by the context manager ``__exit__`` method.
        """
        if self._fh is not None:
            self._fh.flush()
            self._fh.close()
            self._fh = None

    # Writers -----------------------------------------------------------------

    def start(
        self,
        *,
        task_id: str,
        environment: str,
        provider_model: str,
        timestamp_sec: float = 0.0,
    ) -> None:
        """Emit the START marker (matches recorder's metadata marker)."""
        self._write_event(
            TrajectoryEvent(
                timestamp=timestamp_sec,
                event_type=EVENT_START,
                event_args={
                    "task_id": task_id,
                    "environment": environment,
                    "provider_model": provider_model,
                },
            )
        )

    def end(
        self,
        *,
        success: bool,
        total_steps: int,
        timestamp_sec: float | None = None,
    ) -> None:
        """Emit the END marker with success flag and step count."""
        ts = self._monotonic_timestamp(timestamp_sec)
        self._write_event(
            TrajectoryEvent(
                timestamp=ts,
                event_type=EVENT_END,
                event_args={
                    "success": success,
                    "total_steps": total_steps,
                },
            )
        )

    def step(
        self,
        *,
        step_id: int,
        observation: str,
        action: str,
        reward: float,
        timestamp_sec: float | None = None,
    ) -> None:
        """Append a STEP event with observation, action, and reward."""
        ts = self._monotonic_timestamp(timestamp_sec)
        self._write_event(
            TrajectoryEvent(
                timestamp=ts,
                event_type="STEP",
                event_args={
                    "step_id": step_id,
                    "observation": observation,
                    "action": action,
                    "reward": reward,
                },
            )
        )

    def render(
        self,
        *,
        step_id: int,
        frame_bytes: bytes,
        timestamp_sec: float | None = None,
    ) -> str | None:
        """Persist a frame and emit a RENDER event.

        If ``write_frames`` is False, returns None and emits nothing.
        Otherwise, writes the frame to ``frames/<step_id>.png`` and returns
        the relative path string for the caller to embed in the observation.
        """
        if not self.write_frames:
            return None
        ts = self._monotonic_timestamp(timestamp_sec)
        frame_path = self.frames_dir / f"{step_id:06d}.png"
        # Avoid re-hashing the same frame if the caller is being lazy.
        frame_path.write_bytes(frame_bytes)
        rel_path = f"{FRAMES_SUBDIR}/{step_id:06d}.png"
        self._write_event(
            TrajectoryEvent(
                timestamp=ts,
                event_type="RENDER",
                event_args={
                    "step_id": step_id,
                    "frame_path": rel_path,
                    "sha256": hashlib.sha256(frame_bytes).hexdigest(),
                },
            )
        )
        return rel_path

    def tool_call(
        self,
        *,
        step_id: int,
        tool_name: str,
        args: dict[str, Any],
        timestamp_sec: float | None = None,
    ) -> None:
        """Emit a TOOL_CALL event for a tool invocation."""
        ts = self._monotonic_timestamp(timestamp_sec)
        self._write_event(
            TrajectoryEvent(
                timestamp=ts,
                event_type="TOOL_CALL",
                event_args={
                    "step_id": step_id,
                    "tool_name": tool_name,
                    "args": args,
                },
            )
        )

    def tool_result(
        self,
        *,
        step_id: int,
        tool_name: str,
        result: Any,
        timestamp_sec: float | None = None,
    ) -> None:
        """Emit a TOOL_RESULT event with the tool's return value."""
        ts = self._monotonic_timestamp(timestamp_sec)
        self._write_event(
            TrajectoryEvent(
                timestamp=ts,
                event_type="TOOL_RESULT",
                event_args={
                    "step_id": step_id,
                    "tool_name": tool_name,
                    "result": result,
                },
            )
        )

    # Internal helpers --------------------------------------------------------

    def _monotonic_timestamp(self, ts: float | None) -> float:
        """Return a timestamp >= all previously emitted timestamps.

        If ``ts`` is None, returns ``_last_step_timestamp`` unchanged.
        If ``ts`` is less than the last timestamp, returns the last timestamp
        (clamping to preserve monotonicity). Otherwise returns ``ts`` and
        updates the internal high-water mark.
        """
        if ts is None:
            return self._last_step_timestamp
        if ts < self._last_step_timestamp:
            return self._last_step_timestamp
        self._last_step_timestamp = ts
        return ts

    def _write_event(self, event: TrajectoryEvent) -> None:
        """Serialize and write a TrajectoryEvent to the JSONL file."""
        if self._fh is None:
            raise RuntimeError("TrajectoryLogger not open (use as context manager)")
        entry = TrajectoryEntry(event=event)
        # Pydantic v2: model_dump_json() for compact JSON output.
        self._fh.write(entry.model_dump_json() + "\n")