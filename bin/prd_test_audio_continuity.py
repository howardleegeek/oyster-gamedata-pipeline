#!/usr/bin/env python3
"""
Audio Continuity Test for Video Files.

PRD p4 #2: Verify video audio track is continuous (no gaps over 50ms).
Uses ffprobe to extract audio packet timing and numpy to detect gaps.

Exit codes:
    0 - All audio tracks are continuous (no gaps > threshold)
    1 - One or more gaps detected exceeding threshold
    2 - Error (file not found, no audio, ffprobe failure, etc.)
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

import numpy as np


def get_audio_packets(video_path: Path, stream_index: int) -> List[float]:
    """
    Extract audio packet timestamps using ffprobe.

    Args:
        video_path: Path to the video file.
        stream_index: Audio stream index to analyze.

    Returns:
        List of packet presentation timestamps in seconds.

    Raises:
        RuntimeError: If ffprobe fails to execute.
    """
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-select_streams", str(stream_index),
        "-show_entries", "packet=pts_time",
        "-of", "json",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")

    data = json.loads(result.stdout)
    timestamps = []
    for pkt in data.get("packets", []):
        pts = pkt.get("pts_time")
        if pts is not None:
            timestamps.append(float(pts))
    return sorted(timestamps)


def get_audio_streams(video_path: Path) -> List[int]:
    """
    Get indices of all audio streams in the video file.

    Args:
        video_path: Path to the video file.

    Returns:
        List of audio stream indices.
    """
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-show_entries", "stream=index,codec_type",
        "-of", "json",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")

    data = json.loads(result.stdout)
    return [
        s["index"] for s in data.get("streams", [])
        if s.get("codec_type") == "audio"
    ]


def check_continuity(
    timestamps: List[float], threshold_ms: float = 50.0
) -> List[Tuple[float, float, float]]:
    """
    Check for gaps in audio packet timestamps.

    Args:
        timestamps: Sorted list of packet timestamps in seconds.
        threshold_ms: Maximum allowed gap in milliseconds.

    Returns:
        List of tuples (gap_start, gap_end, gap_duration_ms) for gaps
        exceeding the threshold.
    """
    if len(timestamps) < 2:
        return []

    arr = np.array(timestamps)
    diffs = np.diff(arr)
    threshold_sec = threshold_ms / 1000.0

    gaps = []
    for i, diff in enumerate(diffs):
        if diff > threshold_sec:
            gaps.append((arr[i], arr[i + 1], diff * 1000.0))
    return gaps


def main(argv: List[str] = None) -> int:
    """
    Main entry point for audio continuity test.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).

    Returns:
        Exit code: 0 for pass, 1 for gaps detected, 2 for error.
    """
    parser = argparse.ArgumentParser(
        description="Check video audio track continuity (no gaps > 50ms)."
    )
    parser.add_argument(
        "video", type=Path, help="Path to video file to analyze."
    )
    parser.add_argument(
        "-t", "--threshold", type=float, default=50.0,
        help="Maximum allowed gap in milliseconds (default: 50)."
    )
    args = parser.parse_args(argv)

    if not args.video.exists():
        print(f"Error: File not found: {args.video}", file=sys.stderr)
        return 2

    try:
        audio_streams = get_audio_streams(args.video)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    if not audio_streams:
        print("Error: No audio streams found in video.", file=sys.stderr)
        return 2

    all_gaps = []
    for stream_idx in audio_streams:
        try:
            timestamps = get_audio_packets(args.video, stream_idx)
        except RuntimeError as e:
            print(f"Error reading stream {stream_idx}: {e}", file=sys.stderr)
            return 2

        gaps = check_continuity(timestamps, args.threshold)
        for start, end, duration in gaps:
            all_gaps.append((stream_idx, start, end, duration))

    if all_gaps:
        print(f"FAIL: {len(all_gaps)} gap(s) detected > {args.threshold}ms:")
        for stream_idx, start, end, duration in all_gaps:
            print(
                f"  Stream {stream_idx}: {duration:.1f}ms gap "
                f"from {start:.3f}s to {end:.3f}s"
            )
        return 1

    print(f"PASS: All {len(audio_streams)} audio stream(s) continuous.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
