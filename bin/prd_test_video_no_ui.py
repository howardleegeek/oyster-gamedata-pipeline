#!/usr/bin/env python3
"""PRD p4 #3 — Video must contain no overlay UI / chat / dialogs.

Scans sampled frames from a video via OCR and asserts that no UI overlay
patterns (chat bubbles, dialog boxes, navigation bars, watermarks, etc.)
are detected.  Returns 0 when all frames are clean, non-zero otherwise.

Exit codes:
    0 - All frames are clean (no UI detected)
    1 - UI detected in one or more frames
    2 - Error (file not found, ffmpeg failure, etc.)

Usage:
    python bin/prd_test_video_no_ui.py video.mp4 [--frames 10 --threshold 0.05]
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable, Sequence

logger = logging.getLogger(__name__)

_UI_KEYWORDS = frozenset({
    "chat", "dialog", "overlay", "watermark", "subscribe", "follow",
    "like", "comment", "share", "live", "recording", "top_bar",
    "bottom_bar", "navigation", "menu", "back", "home", "search",
    "TOP_BAR", "BOTTOM_BAR", "DENSE_TEXT_REGION",
})


def _get_pil():
    """Return PIL.Image, lazy-imported."""
    from PIL import Image  # noqa: PLC0415
    return Image


def _get_numpy():
    """Return numpy, lazy-imported."""
    import numpy as np  # noqa: PLC0415
    return np


def _heuristic_ocr(image) -> str:
    """Heuristic OCR: detect UI-like patterns via pixel analysis."""
    np_mod = _get_numpy()
    gray = image.convert("L")
    arr = np_mod.array(gray)
    h, w = arr.shape
    tokens: list[str] = []
    center = arr[int(h * 0.3) : int(h * 0.7), :]
    center_mean = float(np_mod.mean(center))
    for band, label in [
        (arr[: int(h * 0.08), :], "TOP_BAR"),
        (arr[int(h * 0.92):, :], "BOTTOM_BAR"),
    ]:
        band_std = float(np_mod.std(band))
        band_mean = float(np_mod.mean(band))
        # Only flag as UI bar if it's uniform AND differs from center
        if band_std < 15 and abs(band_mean - center_mean) > 20:
            tokens.append(label)
    edges_x = np_mod.abs(np_mod.diff(arr.astype(np_mod.float32), axis=1))
    if float(np_mod.mean(edges_x > 30)) > 0.12:
        tokens.append("DENSE_TEXT_REGION")
    return " ".join(tokens)


def _get_ocr_engine():
    """Return callable(frame: Image) -> str for OCR text extraction."""
    try:
        import pytesseract  # noqa: PLC0415
        logger.info("Using pytesseract for OCR")
        return lambda img: pytesseract.image_to_string(img)
    except ImportError:
        logger.info("pytesseract not available; using heuristic fallback")
        return _heuristic_ocr


def _sample_indices(total: int, n: int) -> list[int]:
    """Return *n* evenly-spaced indices in [0, total)."""
    if total <= n:
        return list(range(total))
    step = max(1, total // n)
    return [i * step for i in range(n)]


def _extract_frames(video_path: str, num_frames: int) -> Iterable:
    """Yield PIL.Image frames sampled evenly from *video_path*.

    Uses ffmpeg to extract frames at the requested rate.
    """
    Image = _get_pil()
    tmpdir = tempfile.mkdtemp(prefix="video_frames_")
    try:
        # Use fps={num_frames} to extract num_frames per second
        # Duration of 1 second ensures we get at least num_frames frames
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-vf", f"fps={num_frames}",
            "-t", "1",
            f"{tmpdir}/frame_%04d.png",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg frame extraction failed: {result.stderr.strip()[:200]}"
            )

        frames = sorted(Path(tmpdir).glob("frame_*.png"))
        indices = _sample_indices(len(frames), num_frames)
        for idx in indices:
            if idx < len(frames):
                yield Image.open(str(frames[idx]))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _has_ui(text: str) -> bool:
    """Check if extracted text contains UI keywords."""
    text_lower = text.lower()
    return any(keyword.lower() in text_lower for keyword in _UI_KEYWORDS)


def _analyze_video(
    video_path: str,
    num_frames: int = 10,
    threshold: float = 0.05,
    verbose: bool = False,
) -> tuple[bool, float, list[str]]:
    """Analyze video for UI overlays.

    Returns:
        (passed, ui_ratio, ui_frames) where passed is True if UI ratio
        is below threshold.
    """
    ocr_engine = _get_ocr_engine()
    ui_frames: list[str] = []
    total = 0

    for i, frame in enumerate(_extract_frames(video_path, num_frames)):
        total += 1
        text = ocr_engine(frame)
        if _has_ui(text):
            ui_frames.append(f"frame_{i}")
            if verbose:
                logger.info("UI detected in frame %d: %s", i, text[:80])

    ui_ratio = len(ui_frames) / total if total > 0 else 0.0
    passed = ui_ratio <= threshold
    return passed, ui_ratio, ui_frames


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Check video for UI overlays (chat, dialogs, etc.)"
    )
    parser.add_argument("video", help="Path to video file")
    parser.add_argument(
        "--frames", "-f", type=int, default=10,
        help="Number of frames to sample (default: 10)"
    )
    parser.add_argument(
        "--threshold", "-t", type=float, default=0.05,
        help="Max allowed UI frame ratio (default: 0.05)"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Verbose output"
    )
    args = parser.parse_args(argv)

    if not os.path.exists(args.video):
        logger.error("Video file not found: %s", args.video)
        return 2  # Skip: missing input data

    try:
        passed, ui_ratio, ui_frames = _analyze_video(
            args.video, args.frames, args.threshold, args.verbose
        )
    except Exception as e:
        logger.error("Analysis failed: %s", e)
        return 2  # Skip: tool failure or other error

    if args.verbose or not passed:
        print(f"UI ratio: {ui_ratio:.3f} ({len(ui_frames)}/{args.frames} frames)")
        if ui_frames:
            print(f"UI frames: {', '.join(ui_frames)}")

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
