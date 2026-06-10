#!/usr/bin/env python3
"""
Video Artifact Scanner
Detects stutter / freeze frame / encoding artifacts in video files.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def find_video_file(session_dir: str) -> Optional[str]:
    """Find MP4 file in session directory."""
    session_path = Path(session_dir)
    if not session_path.exists():
        return None

    mp4_files = list(session_path.glob("*.mp4"))
    if not mp4_files:
        return None

    # Return the first MP4 file
    return str(mp4_files[0])


def get_video_duration(video_path: str) -> Optional[float]:
    """Get video duration using ffprobe."""
    try:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            video_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except (subprocess.CalledProcessError, ValueError, FileNotFoundError):
        return None


def extract_thumbnails(video_path: str, sample_rate: int = 10) -> Optional[bytes]:
    """
    Extract 8x8 grayscale thumbnails from video using ffmpeg.
    Samples every sample_rate-th frame.
    Returns raw bytes (64 bytes per thumbnail).
    """
    try:
        cmd = [
            "ffmpeg",
            "-i",
            video_path,
            "-vf",
            f"scale=8:8,format=gray,select='not(mod(n,{sample_rate}))'",
            "-vsync",
            "0",
            "-f",
            "rawvideo",
            "-",
        ]
        result = subprocess.run(cmd, capture_output=True, check=True)
        return result.stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def compute_dhash(thumbnail_bytes: bytes) -> str:
    """
    Compute dHash (difference hash) for 8x8 grayscale thumbnail.
    8x8 grayscale -> 7x8 difference bits -> 56-bit hash -> hex string.
    """
    if len(thumbnail_bytes) != 64:
        raise ValueError(f"Expected 64 bytes for 8x8 thumbnail, got {len(thumbnail_bytes)}")

    # Convert bytes to 8x8 array of pixel values
    pixels = list(thumbnail_bytes)

    # Compute dHash: compare each pixel with its right neighbor
    hash_bits = []
    for i in range(8):  # 8 rows
        for j in range(7):  # 7 columns (comparing j with j+1)
            left = pixels[i * 8 + j]
            right = pixels[i * 8 + j + 1]
            hash_bits.append(1 if left > right else 0)

    # Convert bits to 56-bit integer
    hash_int = 0
    for i, bit in enumerate(hash_bits):
        if bit:
            hash_int |= 1 << (55 - i)

    # Convert to hex (14 characters for 56 bits = 7 bytes)
    return f"{hash_int:014x}"


def hamming_distance(hash1: str, hash2: str) -> int:
    """Compute Hamming distance between two hex hashes."""
    # Convert hex strings to integers
    int1 = int(hash1, 16)
    int2 = int(hash2, 16)

    # XOR and count bits
    xor_result = int1 ^ int2
    return bin(xor_result).count("1")


def detect_freezes(hashes: List[str], sample_rate: int) -> List[Dict[str, Any]]:
    """
    Detect freeze frames: consecutive frames with same hash.
    Threshold: ≥ 30 frames (60fps × 0.5s).
    Since we sample every sample_rate frames, we need ≥ 30/sample_rate consecutive same hashes.
    """
    if not hashes:
        return []

    freezes = []
    current_start = 0
    current_hash = hashes[0]

    for i in range(1, len(hashes)):
        if hashes[i] != current_hash:
            # Check if we have a freeze
            freeze_length = i - current_start
            # Convert to original frame count
            original_frames = freeze_length * sample_rate

            if original_frames >= 30:  # 60fps × 0.5s
                freezes.append(
                    {
                        "start_frame": current_start * sample_rate,
                        "end_frame": (i - 1) * sample_rate,
                        "duration_s": original_frames / 60.0,  # Assuming 60fps
                        "hash": current_hash,
                    }
                )

            current_start = i
            current_hash = hashes[i]

    # Check for freeze at the end
    freeze_length = len(hashes) - current_start
    original_frames = freeze_length * sample_rate
    if original_frames >= 30:
        freezes.append(
            {
                "start_frame": current_start * sample_rate,
                "end_frame": (len(hashes) - 1) * sample_rate,
                "duration_s": original_frames / 60.0,
                "hash": current_hash,
            }
        )

    return freezes


def detect_stutters(hashes: List[str], sample_rate: int) -> List[Dict[str, Any]]:
    """
    Detect stutter events: hash hamming distance < 5 jumps to > 20 in < 3 frames.
    Since we sample every sample_rate frames, we look at consecutive sampled frames.
    """
    if len(hashes) < 3:
        return []

    stutters = []

    for i in range(2, len(hashes)):
        prev_prev_hash = hashes[i - 2]
        prev_hash = hashes[i - 1]
        curr_hash = hashes[i]

        # Calculate hamming distances
        dist1 = hamming_distance(prev_prev_hash, prev_hash)
        dist2 = hamming_distance(prev_hash, curr_hash)

        # Check for stutter: small change then big change
        if dist1 < 5 and dist2 > 20:
            stutters.append(
                {
                    "frame": i * sample_rate,  # Frame where stutter is detected
                    "hamming_jump": f"{dist1}->{dist2}",
                    "prev_hash": prev_hash,
                    "curr_hash": curr_hash,
                }
            )

    return stutters


def format_timestamp(seconds: float) -> str:
    """Format seconds as HH:MM:SS."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def calculate_verdict(
    freezes: List[Dict], stutters: List[Dict], total_frames: int
) -> Tuple[str, float]:
    """
    Calculate verdict based on artifact ratio.
    artifact_ratio = (freeze_frames_total + stutter_count * 5) / total_frames
    """
    if total_frames == 0:
        return "PASS", 0.0

    # Calculate total freeze frames
    freeze_frames_total = 0
    for freeze in freezes:
        freeze_frames_total += freeze["end_frame"] - freeze["start_frame"] + 1

    # Calculate artifact ratio
    artifact_ratio = (freeze_frames_total + len(stutters) * 5) / total_frames

    # Determine verdict
    if artifact_ratio < 0.001:  # 0.1%
        verdict = "PASS"
    elif artifact_ratio < 0.01:  # 1%
        verdict = "PASS_DEGRADED"
    else:
        verdict = "FAIL"

    return verdict, artifact_ratio


def scan_video_artifacts(video_path: str, sample_rate: int = 10) -> Dict[str, Any]:
    """Main scanning function."""
    result = {
        "file": os.path.basename(video_path),
        "frames_sampled": 0,
        "duration_s": 0.0,
        "freeze_events": 0,
        "stutter_events": 0,
        "freezes": [],
        "stutters": [],
        "artifact_ratio": 0.0,
        "verdict": "SKIP",
    }

    # Get video duration
    duration = get_video_duration(video_path)
    if duration is None:
        return result

    result["duration_s"] = duration

    # Extract thumbnails
    thumbnail_data = extract_thumbnails(video_path, sample_rate)
    if thumbnail_data is None:
        return result

    # Calculate total frames (assuming 60fps)
    total_frames = int(duration * 60)

    # Process thumbnails
    thumbnail_size = 64  # 8x8 bytes
    num_thumbnails = len(thumbnail_data) // thumbnail_size

    if num_thumbnails == 0:
        return result

    result["frames_sampled"] = num_thumbnails

    # Compute hashes for all thumbnails
    hashes = []
    for i in range(num_thumbnails):
        start = i * thumbnail_size
        end = start + thumbnail_size
        thumbnail_bytes = thumbnail_data[start:end]

        if len(thumbnail_bytes) == thumbnail_size:
            try:
                dhash = compute_dhash(thumbnail_bytes)
                hashes.append(dhash)
            except ValueError:
                # Skip invalid thumbnail
                hashes.append("0" * 14)  # Default hash

    # Detect artifacts
    freezes = detect_freezes(hashes, sample_rate)
    stutters = detect_stutters(hashes, sample_rate)

    # Calculate verdict
    verdict, artifact_ratio = calculate_verdict(freezes, stutters, total_frames)

    # Prepare result
    result["freeze_events"] = len(freezes)
    result["stutter_events"] = len(stutters)
    result["freezes"] = [
        {
            "start_frame": f["start_frame"],
            "end_frame": f["end_frame"],
            "duration_s": f["duration_s"],
        }
        for f in freezes
    ]
    result["stutters"] = [
        {"frame": s["frame"], "hamming_jump": s["hamming_jump"]} for s in stutters
    ]
    result["artifact_ratio"] = artifact_ratio
    result["verdict"] = verdict

    return result


def print_human_readable(result: Dict[str, Any], session_id: str):
    """Print human-readable report."""
    print(f"VIDEO ARTIFACT SCAN — {session_id}")
    print(f"  File: {result['file']}")
    print(f"  Frames sampled: {result['frames_sampled']} (every 10th @ 60fps)")
    print(f"  Duration: {result['duration_s']:.1f}s")
    print()

    if result["verdict"] == "SKIP":
        print("  SKIP: Could not process video")
        return

    print(f"  Freeze frame events: {result['freeze_events']}")
    for freeze in result["freezes"]:
        timestamp = format_timestamp(freeze["start_frame"] / 60.0)
        frames = freeze["end_frame"] - freeze["start_frame"] + 1
        print(
            f"    [{timestamp}] frame {freeze['start_frame']}-{freeze['end_frame']} "
            f"({frames} frames, {freeze['duration_s']:.2f}s)"
        )

    print()
    print(f"  Stutter events: {result['stutter_events']}")
    for stutter in result["stutters"]:
        timestamp = format_timestamp(stutter["frame"] / 60.0)
        print(
            f"    [{timestamp}] frame {stutter['frame']} "
            f"(hamming jump {stutter['hamming_jump']})"
        )

    print()
    ratio_percent = result["artifact_ratio"] * 100
    print(
        f"  Verdict: {result['verdict']} "
        f"({result['freeze_events']} freezes + {result['stutter_events']} stutter "
        f"in {result['duration_s']:.1f}s, ratio {ratio_percent:.1f}%)"
    )


def main():
    parser = argparse.ArgumentParser(description="Scan video for stutter/freeze frame artifacts")
    parser.add_argument("session_dir", help="Session directory containing MP4 file")
    parser.add_argument("--json", action="store_true", help="Output JSON format")
    parser.add_argument(
        "--sample-rate", type=int, default=10, help="Sample every N frames (default: 10)"
    )

    args = parser.parse_args()

    # Find video file
    video_path = find_video_file(args.session_dir)
    if not video_path:
        if args.json:
            print(
                json.dumps({"error": "No MP4 file found in session directory", "verdict": "SKIP"})
            )
        else:
            print(f"No MP4 file found in session directory: {args.session_dir}")
        sys.exit(0)

    # Scan for artifacts
    result = scan_video_artifacts(video_path, args.sample_rate)

    # Output results
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        session_id = os.path.basename(os.path.normpath(args.session_dir))
        print_human_readable(result, session_id)

    # Exit with appropriate code
    if result["verdict"] == "FAIL":
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
