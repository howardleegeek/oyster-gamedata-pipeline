#!/usr/bin/env python3
"""PRD p6 #5: Verify stationary frames over 5s trigger clip stop.

This script validates the stationary-frame cutoff logic defined in the PRD:
when consecutive frames show no meaningful motion for >= 5 seconds, the
recording pipeline must stop the current clip automatically.

Usage:
    python3 bin/prd_test_stationary_threshold.py [--fps FPS] [--threshold SECS]
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_FPS: int = 30
DEFAULT_THRESHOLD_SEC: float = 5.0
MIN_FRAME_DIFF: float = 1e-3  # below this = "no motion"


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def is_stationary(frame_diff: float, epsilon: float = MIN_FRAME_DIFF) -> bool:
    """Return True when *frame_diff* indicates no meaningful motion."""
    return frame_diff < epsilon


def frames_to_seconds(frame_count: int, fps: int) -> float:
    """Convert a frame count to wall-clock seconds at the given *fps*."""
    if fps <= 0:
        raise ValueError(f"fps must be positive, got {fps}")
    return frame_count / fps


def detect_stationary_cutoff(
    frame_diffs: Sequence[float],
    fps: int = DEFAULT_FPS,
    threshold_sec: float = DEFAULT_THRESHOLD_SEC,
) -> int | None:
    """Find the first frame index where stationary duration exceeds *threshold_sec*.

    Parameters
    ----------
    frame_diffs :
        Per-frame motion metric (e.g. mean absolute pixel difference).
    fps :
        Frames per second of the source video.
    threshold_sec :
        Minimum stationary duration (seconds) that triggers a clip stop.

    Returns
    -------
    int | None
        The frame index at which the cutoff fires, or ``None`` if the
        threshold is never reached.
    """
    threshold_frames = int(threshold_sec * fps)
    consecutive_stationary = 0

    for idx, diff in enumerate(frame_diffs):
        if is_stationary(diff):
            consecutive_stationary += 1
            if consecutive_stationary >= threshold_frames:
                return idx
        else:
            consecutive_stationary = 0

    return None


# ---------------------------------------------------------------------------
# Test scenarios
# ---------------------------------------------------------------------------


def _run_tests(fps: int, threshold_sec: float) -> int:
    """Execute built-in validation scenarios.  Returns 0 on success, 1 on failure."""
    threshold_frames = int(threshold_sec * fps)
    failures = 0

    # Scenario 1: stationary from the start — cutoff should fire at threshold_frames - 1
    diffs_always_stationary = [0.0] * (threshold_frames + 10)
    result = detect_stationary_cutoff(diffs_always_stationary, fps, threshold_sec)
    expected = threshold_frames - 1
    if result != expected:
        print(f"FAIL scenario 1: expected cutoff at frame {expected}, got {result}")
        failures += 1
    else:
        print(f"PASS scenario 1: cutoff at frame {result} (stationary from start)")

    # Scenario 2: motion throughout — no cutoff
    diffs_always_moving = [1.0] * 200
    result = detect_stationary_cutoff(diffs_always_moving, fps, threshold_sec)
    if result is not None:
        print(f"FAIL scenario 2: expected None, got cutoff at frame {result}")
        failures += 1
    else:
        print("PASS scenario 2: no cutoff when frames are always moving")

    # Scenario 3: stationary burst below threshold — no cutoff
    short_burst = [1.0] * 50 + [0.0] * (threshold_frames - 1) + [1.0] * 50
    result = detect_stationary_cutoff(short_burst, fps, threshold_sec)
    if result is not None:
        print(f"FAIL scenario 3: expected None, got cutoff at frame {result}")
        failures += 1
    else:
        print("PASS scenario 3: no cutoff for stationary burst below threshold")

    # Scenario 4: stationary burst exactly at threshold — cutoff fires
    exact_burst = [1.0] * 50 + [0.0] * threshold_frames + [1.0] * 50
    result = detect_stationary_cutoff(exact_burst, fps, threshold_sec)
    expected = 50 + threshold_frames - 1
    if result != expected:
        print(f"FAIL scenario 4: expected cutoff at frame {expected}, got {result}")
        failures += 1
    else:
        print(f"PASS scenario 4: cutoff at frame {result} (exact threshold)")

    # Scenario 5: two short bursts separated by motion — no cutoff
    split_burst = [0.0] * (threshold_frames // 2) + [1.0] * 5 + [0.0] * (threshold_frames // 2)
    result = detect_stationary_cutoff(split_burst, fps, threshold_sec)
    if result is not None:
        print(f"FAIL scenario 5: expected None, got cutoff at frame {result}")
        failures += 1
    else:
        print("PASS scenario 5: no cutoff for split stationary bursts")

    return failures


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point — parse args, run tests, report results."""
    parser = argparse.ArgumentParser(
        description="Verify stationary-frame cutoff triggers clip stop at >= 5 s."
    )
    parser.add_argument(
        "--fps",
        type=int,
        default=DEFAULT_FPS,
        help=f"Frames per second (default: {DEFAULT_FPS})",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD_SEC,
        help=f"Stationary threshold in seconds (default: {DEFAULT_THRESHOLD_SEC})",
    )
    args = parser.parse_args(argv)

    print(f"Running stationary-threshold tests  fps={args.fps}  threshold={args.threshold}s")
    print(f"  → threshold in frames: {int(args.threshold * args.fps)}")
    print()

    failures = _run_tests(args.fps, args.threshold)

    print()
    if failures:
        print(f"FAILED — {failures} scenario(s) did not pass.")
        return 1
    print("ALL SCENARIOS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
