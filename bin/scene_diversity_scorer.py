#!/usr/bin/env python3
"""
Scene Diversity Scorer — G034

Computes a Habitat-style scene diversity metric by clustering pixel
histograms extracted from sampled video frames. Clips whose diversity
score falls below a configurable threshold are flagged as "monotone".

Usage:
    python bin/scene_diversity_scorer.py video.mp4 [--threshold 0.35]
    python bin/scene_diversity_scorer.py --frames-dir /path/to/frames/

Output:
    JSON to stdout with keys: score, flagged, frame_count, threshold
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Sequence

_log = logging.getLogger(__name__)

if TYPE_CHECKING:
    import numpy as np

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
        "-vf", "fps=1/5,scale=256:256",
        "-frames:v", str(max_frames), "-q:v", "3",
        os.path.join(output_dir, "frame_%04d.jpg"),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        print(f"ffmpeg warning: {result.stderr.strip()}", file=sys.stderr)
    return sorted(str(f) for f in Path(output_dir).glob("frame_*.jpg"))


def compute_histogram(image_path: str, bins: int = 32) -> "np.ndarray":
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


def compute_frame_histograms(frame_paths: List[str], bins: int = 32) -> "np.ndarray":
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
    for fp in frame_paths:
        try:
            hist = compute_histogram(fp, bins=bins)
            histograms.append(hist)
        except Exception as e:
            print(f"Warning: failed to process {fp}: {e}", file=sys.stderr)
    if not histograms:
        raise ValueError("No valid frames could be processed")
    return np.stack(histograms, axis=0)


def compute_diversity_score(histograms: "np.ndarray") -> float:
    """Compute scene diversity score from frame histograms.

    Uses pairwise histogram distances to measure visual diversity.
    Higher scores indicate more diverse scenes.

    Args:
        histograms: Array of shape (num_frames, hist_dim).

    Returns:
        Diversity score in [0, 1].
    """
    np = _get_numpy()
    if histograms.shape[0] < 2:
        return 0.0
    # Compute pairwise chi-square distances
    n = histograms.shape[0]
    distances = []
    for i in range(n):
        for j in range(i + 1, n):
            d = np.sum((histograms[i] - histograms[j]) ** 2 /
                       (histograms[i] + histograms[j] + 1e-10))
            distances.append(d)
    mean_dist = np.mean(distances) if distances else 0.0
    # Normalize to [0, 1] range (empirical scaling)
    score = min(1.0, mean_dist / 2.0)
    return float(score)


def analyze_video(video_path: str, threshold: float = 0.35,
                  max_frames: int = 30, bins: int = 32) -> dict:
    """Analyze video for scene diversity.

    Args:
        video_path: Path to video file.
        threshold: Diversity threshold for flagging monotone clips.
        max_frames: Maximum frames to sample.
        bins: Histogram bins per channel.

    Returns:
        Dict with score, flagged, frame_count, threshold.
    """
    output_dir = tempfile.mkdtemp(prefix="scene_div_")
    try:
        frame_paths = extract_frames(video_path, max_frames=max_frames,
                                     output_dir=output_dir)
        if not frame_paths:
            return {"score": 0.0, "flagged": True, "frame_count": 0,
                    "threshold": threshold, "error": "No frames extracted"}
        histograms = compute_frame_histograms(frame_paths, bins=bins)
        score = compute_diversity_score(histograms)
        return {
            "score": round(score, 4),
            "flagged": score < threshold,
            "frame_count": len(frame_paths),
            "threshold": threshold
        }
    finally:
        # Cleanup extracted frames
        for f in Path(output_dir).glob("frame_*.jpg"):
            try:
                f.unlink()
            except Exception as exc:
                _log.debug(
                    "scene_diversity_scorer: failed to remove frame %s: %s",
                    f, exc, exc_info=True,
                )
        try:
            os.rmdir(output_dir)
        except Exception as exc:
            _log.debug(
                "scene_diversity_scorer: failed to remove dir %s: %s",
                output_dir, exc, exc_info=True,
            )


def analyze_frames_dir(frames_dir: str, threshold: float = 0.35,
                       bins: int = 32) -> dict:
    """Analyze pre-extracted frames directory for scene diversity.

    Args:
        frames_dir: Path to directory containing frame images.
        threshold: Diversity threshold for flagging monotone clips.
        bins: Histogram bins per channel.

    Returns:
        Dict with score, flagged, frame_count, threshold.
    """
    frame_paths = sorted([
        str(f) for f in Path(frames_dir).iterdir()
        if f.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp"}
    ])
    if not frame_paths:
        return {"score": 0.0, "flagged": True, "frame_count": 0,
                "threshold": threshold, "error": "No frames found"}
    histograms = compute_frame_histograms(frame_paths, bins=bins)
    score = compute_diversity_score(histograms)
    return {
        "score": round(score, 4),
        "flagged": score < threshold,
        "frame_count": len(frame_paths),
        "threshold": threshold
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Main entry point with argparse CLI.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).

    Returns:
        Exit code (0 for success, non-zero for errors).
    """
    parser = argparse.ArgumentParser(
        description="Compute scene diversity score for video clips."
    )
    parser.add_argument("video", nargs="?", help="Path to video file")
    parser.add_argument("--frames-dir", help="Directory with pre-extracted frames")
    parser.add_argument("--threshold", type=float, default=0.35,
                        help="Diversity threshold for flagging (default: 0.35)")
    parser.add_argument("--max-frames", type=int, default=30,
                        help="Max frames to sample from video (default: 30)")
    parser.add_argument("--bins", type=int, default=32,
                        help="Histogram bins per channel (default: 32)")
    parser.add_argument("--output", "-o", help="Output JSON file (default: stdout)")
    args = parser.parse_args(argv)

    if not args.video and not args.frames_dir:
        parser.error("Either video path or --frames-dir is required")

    try:
        if args.frames_dir:
            result = analyze_frames_dir(args.frames_dir, threshold=args.threshold,
                                        bins=args.bins)
        else:
            result = analyze_video(args.video, threshold=args.threshold,
                                   max_frames=args.max_frames, bins=args.bins)
        output_json = json.dumps(result, indent=2)
        if args.output:
            Path(args.output).write_text(output_json)
        else:
            print(output_json)
        return 0 if not result.get("error") else 1
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
