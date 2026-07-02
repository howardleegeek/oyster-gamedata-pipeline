#!/usr/bin/env python3
"""Stress test: simulate 2-hour scene with 30-min clip cap.

Confirms scene_id rotation and clip cap enforcement under sustained
capture load. Uses only stdlib so it runs on any vendor environment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Clip:
    """A single captured clip within a scene."""
    scene_id: str
    clip_index: int
    start_offset_sec: float
    end_offset_sec: float
    frame_count: int
    duration_sec: float


@dataclass
class CaptureSession:
    """Aggregates all clips produced during a simulated capture run."""
    clips: List[Clip] = field(default_factory=list)
    total_frames: int = 0
    total_duration_sec: float = 0.0

    def add(self, clip: Clip) -> None:
        """Append a clip and update aggregate counters."""
        self.clips.append(clip)
        self.total_frames += clip.frame_count
        self.total_duration_sec += clip.duration_sec


def _make_scene_id(index: int, seed: int) -> str:
    """Generate a deterministic scene identifier via SHA-256 prefix."""
    return hashlib.sha256(f"scene_seed{seed}_idx{index}".encode()).hexdigest()[:12]


def simulate_capture(
    duration_minutes: float, clip_cap_minutes: float, fps: int, seed: int
) -> CaptureSession:
    """Simulate a long capture session with enforced clip caps."""
    session = CaptureSession()
    total_sec = duration_minutes * 60.0
    cap_sec = clip_cap_minutes * 60.0
    scene_idx, elapsed = 0, 0.0

    while elapsed < total_sec:
        clip_dur = min(cap_sec, total_sec - elapsed)
        frames = int(clip_dur * fps)
        clip = Clip(
            scene_id=_make_scene_id(scene_idx, seed),
            clip_index=scene_idx,
            start_offset_sec=elapsed,
            end_offset_sec=elapsed + clip_dur,
            frame_count=frames,
            duration_sec=clip_dur,
        )
        session.add(clip)
        logger.debug("clip %d scene=%s dur=%.1f min frames=%d",
                     scene_idx, clip.scene_id, clip_dur / 60.0, frames)
        elapsed += clip_dur
        scene_idx += 1

    return session


def validate_session(
    session: CaptureSession, duration_minutes: float, clip_cap_minutes: float, fps: int
) -> List[str]:
    """Validate that the capture session respects all constraints.

    Checks: total duration, clip cap, scene_id uniqueness, contiguity.
    """
    errors: List[str] = []
    cap_sec = clip_cap_minutes * 60.0
    expected_total = duration_minutes * 60.0

    if abs(session.total_duration_sec - expected_total) > 0.001:
        errors.append(f"Total duration mismatch: expected {expected_total:.2f}s, "
                      f"got {session.total_duration_sec:.2f}s")

    for clip in session.clips:
        if clip.duration_sec > cap_sec + 0.001:
            errors.append(f"Clip {clip.clip_index} exceeds cap: "
                         f"{clip.duration_sec:.2f}s > {cap_sec:.2f}s")

    scene_ids = [c.scene_id for c in session.clips]
    if len(scene_ids) != len(set(scene_ids)):
        errors.append("Duplicate scene_ids detected - rotation failed")

    for i in range(1, len(session.clips)):
        prev_clip, curr_clip = session.clips[i - 1], session.clips[i]
        if abs(prev_clip.end_offset_sec - curr_clip.start_offset_sec) > 0.001:
            errors.append(f"Non-contiguous clips at index {i}")

    for clip in session.clips:
        expected_frames = int(clip.duration_sec * fps)
        if clip.frame_count != expected_frames:
            errors.append(f"Clip {clip.clip_index} frame count mismatch")

    return errors


def write_report(
    session: CaptureSession, output_dir: Path, duration_minutes: float, clip_cap_minutes: float
) -> Path:
    """Write a JSON report of the capture session."""
    report = {
        "summary": {
            "total_clips": len(session.clips),
            "total_frames": session.total_frames,
            "total_duration_sec": session.total_duration_sec,
            "duration_minutes": duration_minutes,
            "clip_cap_minutes": clip_cap_minutes,
        },
        "clips": [
            {
                "scene_id": c.scene_id,
                "clip_index": c.clip_index,
                "start_offset_sec": c.start_offset_sec,
                "end_offset_sec": c.end_offset_sec,
                "frame_count": c.frame_count,
                "duration_sec": c.duration_sec,
            }
            for c in session.clips
        ],
    }
    report_path = output_dir / "stress_test_report.json"
    report_path.write_text(json.dumps(report, indent=2))
    return report_path


def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point for the stress test."""
    parser = argparse.ArgumentParser(
        description="Stress test: simulate 2-hour capture with 30-min clip cap"
    )
    parser.add_argument("--duration-minutes", type=float, default=120.0,
                       help="Total capture duration in minutes (default: 120)")
    parser.add_argument("--clip-cap-minutes", type=float, default=30.0,
                       help="Maximum clip duration in minutes (default: 30)")
    parser.add_argument("--fps", type=int, default=30,
                       help="Frames per second for simulation (default: 30)")
    parser.add_argument("--seed", type=int, default=42,
                       help="Seed for deterministic scene_id generation (default: 42)")
    parser.add_argument("--output-dir", type=Path, default=None,
                       help="Output directory for report (default: temp dir)")
    parser.add_argument("--verbose", "-v", action="store_true",
                       help="Enable debug logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s"
    )

    logger.info("Starting stress test: %.1f min capture, %.1f min clip cap, %d fps",
                args.duration_minutes, args.clip_cap_minutes, args.fps)

    session = simulate_capture(args.duration_minutes, args.clip_cap_minutes, args.fps, args.seed)
    logger.info("Simulation complete: %d clips, %d frames, %.2f sec total",
                len(session.clips), session.total_frames, session.total_duration_sec)

    errors = validate_session(session, args.duration_minutes, args.clip_cap_minutes, args.fps)
    if errors:
        logger.error("Validation failed with %d errors:", len(errors))
        for err in errors:
            logger.error("  - %s", err)
        return 1

    logger.info("All validations passed")

    output_dir = args.output_dir or Path(tempfile.mkdtemp(prefix="stress_test_"))
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = write_report(session, output_dir, args.duration_minutes, args.clip_cap_minutes)
    logger.info("Report written to: %s", report_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
