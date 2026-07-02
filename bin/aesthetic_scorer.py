#!/usr/bin/env python3
"""
G159 · Aesthetic Scorer
========================

Cluster E: per-clip aesthetic / motion / OCR-overlay / camera-jitter scores
(Open-Sora 2.0 filter stack) — buyer reweighting / curation.

Usage:
    python -m bin.aesthetic_scorer --input video.mp4 --output scores.json
    python -m bin.aesthetic_scorer --batch input_dir/ --output results.csv

Scoring dimensions (all normalised to [0.0, 1.0]):
  - aesthetic : contrast + dynamic-range heuristic per frame
  - motion    : inter-frame pixel-difference intensity
  - ocr       : edge-density heuristic for text/overlay detection
  - jitter    : high-frequency camera-shake proxy via frame-to-frame variance
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_np: Any = None
_Image: Any = None


def _lazy_imports() -> Tuple[Any, Any]:
    """Return (numpy, PIL.Image), importing lazily on first call."""
    global _np, _Image
    if _np is None:
        import numpy as _np  # type: ignore[assignment]
    if _Image is None:
        from PIL import Image as _Image  # type: ignore[assignment]
    return _np, _Image


# ---------------------------------------------------------------------------
# Frame extraction
# ---------------------------------------------------------------------------

def _extract_frames_from_video(video_path: str, num_frames: int = 32) -> List[Any]:
    """Extract *num_frames* evenly-spaced frames from a video file.

    Uses ffmpeg (subprocess list form) + PIL.  Returns list of numpy arrays.
    """
    np, _ = _lazy_imports()
    tmpdir = tempfile.mkdtemp(prefix="g159_frames_")
    try:
        out_pattern = os.path.join(tmpdir, "frame_%04d.png")
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-vf", "fps=1/1,scale=320:240",
            "-frames:v", str(num_frames), "-q:v", "2", out_pattern,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            logging.warning("ffmpeg rc=%d: %s", result.returncode, result.stderr[:200])
        frames: List[Any] = []
        for fname in sorted(Path(tmpdir).glob("frame_*.png")):
            img = _Image.open(str(fname)).convert("RGB")
            frames.append(np.array(img))
        return frames
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _extract_frames_from_images(image_paths: Sequence[str], max_frames: int = 32) -> List[Any]:
    """Load frames from a list of image file paths."""
    np, _ = _lazy_imports()
    frames: List[Any] = []
    for p in image_paths[:max_frames]:
        img = _Image.open(p).convert("RGB")
        frames.append(np.array(img))
    return frames


# ---------------------------------------------------------------------------
# Scoring functions
# ---------------------------------------------------------------------------

def _to_gray(frame: Any) -> Any:
    """Convert frame to 2-D float grayscale array."""
    np, _ = _lazy_imports()
    return np.mean(frame, axis=-1).astype(float) if frame.ndim == 3 else frame.astype(float)


def compute_aesthetic_score(frames: List[Any]) -> float:
    """Compute aesthetic quality score in [0.0, 1.0].

    Heuristic: weighted combination of per-frame contrast (std) and
    dynamic range (max - min).  Higher values indicate visually richer content.
    """
    np, _ = _lazy_imports()
    if not frames:
        return 0.0
    scores: List[float] = []
    for frame in frames:
        gray = _to_gray(frame)
        contrast = float(np.std(gray))
        dynamic_range = float(np.max(gray) - np.min(gray))
        scores.append(min(contrast / 50.0, 1.0) * 0.5 + min(dynamic_range / 128.0, 1.0) * 0.5)
    return float(np.mean(scores))


def compute_motion_score(frames: List[Any]) -> float:
    """Compute motion intensity score in [0.0, 1.0] via inter-frame pixel diff."""
    np, _ = _lazy_imports()
    if len(frames) < 2:
        return 0.0
    motion_scores: List[float] = []
    for i in range(len(frames) - 1):
        f1, f2 = _to_gray(frames[i]), _to_gray(frames[i + 1])
        motion_scores.append(min(float(np.mean(np.abs(f1 - f2))) / 50.0, 1.0))
    return float(np.mean(motion_scores))


def detect_ocr_overlay(frames: List[Any], threshold: float = 0.3) -> Dict[str, Any]:
    """Detect text / OCR overlay presence using edge-density heuristic.

    Returns dict with ``has_ocr``, ``confidence``, and ``affected_frames``.
    """
    np, _ = _lazy_imports()
    if not frames:
        return {"has_ocr": False, "confidence": 0.0, "affected_frames": []}
    ocr_detected: List[int] = []
    for idx, frame in enumerate(frames):
        gray = _to_gray(frame)
        h_edges = np.abs(np.diff(gray, axis=1))
        v_edges = np.abs(np.diff(gray, axis=0))
        text_score = float(np.mean(h_edges > 30)) * 0.5 + float(np.mean(v_edges > 30)) * 0.5
        if text_score > threshold:
            ocr_detected.append(idx)
    return {
        "has_ocr": len(ocr_detected) > 0,
        "confidence": float(len(ocr_detected) / len(frames)),
        "affected_frames": ocr_detected,
    }


def compute_camera_jitter(frames: List[Any]) -> float:
    """Compute camera-jitter score in [0.0, 1.0].

    Proxy: std-dev of inter-frame motion.  High variance = erratic movement.
    """
    np, _ = _lazy_imports()
    if len(frames) < 3:
        return 0.0
    diffs: List[float] = []
    for i in range(len(frames) - 1):
        f1, f2 = _to_gray(frames[i]), _to_gray(frames[i + 1])
        diffs.append(float(np.mean(np.abs(f1 - f2))))
    return min(float(np.std(diffs)) / 30.0, 1.0) if diffs else 0.0


# ---------------------------------------------------------------------------
# Aggregate scoring
# ---------------------------------------------------------------------------

def score_clip(frames: List[Any], ocr_threshold: float = 0.3) -> Dict[str, Any]:
    """Run all four scorers and return combined result with composite score."""
    aesthetic = compute_aesthetic_score(frames)
    motion = compute_motion_score(frames)
    ocr_result = detect_ocr_overlay(frames, threshold=ocr_threshold)
    jitter = compute_camera_jitter(frames)
    composite = (
        aesthetic * 0.40
        + motion * 0.25
        + (1.0 - ocr_result["confidence"]) * 0.20
        + (1.0 - jitter) * 0.15
    )
    return {
        "aesthetic": round(aesthetic, 4),
        "motion": round(motion, 4),
        "ocr": {
            "has_ocr": ocr_result["has_ocr"],
            "confidence": round(ocr_result["confidence"], 4),
            "affected_frames": ocr_result["affected_frames"],
        },
        "jitter": round(jitter, 4),
        "composite": round(composite, 4),
    }


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------

_VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp"}


def process_single(
    input_path: str,
    sample_frames: int = 32,
    ocr_threshold: float = 0.3,
) -> Dict[str, Any]:
    """Score a single video file or image directory."""
    p = Path(input_path)
    if p.is_file() and p.suffix.lower() in _VIDEO_EXTS:
        frames = _extract_frames_from_video(str(p), num_frames=sample_frames)
    elif p.is_dir():
        images = sorted(f for f in p.iterdir() if f.suffix.lower() in _IMAGE_EXTS)
        frames = _extract_frames_from_images([str(f) for f in images], max_frames=sample_frames)
    else:
        raise ValueError(f"Unsupported input: {input_path}")
    result = score_clip(frames, ocr_threshold=ocr_threshold)
    result["source"] = str(p)
    result["num_frames"] = len(frames)
    return result


def process_batch(
    input_dir: str,
    sample_frames: int = 32,
    ocr_threshold: float = 0.3,
) -> List[Dict[str, Any]]:
    """Score every video / image-set under *input_dir*."""
    root = Path(input_dir)
    results: List[Dict[str, Any]] = []
    for vid in sorted(root.glob("*")):
        if vid.is_file() and vid.suffix.lower() in _VIDEO_EXTS:
            try:
                results.append(process_single(str(vid), sample_frames, ocr_threshold))
            except Exception as exc:
                logging.error("Skipping %s: %s", vid, exc)
    for sub in sorted(root.iterdir()):
        if sub.is_dir():
            images = sorted(f for f in sub.iterdir() if f.suffix.lower() in _IMAGE_EXTS)
            if images:
                try:
                    frames = _extract_frames_from_images(
                        [str(f) for f in images], max_frames=sample_frames,
                    )
                    r = score_clip(frames, ocr_threshold=ocr_threshold)
                    r["source"] = str(sub)
                    r["num_frames"] = len(frames)
                    results.append(r)
                except Exception as exc:
                    logging.error("Skipping %s: %s", sub, exc)
    return results


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _write_json(results: Any, output_path: str) -> None:
    """Write results as pretty-printed JSON."""
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2, ensure_ascii=False)
    logging.info("Wrote JSON → %s", output_path)


def _write_csv(results: List[Dict[str, Any]], output_path: str) -> None:
    """Write batch results as CSV."""
    if not results:
        logging.warning("No results to write.")
        return
    fieldnames = ["source", "num_frames", "aesthetic", "motion",
                  "ocr_has_ocr", "ocr_confidence", "jitter", "composite"]
    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            writer.writerow({
                "source": r.get("source", ""),
                "num_frames": r.get("num_frames", 0),
                "aesthetic": r.get("aesthetic", 0.0),
                "motion": r.get("motion", 0.0),
                "ocr_has_ocr": r.get("ocr", {}).get("has_ocr", False),
                "ocr_confidence": r.get("ocr", {}).get("confidence", 0.0),
                "jitter": r.get("jitter", 0.0),
                "composite": r.get("composite", 0.0),
            })
    logging.info("Wrote CSV → %s", output_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser."""
    parser = argparse.ArgumentParser(
        prog="aesthetic_scorer",
        description="G159 — per-clip aesthetic / motion / OCR / jitter scorer",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--input", "-i", type=str, help="Single video file or image directory"
    )
    group.add_argument(
        "--batch", "-b", type=str, help="Directory for batch scoring"
    )
    parser.add_argument(
        "--output", "-o", type=str, required=True, help="Output file (.json or .csv)"
    )
    parser.add_argument(
        "--sample-frames",
        type=int,
        default=32,
        help="Frames per clip (default: 32)",
    )
    parser.add_argument(
        "--ocr-threshold",
        type=float,
        default=0.3,
        help="OCR edge-density threshold",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable debug logging"
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point — parse args, run scoring, write output."""
    parser = build_parser()
    args = parser.parse_args(argv)
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(asctime)s [%(levelname)s] %(message)s")

    try:
        _lazy_imports()
    except ImportError as exc:
        logging.error("Missing dependency: %s", exc)
        return 1

    out_ext = Path(args.output).suffix.lower()
    try:
        if args.input:
            result = process_single(args.input, args.sample_frames, args.ocr_threshold)
            if out_ext == ".csv":
                _write_csv([result], args.output)
            else:
                _write_json(result, args.output)
        elif args.batch:
            results = process_batch(args.batch, args.sample_frames, args.ocr_threshold)
            if out_ext == ".json":
                _write_json(results, args.output)
            else:
                _write_csv(results, args.output)
    except Exception as exc:
        logging.error("Scoring failed: %s", exc, exc_info=args.verbose)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
