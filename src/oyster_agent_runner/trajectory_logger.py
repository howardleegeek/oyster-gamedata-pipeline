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
import json
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

    def append(self, entry: TrajectoryEntry, frame_png: bytes | None = None) -> None:
        """Expand `entry` into events and append them.

        If `frame_png` is provided and `write_frames` is True, the frame
        is persisted under `frames/<step>.png` and a RENDER event with
        `{path, sha256}` is emitted so downstream consumers can re-attach
        depth / pose without touching the agent.
        """
        if entry.timestamp_sec > self._last_step_timestamp:
            self._last_step_timestamp = entry.timestamp_sec
        for ev in entry.to_events():
            self._write_event(ev)

        if frame_png is not None and self.write_frames:
            frame_path = self.frames_dir / f"{entry.step:06d}.png"
            frame_path.write_bytes(frame_png)
            self._write_event(
                TrajectoryEvent(
                    timestamp=entry.timestamp_sec,
                    event_type="RENDER",
                    event_args={
                        # Relative path keeps the trajectory portable.
                        "path": f"{FRAMES_SUBDIR}/{entry.step:06d}.png",
                        "sha256": hashlib.sha256(frame_png).hexdigest(),
                        "bytes": len(frame_png),
                    },
                )
            )

    def end(
        self,
        *,
        success: bool,
        total_steps: int,
        reason: str,
        timestamp_sec: float | None = None,
    ) -> None:
        """Emit the END marker with run summary.

        `timestamp_sec` should be the final monotonic offset (seconds since
        run start). If None, the last observed step timestamp is reused so
        the marker stays monotonic with the step events.
        """
        stamp = timestamp_sec if timestamp_sec is not None else self._last_step_timestamp
        self._write_event(
            TrajectoryEvent(
                timestamp=stamp,
                event_type=EVENT_END,
                event_args={
                    "success": success,
                    "total_steps": total_steps,
                    "reason": reason,
                },
            )
        )

    # Internal ----------------------------------------------------------------

    def _write_event(self, event: TrajectoryEvent) -> None:
        if self._fh is None:
            raise RuntimeError(
                "TrajectoryLogger is not open. Use it as a context manager or call __enter__."
            )
        line = event.model_dump_json()
        self._fh.write(line + "\n")


__all__ = ["FRAMES_SUBDIR", "TRAJECTORY_FILENAME", "TrajectoryLogger"]
