#!/usr/bin/env python3
"""
Sync Tolerance Gate - Audit frame ↔ tick time alignment precision.

Reports distribution across 100ms / 50ms / 10ms buckets.
"""

import argparse
import bisect
import json
import sys
from pathlib import Path
from typing import List, Tuple


def read_camera_frames(session_dir: Path) -> List[Tuple[int, int]]:
    """Read all action_camera_*.jsonl files, return list of (frame_id, timestamp_ns)."""
    frames = []
    for camera_file in session_dir.glob("action_camera_*.jsonl"):
        try:
            with open(camera_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    frame_id = data.get("frame_id")
                    timestamp_ns = data.get("timestamp_ns")
                    if frame_id is not None and timestamp_ns is not None:
                        frames.append((frame_id, timestamp_ns))
        except (json.JSONDecodeError, KeyError, FileNotFoundError) as e:
            print(f"Warning: Failed to read {camera_file}: {e}", file=sys.stderr)

    # Sort by timestamp_ns for consistency
    frames.sort(key=lambda x: x[1])
    return frames


def read_game_ticks(session_dir: Path) -> List[Tuple[int, float]]:
    """Read game_state.jsonl, return list of (tick_id, timestamp_ms)."""
    ticks = []
    game_state_file = session_dir / "game_state.jsonl"

    if not game_state_file.exists():
        raise FileNotFoundError(f"No game_state.jsonl found in {session_dir}")

    try:
        with open(game_state_file, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                tick_id = data.get("tick_id")
                timestamp_ms = data.get("timestamp_ms")
                if tick_id is not None and timestamp_ms is not None:
                    ticks.append((tick_id, timestamp_ms))
    except (json.JSONDecodeError, KeyError) as e:
        raise ValueError(f"Failed to parse game_state.jsonl: {e}") from e

    # Sort by timestamp_ms for consistency
    ticks.sort(key=lambda x: x[1])
    return ticks


def calculate_gaps(
    frames: List[Tuple[int, int]], ticks: List[Tuple[int, float]]
) -> Tuple[int, int, int, int]:
    """
    Calculate alignment gaps between camera frames and game ticks.

    Returns: (le_10ms, le_50ms, le_100ms, gt_100ms) counts
    """
    if not ticks:
        raise ValueError("No game ticks found")

    # Convert tick timestamps to nanoseconds for comparison
    tick_timestamps_ns = [ts_ms * 1_000_000 for _, ts_ms in ticks]

    le_10ms = 0
    le_50ms = 0
    le_100ms = 0
    gt_100ms = 0

    for frame_id, frame_ts_ns in frames:
        # Find the closest tick using bisect
        pos = bisect.bisect_left(tick_timestamps_ns, frame_ts_ns)

        # Check tick at pos (if exists) and pos-1 (if exists)
        candidates = []
        if pos < len(tick_timestamps_ns):
            candidates.append(tick_timestamps_ns[pos])
        if pos > 0:
            candidates.append(tick_timestamps_ns[pos - 1])

        if not candidates:
            # Should not happen if ticks is non-empty
            continue

        # Find the closest tick
        closest_tick_ns = min(candidates, key=lambda x: abs(x - frame_ts_ns))

        # Calculate gap in milliseconds
        gap_ns = abs(frame_ts_ns - closest_tick_ns)
        gap_ms = gap_ns / 1_000_000

        # Bucket the gap
        if gap_ms <= 10:
            le_10ms += 1
        elif gap_ms <= 50:
            le_50ms += 1
        elif gap_ms <= 100:
            le_100ms += 1
        else:
            gt_100ms += 1

    return le_10ms, le_50ms, le_100ms, gt_100ms


def calculate_ratios(
    total_frames: int, le_10ms: int, le_50ms: int, le_100ms: int
) -> Tuple[float, float, float]:
    """Calculate strict, ok, and tolerable ratios."""
    if total_frames == 0:
        return 0.0, 0.0, 0.0

    ratio_strict = le_10ms / total_frames
    ratio_ok = (le_10ms + le_50ms) / total_frames
    ratio_tolerable = (le_10ms + le_50ms + le_100ms) / total_frames

    return ratio_strict, ratio_ok, ratio_tolerable


def determine_verdict(ratio_strict: float, ratio_ok: float, ratio_tolerable: float) -> str:
    """Determine verdict based on ratios."""
    if ratio_strict >= 0.80:
        return "PASS_STRICT"
    elif ratio_ok >= 0.95:
        return "PASS_OK"
    elif ratio_tolerable >= 0.99:
        return "PASS_TOLERABLE"
    else:
        return "FAIL"


def format_human_readable(
    session_id: str,
    total_frames: int,
    total_ticks: int,
    le_10ms: int,
    le_50ms: int,
    le_100ms: int,
    gt_100ms: int,
    ratio_strict: float,
    ratio_ok: float,
    ratio_tolerable: float,
    verdict: str,
) -> str:
    """Format human-readable output."""
    output = []
    output.append(f"SYNC TOLERANCE AUDIT — {session_id}")
    output.append(f"  camera frames: {total_frames}")
    output.append(f"  game ticks:    {total_ticks}")
    output.append("")
    output.append("  Alignment gap distribution:")

    # Calculate percentages
    pct_10 = (le_10ms / total_frames * 100) if total_frames > 0 else 0
    pct_50 = (le_50ms / total_frames * 100) if total_frames > 0 else 0
    pct_100 = (le_100ms / total_frames * 100) if total_frames > 0 else 0
    pct_gt = (gt_100ms / total_frames * 100) if total_frames > 0 else 0

    output.append(f"    <=10ms:  {le_10ms:4d} ({pct_10:5.1f}%)   STRICT")
    output.append(f"    <=50ms:  {le_50ms:4d} ({pct_50:5.1f}%)   OK")
    output.append(f"    <=100ms: {le_100ms:4d} ({pct_100:5.1f}%)   TOLERABLE")
    output.append(f"    >100ms:  {gt_100ms:4d} ({pct_gt:5.1f}%)   POOR")
    output.append("")

    # Add verdict with appropriate message
    if verdict == "PASS_STRICT":
        message = f"PASS_STRICT ({ratio_strict*100:.1f}% within 10ms)"
    elif verdict == "PASS_OK":
        message = f"PASS_OK ({ratio_ok*100:.1f}% within 50ms)"
    elif verdict == "PASS_TOLERABLE":
        message = f"PASS_TOLERABLE ({ratio_tolerable*100:.1f}% within 100ms)"
    else:
        message = f"FAIL (only {ratio_tolerable*100:.1f}% within 100ms)"

    output.append(f"  Verdict: {message}")

    return "\n".join(output)


def format_json_output(
    total_frames: int,
    total_ticks: int,
    le_10ms: int,
    le_50ms: int,
    le_100ms: int,
    gt_100ms: int,
    ratio_strict: float,
    ratio_ok: float,
    ratio_tolerable: float,
    verdict: str,
) -> str:
    """Format JSON output."""
    result = {
        "camera_frames": total_frames,
        "game_ticks": total_ticks,
        "le_10ms": le_10ms,
        "le_50ms": le_50ms,
        "le_100ms": le_100ms,
        "gt_100ms": gt_100ms,
        "ratio_strict": round(ratio_strict, 6),
        "ratio_ok": round(ratio_ok, 6),
        "ratio_tolerable": round(ratio_tolerable, 6),
        "verdict": verdict,
    }
    return json.dumps(result)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit frame ↔ tick time alignment precision.")
    parser.add_argument(
        "session_dir",
        help="Path to session directory containing action_camera_*.jsonl and game_state.jsonl",
    )
    parser.add_argument("--json", action="store_true", help="Output results as JSON")

    args = parser.parse_args()
    session_dir = Path(args.session_dir)

    # Validate session directory exists
    if not session_dir.exists():
        print(f"Error: Session directory '{session_dir}' does not exist", file=sys.stderr)
        return 1

    # Read camera frames
    try:
        frames = read_camera_frames(session_dir)
    except Exception as e:
        print(f"Error reading camera frames: {e}", file=sys.stderr)
        return 1

    if not frames:
        print(f"Error: No camera frames found in {session_dir}", file=sys.stderr)
        return 1

    # Read game ticks
    try:
        ticks = read_game_ticks(session_dir)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error reading game ticks: {e}", file=sys.stderr)
        return 1

    if not ticks:
        print(f"Error: No game ticks found in {session_dir}", file=sys.stderr)
        return 1

    # Calculate gaps
    try:
        le_10ms, le_50ms, le_100ms, gt_100ms = calculate_gaps(frames, ticks)
    except Exception as e:
        print(f"Error calculating alignment gaps: {e}", file=sys.stderr)
        return 1

    total_frames = len(frames)
    total_ticks = len(ticks)

    # Calculate ratios
    ratio_strict, ratio_ok, ratio_tolerable = calculate_ratios(
        total_frames, le_10ms, le_50ms, le_100ms
    )

    # Determine verdict
    verdict = determine_verdict(ratio_strict, ratio_ok, ratio_tolerable)

    # Output results
    if args.json:
        json_output = format_json_output(
            total_frames,
            total_ticks,
            le_10ms,
            le_50ms,
            le_100ms,
            gt_100ms,
            ratio_strict,
            ratio_ok,
            ratio_tolerable,
            verdict,
        )
        print(json_output)
    else:
        human_output = format_human_readable(
            session_dir.name,
            total_frames,
            total_ticks,
            le_10ms,
            le_50ms,
            le_100ms,
            gt_100ms,
            ratio_strict,
            ratio_ok,
            ratio_tolerable,
            verdict,
        )
        print(human_output)

    # Return 0 for pass verdicts, 1 for FAIL
    return 0 if verdict != "FAIL" else 1


if __name__ == "__main__":
    sys.exit(main())
