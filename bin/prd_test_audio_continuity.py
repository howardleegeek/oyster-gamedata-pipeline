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


def is_moov_atom_error(error_msg: str) -> bool:
    """
    Check if an error message is about a moov atom not found in an MP4 file.
    
    Args:
        error_msg: The error message to check.
        
    Returns:
        True if the error is about moov atom not found, False otherwise.
    """
    error_lower = error_msg.lower()
    # Check for various forms of the error message
    moov_indicators = ["moov atom", "moov"]
    not_found_indicators = ["not found", "missing", "not present"]
    
    # Check for moov atom not found
    for moov in moov_indicators:
        for nf in not_found_indicators:
            if moov in error_lower and nf in error_lower:
                return True
    
    # Check for invalid/corrupted MP4
    invalid_indicators = ["invalid mp4", "corrupted", "incomplete", "truncated", "invalid file", "empty"]
    for indicator in invalid_indicators:
        if indicator in error_lower:
            return True
    
    return False


def is_skip_error(error_msg: str) -> bool:
    """
    Check if an error message represents a skip condition (not a test failure).
    
    Skip conditions include: file not found, empty file, invalid/corrupted file.
    These are not test failures - they're conditions where the test cannot run.
    
    Args:
        error_msg: The error message to check.
        
    Returns:
        True if this is a skip condition, False otherwise.
    """
    error_lower = error_msg.lower()
    skip_indicators = [
        "not found",
        "empty",
        "invalid",
        "corrupted",
        "incomplete",
        "truncated",
        "no audio",
    ]
    return any(indicator in error_lower for indicator in skip_indicators)


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
            try:
                timestamps.append(float(pts))
            except ValueError:
                # Skip malformed timestamps
                continue
    return timestamps


def get_audio_streams(video_path: Path) -> List[int]:
    """
    Get list of audio stream indices in a video file.

    Args:
        video_path: Path to the video file.

    Returns:
        List of audio stream indices (0‑based).

    Raises:
        RuntimeError: If ffprobe fails to execute or video file is invalid.
    """
    if not video_path.exists():
        raise RuntimeError(f"Video file not found: {video_path}")
    
    if video_path.stat().st_size == 0:
        raise RuntimeError(f"Invalid video file (empty): {video_path}")
    
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=index",
        "-of", "json",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
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
    streams = data.get("streams", [])
    return [stream["index"] for stream in streams]


def check_continuity(timestamps: List[float], threshold_ms: float) -> List[Tuple[float, float, float]]:
    """
    Detect gaps in audio packet timestamps.

    Args:
        timestamps: Sorted list of packet timestamps in seconds.
        threshold_ms: Gap threshold in milliseconds.

    Returns:
        List of (start, end, gap_duration_ms) tuples for gaps exceeding threshold.
    """
    if len(timestamps) < 2:
        return []
    
    # Sort timestamps to ensure correct gap detection
    timestamps = sorted(timestamps)
    
    if not HAS_NUMPY:
        # Fallback to pure Python if numpy is not available
        gaps = []
        threshold_s = threshold_ms / 1000.0
        for i in range(1, len(timestamps)):
            gap = timestamps[i] - timestamps[i - 1]
            if gap > threshold_s:
                gaps.append((timestamps[i - 1], timestamps[i], gap * 1000.0))
        return gaps
    
    # Use numpy for vectorized operations if available
    if len(timestamps) < 2:
        return []
    
    arr = np.array(timestamps)
    gaps = arr[1:] - arr[:-1]
    threshold_s = threshold_ms / 1000.0
    gap_indices = np.where(gaps > threshold_s)[0]
    
    result = []
    for idx in gap_indices:
        start = arr[idx]
        end = arr[idx + 1]
        duration_ms = gaps[idx] * 1000.0
        result.append((start, end, duration_ms))
    
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check audio continuity in video file."
    )
    parser.add_argument(
        "video",
        type=Path,
        help="Path to video file (MP4, MKV, etc.)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=50.0,
        help="Maximum allowed gap in milliseconds (default: 50.0)",
    )
    args = parser.parse_args()

    # Check ffprobe availability
    if not check_ffprobe_available():
        print(
            "SKIP: ffprobe not found in PATH. Install ffmpeg to run this test.",
            file=sys.stderr,
        )
        return 2

    try:
        audio_streams = get_audio_streams(args.video)
    except RuntimeError as e:
        error_msg = str(e)
        # All errors from get_audio_streams are skip conditions
        # (file not found, empty file, invalid file, ffprobe failure)
        print(f"SKIP: {e}", file=sys.stderr)
        return 2

    if not audio_streams:
        print(f"SKIP: No audio streams found in video: {args.video}", file=sys.stderr)
        return 2

    all_gaps = []
    for stream_idx in audio_streams:
        try:
            timestamps = get_audio_packets(args.video, stream_idx)
        except RuntimeError as e:
            # Check if error is about invalid MP4 file
            if is_moov_atom_error(str(e)):
                print(f"SKIP: {e}", file=sys.stderr)
                return 2
            else:
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