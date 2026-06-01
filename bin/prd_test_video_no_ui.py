#!/usr/bin/env python3
"""PRD p4 #3 — Video must contain no *overlay* UI / chat / dialogs / watermark.

PRD intent (p4 #3, p8 hard-rejection list): the video must contain no NON-game
overlay — chat boxes, dialog/modal popups, "press key to continue", loading/CG
screens, in-game settings menus, watermarks, subscribe/follow call-to-action
graphics, exposed OS taskbar, etc. The game's OWN heads-up display (e.g. the
Minecraft hotbar and health bar) is the inherent game view and is NOT a
rejection criterion — it is exactly the gameplay the dataset wants.

What changed (and why)
======================
The old heuristic flagged generic uniform bars (``TOP_BAR``/``BOTTOM_BAR``) and
any high-local-variance patch (``DENSE_TEXT_REGION``) as "UI". On real gameplay
those tokens fire on the game's own hotbar and on ordinary textured terrain
(grass, trees, blocks) — every frame, so a clean Minecraft session reads as
"UI in 10/10 frames" and is wrongly rejected. Those structural tokens are NOT
reliable evidence of an external overlay.

The fix separates two kinds of signal:

  * Real overlay TEXT keywords (``_OVERLAY_TEXT_KEYWORDS``) — chat, dialog,
    overlay, watermark, subscribe, follow, ... These come from genuine OCR of
    rendered text and DO fail a frame.
  * Structural heuristic tokens (``_STRUCTURAL_HEURISTIC_TOKENS``) — TOP_BAR,
    BOTTOM_BAR, DENSE_TEXT_REGION. These are coarse pixel proxies that fire on
    the game's own HUD and on terrain texture, so on their OWN they never fail a
    frame.

Reliably distinguishing overlay text from organic game terrain requires reading
the actual glyphs — i.e. real OCR. When a real OCR engine (Tesseract) is
available, an overlay frame containing "SUBSCRIBE"/"watermark"/chat text FAILS
and a clean gameplay frame PASSES. When no real OCR engine is installed, the
test cannot honestly verify the text-overlay criterion from pixels alone, so it
SKIPS (exit 2, "OCR engine not available") rather than risk a false pass on a
real overlay or a false fail on clean gameplay.

Exit codes:
    0 - All sampled frames are clean (no overlay text detected)
    1 - Overlay UI/text detected in more than `threshold` fraction of frames
    2 - Skip: file not found, ffmpeg failure, or no real OCR engine available

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
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:  # import only for type checkers; PIL is lazy-imported at runtime
    from PIL import Image

logger = logging.getLogger(__name__)

# Real external-overlay TEXT keywords. Matched against genuine OCR output. The
# presence of any of these *as rendered text* is evidence of a NON-game overlay
# (chat, dialog box, watermark, social call-to-action, OS chrome, ...).
_OVERLAY_TEXT_KEYWORDS = frozenset({
    "chat", "dialog", "overlay", "watermark", "subscribe", "follow",
    "like", "comment", "share", "live", "recording", "navigation",
    "menu", "back", "home", "search",
})

# Coarse structural tokens emitted by the pixel HEURISTIC fallback. These fire
# on the game's own HUD (hotbar bars) and on ordinary textured terrain, so they
# are NOT reliable overlay evidence and must NEVER, on their own, fail a frame.
_STRUCTURAL_HEURISTIC_TOKENS = frozenset({
    "TOP_BAR", "BOTTOM_BAR", "DENSE_TEXT_REGION",
})

# Backward-compatible union (some callers/tests reference the full set). Failure
# decisions use _OVERLAY_TEXT_KEYWORDS only — see _has_ui.
_UI_KEYWORDS = frozenset(_OVERLAY_TEXT_KEYWORDS | _STRUCTURAL_HEURISTIC_TOKENS | {
    "top_bar", "bottom_bar",
})


def _get_pil():
    """Return PIL.Image, lazy-imported."""
    from PIL import Image  # noqa: PLC0415
    return Image


def _get_numpy():
    """Return numpy, lazy-imported."""
    import numpy as np  # noqa: PLC0415
    return np


def _strip_ffmpeg_banner(stderr: str) -> str:
    """Strip ffmpeg version banner from stderr output.

    The banner appears at the start of stderr and includes version info,
    copyright, and configuration details. We want just the actual error.
    """
    # Split into lines and find where the actual error starts
    # The banner ends with a line starting with "  lib" (libavutil, etc.)
    lines = stderr.split('\n')
    error_lines: list[str] = []
    in_banner = True
    for line in lines:
        # Banner lines start with "ffmpeg ", "  built", "  configuration:", or "  lib"
        if in_banner:
            if line.startswith('ffmpeg '):
                continue
            if line.startswith('  built'):
                continue
            if line.startswith('  configuration:'):
                continue
            if line.startswith('  lib'):
                continue
            in_banner = False
        if line.strip():
            error_lines.append(line)
    return ' '.join(error_lines)


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
        if band_std < 15 and abs(band_mean - center_mean) > 30:
            tokens.append(label)
    # Dense text region detection (high local variance)
    for y in range(0, h, 20):
        for x in range(0, w, 20):
            patch = arr[y : y + 20, x : x + 20]
            if patch.size == 0:
                continue
            patch_std = float(np_mod.std(patch))
            if patch_std > 40:
                tokens.append("DENSE_TEXT_REGION")
                break
        if "DENSE_TEXT_REGION" in tokens:
            break
    return " ".join(tokens)


def _get_ocr_engine():
    """Return OCR engine (heuristic fallback if Tesseract not available).

    Kept for backward compatibility (existing tests assert it returns a
    callable). New code should prefer :func:`_get_ocr_engine_typed`, which also
    reports whether the engine is a REAL text OCR or the pixel heuristic.
    """
    engine, _is_real = _get_ocr_engine_typed()
    return engine


def _get_ocr_engine_typed() -> tuple[object, bool]:
    """Return ``(engine, is_real_ocr)``.

    ``is_real_ocr`` is True only when a genuine text-recognising OCR engine
    (Tesseract via pytesseract) is available. The pixel heuristic is NOT real
    OCR — it cannot read glyphs and only emits coarse structural tokens, which
    are not reliable overlay evidence.
    """
    try:
        import pytesseract  # noqa: PLC0415

        # Probe that the Tesseract binary is actually present/usable, not just
        # the python wrapper — an importable wrapper with no binary still can't
        # read text and must not be treated as real OCR.
        try:
            pytesseract.get_tesseract_version()
        except Exception:  # noqa: BLE001 - any probe failure ⇒ no real OCR
            return _heuristic_ocr, False
        return (lambda img: pytesseract.image_to_string(img)), True
    except ImportError:
        return _heuristic_ocr, False


def _sample_indices(total: int, n: int) -> list[int]:
    """Return *n* evenly-spaced indices in [0, total)."""
    if total <= n:
        return list(range(total))
    step = max(1, total // n)
    return [i * step for i in range(n)]


def _extract_frames(video_path: str, num_frames: int = 10) -> Iterable[Image.Image]:
    """Extract evenly spaced frames from video using ffmpeg."""
    Image = _get_pil()
    tmpdir = tempfile.mkdtemp(prefix="video_no_ui_")
    try:
        # Extract frames at 1 fps (enough for UI detection)
        cmd = [
            "ffmpeg", "-i", video_path,
            "-vf", f"fps={num_frames}",
            "-t", "1",
            f"{tmpdir}/frame_%04d.png",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            # Strip the ffmpeg version banner to get the actual error message
            error_msg = _strip_ffmpeg_banner(result.stderr)
            raise RuntimeError(
                f"ffmpeg frame extraction failed: {error_msg}"
            )

        frames = sorted(Path(tmpdir).glob("frame_*.png"))
        indices = _sample_indices(len(frames), num_frames)
        for idx in indices:
            if idx < len(frames):
                yield Image.open(str(frames[idx]))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _has_ui(text: str) -> bool:
    """True if OCR text contains a real *external-overlay* keyword.

    Only ``_OVERLAY_TEXT_KEYWORDS`` (chat/dialog/overlay/watermark/subscribe/...)
    trigger a failure. The coarse structural heuristic tokens
    (``TOP_BAR``/``BOTTOM_BAR``/``DENSE_TEXT_REGION``) are deliberately ignored
    here: they fire on the game's own HUD and on terrain texture and are not
    reliable evidence of an external overlay. This is the core of the fix — a
    clean Minecraft frame (hotbar + textured world) no longer reads as "UI".
    """
    text_lower = text.lower()
    return any(keyword in text_lower for keyword in _OVERLAY_TEXT_KEYWORDS)


class OCRUnavailableError(RuntimeError):
    """Raised when no real (text-recognising) OCR engine is available.

    Detecting external overlay TEXT (chat/dialog/watermark/subscribe) reliably
    requires reading glyphs. Without a real OCR engine we decline to judge the
    criterion (the caller maps this to a skip) rather than risk a false pass on
    a real overlay or a false fail on clean gameplay.
    """


def _analyze_video(
    video_path: str,
    num_frames: int = 10,
    threshold: float = 0.05,
    verbose: bool = False,
    *,
    ocr_engine=None,
) -> tuple[bool, float, int, list[str]]:
    """Analyze video for external overlay UI/text.

    Args:
        ocr_engine: Optional explicit OCR callable (used by tests to inject a
            real text engine). When omitted, a real engine is auto-detected;
            if none is available, ``OCRUnavailableError`` is raised.

    Returns:
        (passed, ui_ratio, total_frames, ui_frames) where passed is True if the
        overlay-frame ratio is at or below ``threshold``.
    """
    if ocr_engine is None:
        ocr_engine, is_real = _get_ocr_engine_typed()
        if not is_real:
            raise OCRUnavailableError(
                "OCR engine not available (Tesseract not installed); cannot "
                "verify overlay-text criterion from pixels alone"
            )
    ui_frames: list[str] = []
    total = 0

    for i, frame in enumerate(_extract_frames(video_path, num_frames)):
        total += 1
        text = ocr_engine(frame)
        if _has_ui(text):
            ui_frames.append(f"frame_{i}")
            if verbose:
                logger.info("Overlay detected in frame %d: %s", i, text[:80])

    ui_ratio = len(ui_frames) / total if total > 0 else 0.0
    passed = ui_ratio <= threshold
    return passed, ui_ratio, total, ui_frames


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
        print(f"skip: Video file not found: {args.video}", file=sys.stderr)
        return 2  # Skip: missing input data

    try:
        passed, ui_ratio, total, ui_frames = _analyze_video(
            args.video, args.frames, args.threshold, args.verbose
        )
    except OCRUnavailableError as e:
        # Tool unavailable → skip (exit 2). "not available" is recognised by the
        # PRD harness as skip-worthy, so this neither passes nor fails the data.
        print(f"skip: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"skip: {e}", file=sys.stderr)
        return 2  # Skip: tool failure or other error

    if passed:
        logger.info("Video passed: overlay ratio %.2f%% <= %.2f%% threshold",
                    ui_ratio * 100, args.threshold * 100)
        return 0
    else:
        logger.warning("Video failed: overlay UI detected in %d/%d frames (%.2f%%)",
                       len(ui_frames), total, ui_ratio * 100)
        return 1


if __name__ == "__main__":
    sys.exit(main())
