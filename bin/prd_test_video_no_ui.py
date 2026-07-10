#!/usr/bin/env python3
"""PRD p4 #3 — Video must contain no overlay UI / chat / dialogs.

Scans sampled frames from a video via OCR and asserts that no UI overlay
patterns (chat bubbles, dialog boxes, navigation bars, watermarks, etc.)
are detected.  Returns 0 when all frames are clean, non-zero otherwise.

Usage:
    python bin/prd_test_video_no_ui.py video.mp4 [--frames 10 --threshold 0.05]
"""

from __future__ import annotations

import argparse
import logging
import os
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
    for band, label in [
        (arr[: int(h * 0.08), :], "TOP_BAR"),
        (arr[int(h * 0.92):, :], "BOTTOM_BAR"),
    ]:
        if float(np_mod.std(band)) < 15:
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
    except ImportError as exc:
        logger.warning("pytesseract not available; using heuristic fallback: %s", exc)
        return _heuristic_ocr


def _sample_indices(total: int, n: int) -> list[int]:
    """Return *n* evenly-spaced indices in [0, total)."""
    if total <= n:
        return list(range(total))
    step = max(1, total // n)
    return [i * step for i in range(n)]


def _extract_frames(video_path: str, num_frames: int) -> Iterable:
    """Yield PIL.Image frames sampled evenly from *video_path*."""
    Image = _get_pil()
    try:
        vid = Image.open(video_path)
        total = getattr(vid, "n_frames", 1)
        for idx in _sample_indices(total, num_frames):
            vid.seek(idx)
            yield vid.copy()
        vid.close()
        return
    except OSError as e:
        logger.warning("PIL cannot open %s: %s; will try ffmpeg fallback", video_path, e)
    except Exception as e:
        logger.warning("PIL failed to extract frames from %s: %s; will try ffmpeg fallback", video_path, e)

    tmpdir = tempfile.mkdtemp(prefix="g069_frames_")
    pattern = os.path.join(tmpdir, "frame_%04d.png")
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", "fps=1/10,scale=640:-1",
        "-frames:v", str(num_frames), "-q:v", "2", pattern,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    except FileNotFoundError as exc:
        logger.error("ffmpeg not found; cannot extract frames from %s: %s", video_path, exc)
        return
    except subprocess.TimeoutExpired as exc:
        logger.error("ffmpeg timed out on %s: %s", video_path, exc)
        return

    for fname in sorted(Path(tmpdir).glob("frame_*.png")):
        yield Image.open(str(fname))


def _frame_has_ui(image, ocr_fn) -> tuple[bool, str]:
    """Return (has_ui, detail) for a single frame."""
    text = ocr_fn(image).lower()
    found = [kw for kw in _UI_KEYWORDS if kw.lower() in text]
    if found:
        return True, f"UI keywords detected: {', '.join(found)}"
    return False, "clean"


def main(argv: Sequence[str] | None = None) -> int:
    """Entry-point: parse args, scan video, return 0 on success."""
    parser = argparse.ArgumentParser(
        description="Assert that a video contains no overlay UI / chat / dialogs.",
    )
    parser.add_argument("video", help="Path to the video file to scan")
    parser.add_argument("--frames", type=int, default=10,
                        help="Number of frames to sample (default: 10)")
    parser.add_argument("--threshold", type=float, default=0.05,
                        help="Max fraction of frames with UI (default: 0.05)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Enable debug logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    if not os.path.isfile(args.video):
        logger.error("Video file not found: %s", args.video)
        return 2

    ocr_fn = _get_ocr_engine()
    total = 0
    ui_count = 0

    for idx, frame in enumerate(_extract_frames(args.video, args.frames)):
        total += 1
        has_ui, detail = _frame_has_ui(frame, ocr_fn)
        if has_ui:
            ui_count += 1
            logger.warning("Frame %d: %s", idx, detail)
        else:
            logger.debug("Frame %d: %s", idx, detail)

    if total == 0:
        logger.error("No frames could be extracted from %s", args.video)
        return 2

    frac = ui_count / total
    logger.info(
        "Scanned %d frames — %d with UI overlays (%.1f%%, threshold %.1f%%)",
        total, ui_count, frac * 100, args.threshold * 100,
    )

    if frac > args.threshold:
        logger.error("FAIL: UI overlay fraction %.1f%% exceeds threshold %.1f%%",
                     frac * 100, args.threshold * 100)
        return 1

    logger.info("PASS: video is clean of overlay UI")
    return 0


if __name__ == "__main__":
    sys.exit(main())
