#!/usr/bin/env python3
"""recorder_post_pipeline.py — Post-process a finished recorder clip dir.

When the recorder finishes packaging a clip, the resulting tarball lacks the
heavy artefacts demanded by the buyer spec (audio.flac, 1800 .exr depth maps,
MANIFEST.json with sha256 digests).  Producing those during recording would
hold up the GUI and demand torch/numpy in the foreground process, so we
defer them to this script.

This module is a *thin coordinator*: it imports each sibling helper lazily
and skips the corresponding phase if the sibling is not yet shipped or
raises ``ImportError``.  That lets the recorder ship with whatever subset of
the post-processing ecosystem is available — Howard's design rule for the
W31 wave is "never block the tester even if half the pipeline is missing".

Phases (each independently skippable):
    1. Audio  — recorder_audio_postprocess.process_clip(clip_dir)
       (G260; falls back to audio_track_extractor.extract_and_validate)
    2. Depth  — recorder_dav2_runner.run_clip(clip_dir)
       (G275; falls back to depth_anything_smoke.main)
    3. Manifest — recorder_manifest.write_manifest(clip_dir)
       (G262; falls back to generate_manifest.build_manifest)

Usage:
    python3 bin/recorder_post_pipeline.py --clip-dir <dir>
    python3 bin/recorder_post_pipeline.py --clip-dir <dir> --skip-depth

Exit codes:
    0 — pipeline completed (or every phase was skipped because deps missing)
    1 — invalid args / clip dir missing
    2 — at least one phase ran and reported a hard failure
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, List, Optional

logger = logging.getLogger(__name__)

PHASE_AUDIO: str = "audio"
PHASE_DEPTH: str = "depth"
PHASE_MANIFEST: str = "manifest"

# Ordered: audio first (cheap), depth next (expensive), manifest last
# (must observe outputs from the prior two phases).
DEFAULT_PHASES: List[str] = [PHASE_AUDIO, PHASE_DEPTH, PHASE_MANIFEST]


@dataclass
class PhaseResult:
    """Outcome of a single pipeline phase."""

    name: str
    status: str  # "ok" | "skipped" | "failed"
    detail: str = ""
    duration_s: float = 0.0


@dataclass
class PipelineReport:
    """Aggregate report returned by :func:`run_pipeline`."""

    clip_dir: str
    phases: List[PhaseResult] = field(default_factory=list)
    started_at: float = 0.0
    finished_at: float = 0.0

    def hard_failed(self) -> bool:
        """Return True if any phase reported a hard failure."""
        return any(p.status == "failed" for p in self.phases)

    def to_dict(self) -> dict:
        """Serialise the report to a JSON-friendly dict."""
        return {
            "clip_dir": self.clip_dir,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "phases": [
                {
                    "name": p.name,
                    "status": p.status,
                    "detail": p.detail,
                    "duration_s": round(p.duration_s, 3),
                } for p in self.phases
            ],
        }


def _try_import(module_name: str) -> Optional[Any]:
    """Import ``module_name`` lazily, returning None if unavailable.

    Tries the bare name first (recorder runtime, with ``bin/`` on sys.path)
    then ``bin.<name>`` (test harness with repo root on sys.path).  ``None``
    is the universal "phase skipped" signal in this orchestrator; callers
    must not treat ImportError as fatal — the W31 contract is to keep
    coordinating whatever phases ARE available.
    """
    last_exc: Optional[Exception] = None
    for candidate in (module_name, f"bin.{module_name}"):
        try:
            return importlib.import_module(candidate)
        except (ImportError, ModuleNotFoundError) as exc:
            last_exc = exc
            continue
    logger.info("Sibling %s not available: %s — skipping phase",
                module_name, last_exc)
    return None


def _run_phase(name: str, fn: Callable[[], str]) -> PhaseResult:
    """Run a phase callable, catching exceptions and timing the result."""
    started = time.monotonic()
    try:
        detail = fn() or ""
        return PhaseResult(name=name, status="ok", detail=detail,
                           duration_s=time.monotonic() - started)
    except Exception as exc:  # noqa: BLE001 — boundary catch by design
        logger.warning("Phase %s failed: %s", name, exc, exc_info=True)
        return PhaseResult(name=name, status="failed", detail=str(exc),
                           duration_s=time.monotonic() - started)


def run_audio_phase(clip_dir: Path) -> PhaseResult:
    """Run audio post-processing if a sibling helper is available."""
    primary = _try_import("recorder_audio_postprocess")
    if primary is not None and hasattr(primary, "process_clip"):
        return _run_phase(PHASE_AUDIO,
                          lambda: str(primary.process_clip(clip_dir)))
    fallback = _try_import("audio_track_extractor")
    if fallback is not None and hasattr(fallback, "extract_and_validate"):
        video = clip_dir / "video.mp4"
        if not video.exists():
            return PhaseResult(name=PHASE_AUDIO, status="skipped",
                               detail="video.mp4 missing")
        return _run_phase(
            PHASE_AUDIO,
            lambda: str(fallback.extract_and_validate(video, clip_dir)),
        )
    return PhaseResult(name=PHASE_AUDIO, status="skipped",
                       detail="no audio sibling available")


def run_depth_phase(clip_dir: Path) -> PhaseResult:
    """Run depth-fill if a sibling helper is available."""
    primary = _try_import("recorder_dav2_runner")
    if primary is not None and hasattr(primary, "run_clip"):
        return _run_phase(PHASE_DEPTH,
                          lambda: str(primary.run_clip(clip_dir)))
    # The smoke test only emits a single .exr; treat its presence as a
    # diagnostic rather than a real depth fill.
    fallback = _try_import("depth_anything_smoke")
    if fallback is not None and hasattr(fallback, "main"):
        return PhaseResult(name=PHASE_DEPTH, status="skipped",
                           detail="only smoke runner available; "
                                  "real depth fill needs G275")
    return PhaseResult(name=PHASE_DEPTH, status="skipped",
                       detail="no depth sibling available")


def run_manifest_phase(clip_dir: Path) -> PhaseResult:
    """Run manifest regeneration if a sibling helper is available."""
    primary = _try_import("recorder_manifest")
    if primary is not None and hasattr(primary, "write_manifest"):
        return _run_phase(PHASE_MANIFEST,
                          lambda: str(primary.write_manifest(clip_dir)))
    fallback = _try_import("generate_manifest")
    if fallback is not None and hasattr(fallback, "build_manifest"):
        out_path = clip_dir / "MANIFEST.json"

        def _build() -> str:
            manifest = fallback.build_manifest(str(clip_dir))
            out_path.write_text(json.dumps(manifest, indent=2,
                                           sort_keys=True))
            return str(out_path)
        return _run_phase(PHASE_MANIFEST, _build)
    return PhaseResult(name=PHASE_MANIFEST, status="skipped",
                       detail="no manifest sibling available")


def run_pipeline(clip_dir: Path,
                 skip: Optional[List[str]] = None) -> PipelineReport:
    """Run the full post-pipeline against ``clip_dir``.

    Args:
        clip_dir: The recorder clip directory containing video.mp4 +
            placeholder companions.
        skip: Optional list of phase names to skip (one of
            ``{"audio", "depth", "manifest"}``).

    Returns:
        :class:`PipelineReport` describing each phase's outcome.
    """
    skip_set = set(skip or [])
    report = PipelineReport(clip_dir=str(clip_dir),
                            started_at=time.time())
    runners = {
        PHASE_AUDIO: run_audio_phase,
        PHASE_DEPTH: run_depth_phase,
        PHASE_MANIFEST: run_manifest_phase,
    }
    for phase in DEFAULT_PHASES:
        if phase in skip_set:
            report.phases.append(PhaseResult(name=phase, status="skipped",
                                             detail="--skip-" + phase))
            continue
        report.phases.append(runners[phase](clip_dir))
    report.finished_at = time.time()
    return report


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--clip-dir", type=Path, required=True,
                        help="Recorder clip directory to post-process")
    parser.add_argument("--skip-audio", action="store_true",
                        help="Skip audio extraction phase")
    parser.add_argument("--skip-depth", action="store_true",
                        help="Skip depth-fill phase")
    parser.add_argument("--skip-manifest", action="store_true",
                        help="Skip manifest regeneration phase")
    parser.add_argument("--report-json", type=Path, default=None,
                        help="Optional path for writing the pipeline report")
    parser.add_argument("--verbose", action="store_true",
                        help="Enable DEBUG logging")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point."""
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    clip_dir: Path = args.clip_dir.resolve()
    if not clip_dir.exists() or not clip_dir.is_dir():
        logger.error("Clip dir not found: %s", clip_dir)
        return 1
    skip: List[str] = []
    if args.skip_audio:
        skip.append(PHASE_AUDIO)
    if args.skip_depth:
        skip.append(PHASE_DEPTH)
    if args.skip_manifest:
        skip.append(PHASE_MANIFEST)
    report = run_pipeline(clip_dir, skip=skip)
    if args.report_json:
        args.report_json.write_text(json.dumps(report.to_dict(), indent=2))
    for phase in report.phases:
        logger.info("phase=%s status=%s detail=%s duration=%.2fs",
                    phase.name, phase.status, phase.detail, phase.duration_s)
    return 2 if report.hard_failed() else 0


if __name__ == "__main__":
    sys.exit(main())
