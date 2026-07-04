"""Phase 1 multi-stream writer for the Minecraft trajectory product.

The L4 spec (`docs/MINECRAFT_TRAJECTORY_SPEC.md`) calls for four
separate output files per session, all sharing a single wall-clock
anchor:

    trajectory_<id>/
    ├── manifest.json        ← session metadata + alignment proof
    ├── cot.jsonl            ← LLM thinking + reasoning + actions
    ├── metadata.jsonl       ← per-tick / per-step Minecraft world state
    └── inputs.jsonl         ← per-step bot actions (move / dig / look / chat)

Phase 1 INTENTIONALLY excludes `video.mp4` + `frames.jsonl` — those
land in Phase 2 with the OBS spectator pipeline.

Why a separate module instead of folding into TrajectoryLogger?
---------------------------------------------------------------
The existing `TrajectoryLogger` writes a single mixed-event JSONL that
matches the `gamedata-recorder` schema. That contract still holds for
the generic L4 runner. Minecraft Phase 1 layers ADDITIONAL per-stream
files on top: same events, written N more times into stream-specific
files.

This module owns:
  1. The demuxer (`MinecraftStreamWriter`) that takes a stream of
     `TrajectoryEvent` objects and decides which files to fan them out
     into.
  2. The manifest writer that stamps `session_start_wall_clock_utc` and
     finalizes counts at the end of the session.

Used by `cli.py`'s `run-mc` command. Pure-Python, no test dependencies.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

UTC = timezone.utc

UTC = UTC
from pathlib import Path
from typing import Any

from oyster_agent_runner.schema import (
    EVENT_ACTION,
    EVENT_AGENT_STEP,
    EVENT_END,
    EVENT_LLM_REASONING,
    EVENT_LLM_THINKING,
    EVENT_OBSERVATION,
    EVENT_REWARD,
    EVENT_START,
    TrajectoryEvent,
)

COT_FILENAME = "cot.jsonl"
METADATA_FILENAME = "metadata.jsonl"
INPUTS_FILENAME = "inputs.jsonl"
MANIFEST_FILENAME = "manifest.json"

# Event types that flow into each stream.
COT_EVENT_TYPES = frozenset(
    {
        EVENT_LLM_THINKING,
        EVENT_LLM_REASONING,
        EVENT_ACTION,
        EVENT_START,
        EVENT_END,
    }
)
# Metadata is the world-state ground truth — observations + start/end markers
# (so the stream is self-contained for buyers).
METADATA_EVENT_TYPES = frozenset(
    {
        EVENT_OBSERVATION,
        EVENT_AGENT_STEP,
        EVENT_REWARD,
        EVENT_START,
        EVENT_END,
    }
)
# Inputs = the action stream. We keep START/END markers for self-containment.
INPUT_EVENT_TYPES = frozenset(
    {
        EVENT_ACTION,
        EVENT_START,
        EVENT_END,
    }
)


class MinecraftStreamWriter:
    """Append-only multi-stream JSONL writer for Phase 1 trajectories.

    Usage
    -----
    >>> with MinecraftStreamWriter(Path("trajectories/run-001")) as w:
    ...     w.write(TrajectoryEvent(timestamp=0.0, event_type="START", event_args={...}))
    ...     w.write(TrajectoryEvent(timestamp=0.1, event_type="LLM_THINKING", event_args={...}))
    ...     # ...
    ...     w.finalize_manifest(task_id="MC-tutorial-001", ...)
    """

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.cot_path = self.output_dir / COT_FILENAME
        self.metadata_path = self.output_dir / METADATA_FILENAME
        self.inputs_path = self.output_dir / INPUTS_FILENAME
        self.manifest_path = self.output_dir / MANIFEST_FILENAME

        # Counters drive the manifest's alignment block at finalize time.
        self.cot_event_count = 0
        self.metadata_event_count = 0
        self.input_event_count = 0
        self._max_timestamp_sec = 0.0
        self._cot_fh: Any = None
        self._metadata_fh: Any = None
        self._inputs_fh: Any = None
        self._open_called = False
        self._closed = False

    # Context manager ---------------------------------------------------------

    def __enter__(self) -> MinecraftStreamWriter:
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def open(self) -> None:
        """Open output files for writing.

        Creates the output directory and line-buffered file handles
        for CoT, metadata, and inputs streams. Idempotent: subsequent
        calls after the first are no-ops.

        Raises:
            OSError: If directory creation fails.
        """
        if self._open_called:
            return
        self._open_called = True
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # Line-buffered so partial runs leave usable logs on crash.
        self._cot_fh = self.cot_path.open("w", encoding="utf-8", buffering=1)
        self._metadata_fh = self.metadata_path.open("w", encoding="utf-8", buffering=1)
        self._inputs_fh = self.inputs_path.open("w", encoding="utf-8", buffering=1)

    def close(self) -> None:
        """Close all output file handles and mark the writer as closed.

        Safely handles already-closed file handles by catching exceptions.
        After closing, all internal file handle references are set to None.

        Returns:
            None.
        """
        if self._closed:
            return
        self._closed = True
        for fh in (self._cot_fh, self._metadata_fh, self._inputs_fh):
            if fh is not None:
                try:
                    fh.flush()
                    fh.close()
                except Exception:
                    # Already-closed handles raise; ignore on the terminal path.
                    pass
        self._cot_fh = None
        self._metadata_fh = None
        self._inputs_fh = None

    # Writers -----------------------------------------------------------------

    def write(self, event: TrajectoryEvent) -> None:
        """Demux a single event into all streams it belongs to.

        An event can land in multiple streams (e.g. ACTION goes to BOTH
        cot.jsonl AND inputs.jsonl) — the buyer of the metadata-only
        bundle should not need to read cot.jsonl to know what happened.
        """
        if not self._open_called or self._closed:
            raise RuntimeError(
                "MinecraftStreamWriter is not open; use it as a context manager or call open()."
            )
        if event.timestamp > self._max_timestamp_sec:
            self._max_timestamp_sec = event.timestamp

        line = event.model_dump_json() + "\n"
        if event.event_type in COT_EVENT_TYPES:
            self._cot_fh.write(line)
            self.cot_event_count += 1
        if event.event_type in METADATA_EVENT_TYPES:
            self._metadata_fh.write(line)
            self.metadata_event_count += 1
        if event.event_type in INPUT_EVENT_TYPES:
            self._inputs_fh.write(line)
            self.input_event_count += 1

    def write_metadata_tick(self, observation: dict[str, Any], timestamp_sec: float) -> None:
        """Emit a TICK metadata line.

        Used by the Mineflayer environment's `metadata_callback` hook to
        write per-observation snapshots into `metadata.jsonl` — even
        observations that don't correspond to a runner step (for example,
        the initial spawn observation).

        TICK is a non-canonical event_type — recipients tolerate unknown
        types per the gamedata-recorder validator contract.
        """
        if not self._open_called or self._closed:
            raise RuntimeError("MinecraftStreamWriter is not open")
        if timestamp_sec > self._max_timestamp_sec:
            self._max_timestamp_sec = timestamp_sec
        ev = TrajectoryEvent(
            timestamp=timestamp_sec,
            event_type="TICK",
            event_args=observation,
        )
        self._metadata_fh.write(ev.model_dump_json() + "\n")
        self.metadata_event_count += 1

    # Manifest ----------------------------------------------------------------

    def finalize_manifest(
        self,
        *,
        task_id: str,
        model: str,
        provider: str,
        environment: str,
        anchor_utc: datetime,
        success: bool,
        termination_reason: str,
        total_steps: int,
        wall_clock_sec: float,
        thinking_budget_tokens: int | None,
        license: str = "train-only",
        phase: int = 1,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Write `manifest.json` with all session-level metadata.

        Phase 1 omits the video-related fields (`video_fps`,
        `video_frame_count`, `max_observed_drift_ms`) — they're filled in
        by Phase 2's video pipeline.
        """
        manifest: dict[str, Any] = {
            "spec_version": 1,
            "phase": phase,
            "task_id": task_id,
            "session_start_wall_clock_utc": anchor_utc.astimezone(UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "environment": environment,
            "provider": provider,
            "model": model,
            "license": license,
            "thinking_budget_tokens": thinking_budget_tokens,
            "result": {
                "success": success,
                "termination_reason": termination_reason,
                "total_steps": total_steps,
                "wall_clock_sec": wall_clock_sec,
            },
            "alignment": {
                "anchor_utc": anchor_utc.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                # Phase 1 has no video — these are filled in Phase 2.
                "video_fps": None,
                "video_frame_count": None,
                "max_observed_drift_ms": None,
                # Counts buyers can sanity-check against.
                "cot_event_count": self.cot_event_count,
                "metadata_event_count": self.metadata_event_count,
                "input_event_count": self.input_event_count,
                "max_timestamp_sec": self._max_timestamp_sec,
            },
            "outputs": {
                "cot": COT_FILENAME,
                "metadata": METADATA_FILENAME,
                "inputs": INPUTS_FILENAME,
                "video": None,  # Phase 2
                "frames": None,  # Phase 2
            },
        }
        if extra:
            manifest["extra"] = extra
        # Atomic-ish write: write to a temp neighbor, rename. We don't go
        # full POSIX rename because in Phase 1 the manifest is written
        # last and the operator's cleanup is to delete the directory; a
        # crash between rename + flush is no worse than a crash before
        # finalize_manifest is called.
        self.manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )


__all__ = [
    "COT_EVENT_TYPES",
    "COT_FILENAME",
    "INPUTS_FILENAME",
    "INPUT_EVENT_TYPES",
    "MANIFEST_FILENAME",
    "METADATA_EVENT_TYPES",
    "METADATA_FILENAME",
    "MinecraftStreamWriter",
]
