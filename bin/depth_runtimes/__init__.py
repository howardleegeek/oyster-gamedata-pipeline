"""bin/depth_runtimes — multi-backend depth inference (rc15.9 self-heal v2).

Each runtime exposes a uniform interface:
    infer_depth_for_video(video_path, output_dir, *, model_variant='vits',
                          progress_callback=None, should_skip=None) -> dict[int, str]

Returns dict[frame_idx → sha256 hex of EXR file]. Raises on failure.

Failure raises a runtime-specific exception subclass that the caller (recorder
fallback chain) classifies + retries with the next runtime in the tier list.
"""
from __future__ import annotations


class DepthRuntimeError(RuntimeError):
    """Base class so all runtime failures are catchable uniformly."""

    runtime_name: str = "unknown"


class DepthRuntimeUnavailable(DepthRuntimeError):
    """Runtime not installed / not importable. Skip to next tier."""


class DepthRuntimeOpCompat(DepthRuntimeError):
    """Runtime loaded but failed at inference time on op-dispatch (model
    architecture × runtime backend incompatibility)."""


class DepthRuntimeOOM(DepthRuntimeError):
    """Hardware ran out of memory (UMA / VRAM / system RAM)."""


class DepthRuntimeDriver(DepthRuntimeError):
    """Driver-level error (DML/CUDA init failed, version mismatch)."""
