#!/usr/bin/env python3
"""
Scene Diversity Scorer — G034

Computes a Habitat-style scene diversity metric by clustering pixel
histograms extracted from sampled video frames. Clips whose diversity
score falls below a configurable threshold are flagged as "monotone".

Usage:
    python bin/scene_diversity_scorer.py video.mp4 [--threshold 0.35]
    python bin/scene_diversity_scorer.py --frames-dir /path/to/frames/
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

_np = None
_pil_image = None


def _get_numpy():
    """Lazy import numpy."""
    global _np
    if _np is None:
        import numpy as np
        _np = np
    return _np


def _get_pil():
    """Lazy import PIL.Image."""
    global _pil_image
    if _pil_image is None:
        from PIL import Image
        _pil_image = Image
    return _pil_image


def extract_frames(video_path: str, max_frames: int = 30,
                   output_dir: Optional[str] = None) -> List[str]:
    """Extract evenly-spaced frames from video using ffmpeg.

    Args:
        video_path: Path to input video file.
        max_frames: Maximum frames to extract.
        output_dir: Output directory (uses tempfile.mkdtemp if None).

    Returns:
        Sorted list of extracted frame file paths.
    """
    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="scene_div_")
    os.makedirs(output_dir, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", f"fps=1/5,scale=256:256",
        "-frames:v", str(max_frames), "-q:v", "3",
        os.path.join(output_dir, "frame_%04d.jpg"),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        print(f"ffmpeg warning: {result.stderr.strip()}", file=sys.stderr)
    return sorted(str(f) for f in Path(output_dir).glob("frame_*.jpg"))


def compute_histogram(image_path: str, bins: int = 32) -> "numpy.ndarray":
    """Compute normalized RGB histogram for an image.

    Args:
        image_path: Path to image file.
        bins: Number of bins per color channel.

    Returns:
        Normalized histogram vector of shape (bins * 3,).
    """
    np = _get_numpy()
    img = _get_pil().open(image_path).convert("RGB")
    arr = np.array(img, dtype=np.float32) / 255.0
    hist = np.concatenate([
        np.histogram(arr[:, :, c], bins=bins, range=(0.0, 1.0))[0].astype(np.float32)
        for c in range(3)
    ])
    return hist / (hist.sum() + 1e-10)


def compute_frame_histograms(frame_paths: List[str], bins: int = 32) -> "numpy.ndarray":
    """Compute histograms for all frames.

    Args:
        frame_paths: List of frame image paths.
        bins: Histogram bins per channel.

    Returns:
        Array of shape (num_frames, bins * 3).

    Raises:
        ValueError: If no valid frames could be processed.
    """
    np = _get_numpy()
    histograms = []
    for path in frame_paths:
        try:
            histograms.append(compute_histogram(path, bins))
        except Exception as exc:
            print(f"  Skipping frame {path}: {exc}", file=sys.stderr)
    if not histograms:
        raise ValueError("No valid frames could be processed")
    return np.array(histograms)


def compute_diversity_score(histograms: "numpy.ndarray") -> float:
    """Compute scene diversity score from frame histograms.

    Uses pairwise chi-square distances to measure visual variety.

    Args:
        histograms: Array of shape (num_frames, hist_dim).

    Returns:
        Diversity score in [0, 1], where 1 = maximum diversity.
    """
    np = _get_numpy()
    n_frames = len(histograms)
    if n_frames < 2:
        return 0.0
    distances = []
    for i in range(n_frames):
        for j in range(i + 1, n_frames):
            diff = histograms[i] - histograms[j]
            denom = histograms[i] + histograms[j] + 1e-10
            chi_sq = np.sum((diff ** 2) / denom)
            distances.append(chi_sq)
    mean_dist = np.mean(distances) if distances else 0.0
    return float(min(mean_dist / 1.0, 1.0))


def analyze_video(video_path: str, threshold: float = 0.35,
                   max_frames: int = 30, bins: int = 32) -> Tuple[float, bool, dict]:
    """Analyze a video file for scene diversity.

    Args:
        video_path: Path to video file.
        threshold: Diversity threshold for flagging.
        max_frames: Maximum frames to sample.
        bins: Histogram bins per channel.

    Returns:
        Tuple of (diversity_score, is_monotone, metadata_dict).
    """
    frame_dir = tempfile.mkdtemp(prefix="scene_diversity_frames_")
    try:
        frame_paths = extract_frames(video_path, max_frames, frame_dir)
        if not frame_paths:
            return 0.0, True, {"error": "No frames extracted"}
        histograms = compute_frame_histograms(frame_paths, bins)
        score = compute_diversity_score(histograms)
        is_monotone = score < threshold
        metadata = {
            "video_path": video_path, "num_frames": len(frame_paths),
            "diversity_score": round(score, 4), "threshold": threshold,
            "is_monotone": is_monotone,
        }
        return score, is_monotone, metadata
    finally:
        for f in Path(frame_dir).glob("*"):
            try:
                f.unlink()
            except OSError:
                pass
        try:
            Path(frame_dir).rmdir()
        except OSError:
            pass


def analyze_frames_dir(frames_dir: str, threshold: float = 0.35,
                       bins: int = 32) -> Tuple[float, bool, dict]:
    """Analyze a directory of pre-extracted frames.

    Args:
        frames_dir: Path to directory with frame images.
        threshold: Diversity threshold for flagging.
        bins: Histogram bins per channel.

    Returns:
        Tuple of (diversity_score, is_monotone, metadata_dict).
    """
    frame_paths = sorted(
        str(f) for f in Path(frames_dir).glob("*")
        if f.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp"}
    )
    if not frame_paths:
        return 0.0, True, {"error": "No frames found in directory"}
    histograms = compute_frame_histograms(frame_paths, bins)
    score = compute_diversity_score(histograms)
    is_monotone = score < threshold
    metadata = {
        "frames_dir": frames_dir, "num_frames": len(frame_paths),
        "diversity_score": round(score, 4), "threshold": threshold,
        "is_monotone": is_monotone,
    }
    return score, is_monotone, metadata


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Main entry point for CLI.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).

    Returns:
        Exit code: 0 for success, 1 for flagged monotone clip, 2 for errors.
    """
    parser = argparse.ArgumentParser(
        description="Compute Habitat-style scene diversity metric for video clips."
    )
    parser.add_argument("input", nargs="?", help="Input video file path")
    parser.add_argument("--frames-dir", help="Directory with pre-extracted frames")
    parser.add_argument("--threshold", "-t", type=float, default=0.35,
                        help="Diversity threshold for flagging (default: 0.35)")
    parser.add_argument("--max-frames", type=int, default=30,
                        help="Max frames to sample (default: 30)")
    parser.add_argument("--bins", type=int, default=32,
                        help="Histogram bins per channel (default: 32)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args(argv)

    if not args.input and not args.frames_dir:
        parser.error("Either INPUT or --frames-dir must be specified")

    try:
        if args.frames_dir:
            score, is_monotone, metadata = analyze_frames_dir(
                args.frames_dir, args.threshold, args.bins)
        else:
            score, is_monotone, metadata = analyze_video(
                args.input, args.threshold, args.max_frames, args.bins)

        if args.json:
            print(json.dumps(metadata, indent=2))
        else:
            status = "MONOTONE (flagged)" if is_monotone else "DIVERSE (ok)"
            print(f"Scene diversity score: {score:.4f}")
            print(f"Threshold: {args.threshold}")
            print(f"Status: {status}")
        return 1 if is_monotone else 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())