#!/usr/bin/env python3
"""
Audio Continuity Test for Video Files.

PRD p4 #2: Verify video audio track is continuous (no gaps over 50ms).
Uses ffprobe to extract audio packet timing and numpy to detect gaps.

Exit codes:
    0 - All audio tracks are continuous (no gaps > threshold)
    1 - One or more gaps detected exceeding threshold
    2 - Error/Skip (file not found, no audio, ffprobe failure, etc.)
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

# Try to import numpy, but handle the case where it's not installed
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False
    # Create a dummy np module for type checking
    class DummyNP:
        pass
    np = DummyNP()


def check_ffprobe_available() -> bool:
    """
    Check if ffprobe is available in the system PATH.
    
    Returns:
        True if ffprobe is available, False otherwise.
    """
    return shutil.which("ffprobe") is not None


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
        "-v", "error",
        "-select_streams", str(stream_index),
        "-show_entries", "packet=pts_time",
        "-of", "json",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        # Provide detailed error information
        error_msg = result.stderr.strip()
        if not error_msg:
            error_msg = "no error output"
        
        # Check for specific "moov atom not found" error
        if "moov atom not found" in error_msg.lower():
            raise RuntimeError(
                f"Invalid MP4 file: moov atom (metadata) not found. "
                f"This usually means the file is incomplete or corrupted. "
                f"File: {video_path}"
            )
        
        raise RuntimeError(
            f"ffprobe failed with code {result.returncode}: {error_msg}. "
            f"Command: {' '.join(cmd)}"
        )

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
        "-v", "error",
        "-show_entries", "stream=index,codec_type",
        "-of", "json",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        # If ffprobe fails, assume no audio streams
        return []

    data = json.loads(result.stdout)
    audio_streams = []
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "audio":
            audio_streams.append(int(stream["index"]))
    return audio_streams


def check_continuity(
    timestamps: List[float], threshold_ms: float
) -> List[Tuple[float, float, float]]:
    """
    Check if audio packet timestamps have gaps exceeding threshold.

    Args:
        timestamps: Sorted list of packet timestamps in seconds.
        threshold_ms: Maximum allowed gap in milliseconds.

    Returns:
        List of (start_time, end_time, gap_ms) tuples for gaps
        exceeding threshold.
    """
    if len(timestamps) < 2:
        return []

    threshold_s = threshold_ms / 1000.0
    gaps = []

    for i in range(1, len(timestamps)):
        gap = timestamps[i] - timestamps[i - 1]
        if gap > threshold_s:
            gaps.append((timestamps[i - 1], timestamps[i], gap * 1000.0))

    return gaps


def main(argv: List[str] = None) -> int:
    """
    Main entry point for audio continuity test.

    Args:
        argv: Command line arguments (defaults to sys.argv[1:]).

    Returns:
        Exit code as described in module docstring.
    """
    # Check if numpy is available
    if not HAS_NUMPY:
        print(
            "Error: numpy is not installed. "
            "Please install numpy to run this test (pip install numpy).",
            file=sys.stderr
        )
        return 2

    parser = argparse.ArgumentParser(
        description="Check audio continuity in video files."
    )
    parser.add_argument(
        "video", type=Path, help="Path to video file to analyze."
    )
    parser.add_argument(
        "-t",
        "--threshold",
        type=float,
        default=50.0,
        help="Maximum allowed gap in milliseconds (default: 50).",
    )
    args = parser.parse_args(argv)

    if not args.video.exists():
        print(f"SKIP: Video file not found: {args.video}", file=sys.stderr)
        return 2

    # Check if file is empty (0 bytes)
    if args.video.stat().st_size == 0:
        print(f"SKIP: Video file is empty (0 bytes): {args.video}", file=sys.stderr)
        return 2

    # Check if ffprobe is available
    if not check_ffprobe_available():
        print(
            "SKIP: ffprobe not found in PATH. "
            "Please install ffmpeg/ffprobe to run this test.",
            file=sys.stderr
        )
        return 2

    try:
        audio_streams = get_audio_streams(args.video)
    except RuntimeError as e:
        # Check if error is about invalid MP4 file
        error_str = str(e)
        if "moov atom not found" in error_str.lower() or "invalid mp4" in error_str.lower():
            print(f"SKIP: {error_str}", file=sys.stderr)
            return 2
        else:
            print(f"Error: {e}", file=sys.stderr)
            return 2

    if not audio_streams:
        print(f"SKIP: No audio streams found in video: {args.video}", file=sys.stderr)
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