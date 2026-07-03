#!/usr/bin/env python3
"""
bin/clip_validator_strict.py

Stricter clip-level validator for video/audio quality assessment.
Cross-checks: audio mute/silent, black-frame rate, repeated-frame rate, motion entropy.

Usage:
    python clip_validator_strict.py <video_path> [options]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

_numpy: Any = None
_pil_image: Any = None


def _get_numpy() -> Any:
    """Lazy import numpy."""
    global _numpy
    if _numpy is None:
        import numpy as np
        _numpy = np
    return _numpy


def _get_pil_image() -> Any:
    """Lazy import PIL.Image."""
    global _pil_image
    if _pil_image is None:
        from PIL import Image
        _pil_image = Image
    return _pil_image


@dataclass
class ValidationResult:
    """Container for all validation metrics of a single clip."""
    video_path: str
    audio_mute_ratio: float = 0.0
    audio_silent_ratio: float = 0.0
    black_frame_ratio: float = 0.0
    repeated_frame_ratio: float = 0.0
    motion_entropy: float = 0.0
    is_valid: bool = True
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict for JSON output."""
        return {
            "video_path": self.video_path,
            "audio_mute_ratio": round(self.audio_mute_ratio, 4),
            "audio_silent_ratio": round(self.audio_silent_ratio, 4),
            "black_frame_ratio": round(self.black_frame_ratio, 4),
            "repeated_frame_ratio": round(self.repeated_frame_ratio, 4),
            "motion_entropy": round(self.motion_entropy, 4),
            "is_valid": self.is_valid,
            "warnings": self.warnings,
            "errors": self.errors,
            "metadata": self.metadata,
        }


@dataclass
class ValidationThresholds:
    """Configurable pass/fail thresholds."""
    max_audio_mute_ratio: float = 0.95
    max_audio_silent_ratio: float = 0.90
    max_black_frame_ratio: float = 0.80
    max_repeated_frame_ratio: float = 0.50
    min_motion_entropy: float = 0.1
    black_pixel_threshold: int = 16
    silent_db_threshold: float = -60.0


def _has_audio_stream(video_path: str) -> bool:
    """Return True if the file contains at least one audio stream."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", "-select_streams", "a", video_path],
            capture_output=True, text=True, timeout=30,
        )
        return len(json.loads(result.stdout).get("streams", [])) > 0
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return False


def _parse_db_value(text: str, key: str) -> Optional[float]:
    """Extract a dB value like 'mean_volume: -30.5 dB' from ffmpeg output."""
    for line in text.splitlines():
        if key in line and "db" in line:
            try:
                return float(line.split(":")[1].strip().replace("db", "").strip())
            except ValueError as exc:
                logger.debug(
                    "could not parse dB value for key %r on line %r: %s",
                    key, line, exc,
                )
    return None


def compute_audio_metrics(
    video_path: str, thresholds: ValidationThresholds,
) -> Tuple[float, float]:
    """
    Compute audio mute and silent ratios via ffprobe volumedetect.
    Returns (mute_ratio, silent_ratio) each in [0, 1].
    """
    if not _has_audio_stream(video_path):
        logger.info("No audio stream found; marking as fully mute/silent.")
        return 1.0, 1.0

    try:
        result = subprocess.run(
            ["ffmpeg", "-i", video_path, "-af", "volumedetect",
             "-f", "null", "/dev/null"],
            capture_output=True, text=True, timeout=120,
        )
        stderr = result.stderr.lower()
        mean_vol = _parse_db_value(stderr, "mean_volume")
        max_vol = _parse_db_value(stderr, "max_volume")

        silent_ratio = 0.0
        if mean_vol is not None:
            if mean_vol < thresholds.silent_db_threshold:
                silent_ratio = min(1.0, abs(mean_vol) / abs(thresholds.silent_db_threshold))
            else:
                silent_ratio = max(0.0, 1.0 - (mean_vol / -20.0))

        mute_ratio = 0.0
        if max_vol is not None:
            if max_vol < thresholds.silent_db_threshold - 10:
                mute_ratio = 1.0
            elif max_vol < thresholds.silent_db_threshold:
                mute_ratio = 0.5

        return mute_ratio, silent_ratio
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.warning("ffmpeg error: %s", exc)
        return 0.0, 0.0


def _get_video_info(video_path: str) -> Dict[str, Any]:
    """Probe video for duration, fps, width, height via ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", "-select_streams", "v:0", video_path],
            capture_output=True, text=True, timeout=30,
        )
        streams = json.loads(result.stdout).get("streams", [])
        if streams:
            s = streams[0]
            fps_str = s.get("r_frame_rate", "0/1")
            try:
                num, den = fps_str.split("/")
                fps = float(num) / float(den)
            except (ValueError, ZeroDivisionError):
                fps = 0.0
            return {
                "width": int(s.get("width", 0)),
                "height": int(s.get("height", 0)),
                "fps": fps,
                "duration": float(s.get("duration", 0)),
            }
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass
    return {}


def compute_video_metrics(
    video_path: str, thresholds: ValidationThresholds,
    sample_interval: float = 1.0,
) -> Tuple[float, float, float]:
    """
    Extract frames at regular intervals and compute black-frame ratio,
    repeated-frame ratio, and motion entropy.
    """
    np = _get_numpy()
    Image = _get_pil_image()

    info = _get_video_info(video_path)
    duration = info.get("duration", 0)
    if duration <= 0:
        logger.warning("Could not determine video duration.")
        return 0.0, 0.0, 0.0

    tmpdir = tempfile.mkdtemp(prefix="clipval_")
    pattern = os.path.join(tmpdir, "frame_%06d.png")

    try:
        subprocess.run(
            ["ffmpeg", "-i", video_path, "-vf", f"fps=1/{sample_interval}",
             "-q:v", "2", pattern],
            capture_output=True, text=True, timeout=120,
        )
        frames = sorted(f for f in os.listdir(tmpdir) if f.endswith(".png"))
        if not frames:
            logger.warning("No frames extracted from video.")
            return 0.0, 0.0, 0.0

        hashes: List[str] = []
        black_count = 0
        entropy_values: List[float] = []
        prev_arr: Optional[np.ndarray] = None

        for fname in frames:
            img = Image.open(os.path.join(tmpdir, fname)).convert("L")
            arr = np.array(img)

            if float(np.mean(arr)) < thresholds.black_pixel_threshold:
                black_count += 1

            hashes.append(hashlib.md5(arr.tobytes()).hexdigest())

            if prev_arr is not None:
                diff = np.abs(arr.astype(np.int16) - prev_arr.astype(np.int16))
                hist, _ = np.histogram(diff.flatten(), bins=256, range=(0, 256))
                probs = hist / hist.sum()
                probs = probs[probs > 0]
                entropy_values.append(float(-np.sum(probs * np.log2(probs))))

            prev_arr = arr

        total = len(frames)
        black_ratio = black_count / total if total else 0.0

        repeat_count = sum(1 for i in range(1, len(hashes)) if hashes[i] == hashes[i - 1])
        repeated_ratio = repeat_count / max(total - 1, 1)

        avg_entropy = float(np.mean(entropy_values)) if entropy_values else 0.0
        return black_ratio, repeated_ratio, avg_entropy

    except subprocess.TimeoutExpired:
        logger.error("Frame extraction timed out.")
        return 0.0, 0.0, 0.0
    except FileNotFoundError:
        logger.error("ffmpeg not found on PATH.")
        return 0.0, 0.0, 0.0
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def validate_clip(
    video_path: str,
    thresholds: Optional[ValidationThresholds] = None,
    sample_interval: float = 1.0,
) -> ValidationResult:
    """Run all validation checks on a single video clip."""
    if thresholds is None:
        thresholds = ValidationThresholds()

    result = ValidationResult(video_path=video_path)

    if not os.path.isfile(video_path):
        result.errors.append(f"File not found: {video_path}")
        result.is_valid = False
        return result

    result.metadata["file_size_bytes"] = os.path.getsize(video_path)

    mute_ratio, silent_ratio = compute_audio_metrics(video_path, thresholds)
    result.audio_mute_ratio = mute_ratio
    result.audio_silent_ratio = silent_ratio

    if mute_ratio >= thresholds.max_audio_mute_ratio:
        result.warnings.append(
            f"Audio mute ratio {mute_ratio:.2%} exceeds threshold {thresholds.max_audio_mute_ratio:.2%}")
    if silent_ratio >= thresholds.max_audio_silent_ratio:
        result.warnings.append(
            f"Audio silent ratio {silent_ratio:.2%} exceeds threshold {thresholds.max_audio_silent_ratio:.2%}")

    black_ratio, repeated_ratio, motion_ent = compute_video_metrics(
        video_path, thresholds, sample_interval)
    result.black_frame_ratio = black_ratio
    result.repeated_frame_ratio = repeated_ratio
    result.motion_entropy = motion_ent

    if black_ratio >= thresholds.max_black_frame_ratio:
        result.warnings.append(
            f"Black frame ratio {black_ratio:.2%} exceeds threshold {thresholds.max_black_frame_ratio:.2%}")
    if repeated_ratio >= thresholds.max_repeated_frame_ratio:
        result.warnings.append(
            f"Repeated frame ratio {repeated_ratio:.2%} exceeds threshold {thresholds.max_repeated_frame_ratio:.2%}")
    if motion_ent < thresholds.min_motion_entropy:
        result.warnings.append(
            f"Motion entropy {motion_ent:.4f} below threshold {thresholds.min_motion_entropy}")

    result.is_valid = len(result.warnings) == 0 and len(result.errors) == 0
    return result


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the CLI."""
    parser = argparse.ArgumentParser(
        description="Strict clip-level validator for video/audio quality.")
    parser.add_argument("video_path", type=str, help="Path to the video file.")
    parser.add_argument("--sample-interval", type=float, default=1.0,
                        help="Seconds between sampled frames (default: 1.0).")
    parser.add_argument("--max-audio-mute", type=float, default=0.95,
                        help="Maximum allowed audio mute ratio.")
    parser.add_argument("--max-audio-silent", type=float, default=0.90,
                        help="Maximum allowed audio silent ratio.")
    parser.add_argument("--max-black-frame", type=float, default=0.80,
                        help="Maximum allowed black frame ratio.")
    parser.add_argument("--max-repeated-frame", type=float, default=0.50,
                        help="Maximum allowed repeated frame ratio.")
    parser.add_argument("--min-motion-entropy", type=float, default=0.1,
                        help="Minimum required motion entropy.")
    parser.add_argument("--json", action="store_true", help="Output as JSON.")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging.")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """
    Entry point for the clip validator CLI.
    Returns: 0 if valid, 1 if invalid, 2 on error.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    thresholds = ValidationThresholds(
        max_audio_mute_ratio=args.max_audio_mute,
        max_audio_silent_ratio=args.max_audio_silent,
        max_black_frame_ratio=args.max_black_frame,
        max_repeated_frame_ratio=args.max_repeated_frame,
        min_motion_entropy=args.min_motion_entropy,
    )

    try:
        result = validate_clip(args.video_path, thresholds=thresholds,
                               sample_interval=args.sample_interval)
    except Exception as exc:
        logger.error("Validation failed: %s", exc)
        return 2

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(f"Video: {result.video_path}")
        print(f"  Audio mute ratio:      {result.audio_mute_ratio:.4f}")
        print(f"  Audio silent ratio:    {result.audio_silent_ratio:.4f}")
        print(f"  Black frame ratio:     {result.black_frame_ratio:.4f}")
        print(f"  Repeated frame ratio:  {result.repeated_frame_ratio:.4f}")
        print(f"  Motion entropy:        {result.motion_entropy:.4f}")
        print(f"  Valid:                 {result.is_valid}")
        for w in result.warnings:
            print(f"  Warning: {w}")
        for e in result.errors:
            print(f"  Error: {e}")

    return 0 if result.is_valid else 1


if __name__ == "__main__":
    sys.exit(main())
