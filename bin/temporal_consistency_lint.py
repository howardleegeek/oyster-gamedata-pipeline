#!/usr/bin/env python3
"""
temporal_consistency_lint.py — G171 v4 temporal artifact detector.

Detects frame-to-frame discontinuity > 30 % in optical-flow magnitude.
Buyers reject jitter; this lint flags sequences where consecutive-frame
flow changes exceed the configured threshold.

Usage:
    python3 bin/temporal_consistency_lint.py --frames /path/to/frames/
    python3 bin/temporal_consistency_lint.py --frames /path/to/frames/ --threshold 0.25

Exit codes:
    0 — all frames pass (no temporal artifacts detected)
    1 — one or more frames exceed the discontinuity threshold
    2 — usage / I/O error
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Sequence, Tuple

if TYPE_CHECKING:
    import numpy as np

# ---------------------------------------------------------------------------
# Lazy imports — heavy deps only loaded when actually needed
# ---------------------------------------------------------------------------

_numpy: Optional[object] = None
_PIL_Image: Optional[object] = None


def _get_numpy():
    """Return numpy module, importing lazily."""
    global _numpy
    if _numpy is None:
        import numpy as np  # type: ignore[import]
        _numpy = np
    return _numpy


def _get_pil_image():
    """Return PIL.Image module, importing lazily."""
    global _PIL_Image
    if _PIL_Image is None:
        from PIL import Image  # type: ignore[import]
        _PIL_Image = Image
    return _PIL_Image


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

SUPPORTED_EXTENSIONS: Tuple[str, ...] = (
    ".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp",
)

logger = logging.getLogger(__name__)


def _load_frame(path: Path) -> "np.ndarray":
    """Load an image file and return it as a grayscale float32 array."""
    np = _get_numpy()
    Image = _get_pil_image()
    img = Image.open(str(path)).convert("L")  # grayscale
    return np.asarray(img, dtype=np.float32)


def _compute_flow_magnitude(
    prev: "np.ndarray",
    curr: "np.ndarray",
) -> "np.ndarray":
    """
    Approximate optical-flow magnitude between two consecutive frames.

    Uses a simple gradient-based approach:
      - Compute spatial gradients (Sobel-like) for each frame.
      - Estimate per-pixel displacement via brightness-constancy equation.
      - Return the magnitude map (same shape as input).
    """
    np = _get_numpy()

    # Spatial gradients via central differences
    gx_prev = np.zeros_like(prev)
    gy_prev = np.zeros_like(prev)
    gx_curr = np.zeros_like(curr)
    gy_curr = np.zeros_like(curr)

    gx_prev[:, 1:-1] = (prev[:, 2:] - prev[:, :-2]) / 2.0
    gy_prev[1:-1, :] = (prev[2:, :] - prev[:-2, :]) / 2.0
    gx_curr[:, 1:-1] = (curr[:, 2:] - curr[:, :-2]) / 2.0
    gy_curr[1:-1, :] = (curr[2:, :] - curr[:-2, :]) / 2.0

    # Average gradient across the pair
    gx_avg = (gx_prev + gx_curr) / 2.0
    gy_avg = (gy_prev + gy_curr) / 2.0

    # Temporal difference
    dt = curr - prev

    # Flow components (Lucas-Kanade simplified, per-pixel)
    denom = gx_avg ** 2 + gy_avg ** 2 + 1e-6
    u = -(gx_avg * dt) / denom  # horizontal flow
    v = -(gy_avg * dt) / denom  # vertical flow

    return np.sqrt(u ** 2 + v ** 2)


def _mean_flow(flow: "np.ndarray") -> float:
    """Return the mean flow magnitude, ignoring border pixels."""
    np = _get_numpy()
    # Exclude 1-pixel border where gradients are zero
    inner = flow[1:-1, 1:-1]
    return float(np.mean(inner))


def detect_temporal_artifacts(
    frame_dir: Path,
    threshold: float = 0.30,
    pattern: str = "*",
) -> List[Tuple[int, int, float, float]]:
    """
    Scan a directory of frames for temporal discontinuities.

    Parameters
    ----------
    frame_dir : Path
        Directory containing sequentially-named image frames.
    threshold : float
        Maximum allowed relative change in mean flow between consecutive
        frame pairs.  Default 0.30 (30 %).
    pattern : str
        Glob pattern for frame files.  Default ``"*"`` (all files).

    Returns
    -------
    List of tuples ``(frame_a_idx, frame_b_idx, flow_a, flow_b)`` where
    the relative change exceeds *threshold*.
    """
    # Collect and sort frame paths
    candidates = sorted(
        p for p in frame_dir.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    if len(candidates) < 3:
        logger.warning("Need at least 3 frames; found %d", len(candidates))
        return []

    logger.info("Loaded %d frames from %s", len(candidates), frame_dir)

    # Pre-load all frames
    frames: List["np.ndarray"] = []
    for p in candidates:
        frames.append(_load_frame(p))

    # Compute flow magnitude for each consecutive pair
    flow_magnitudes: List[float] = []
    for i in range(len(frames) - 1):
        flow_map = _compute_flow_magnitude(frames[i], frames[i + 1])
        flow_magnitudes.append(_mean_flow(flow_map))
        logger.debug(
            "Flow[%d→%d] = %.4f", i, i + 1, flow_magnitudes[-1]
        )

    # Detect discontinuities: relative change > threshold
    violations: List[Tuple[int, int, float, float]] = []
    for i in range(len(flow_magnitudes) - 1):
        f_a = flow_magnitudes[i]
        f_b = flow_magnitudes[i + 1]
        # Relative change (symmetric)
        denom = max(abs(f_a), abs(f_b), 1e-6)
        rel_change = abs(f_b - f_a) / denom
        if rel_change > threshold:
            violations.append((i, i + 1, f_a, f_b))
            logger.warning(
                "DISCONTINUITY at pair %d→%d: flow %.4f → %.4f "
                "(rel_change=%.2f%%, threshold=%.2f%%)",
                i, i + 1, f_a, f_b,
                rel_change * 100, threshold * 100,
            )

    return violations


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="temporal_consistency_lint",
        description=(
            "G171 v4 — detect temporal artifacts (frame-to-frame "
            "discontinuity > 30 % in optical flow)."
        ),
    )
    parser.add_argument(
        "--frames",
        type=Path,
        required=True,
        help="Directory containing sequentially-named image frames.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.30,
        help="Max allowed relative flow change (default: 0.30 = 30 %%).",
    )
    parser.add_argument(
        "--pattern",
        type=str,
        default="*",
        help="Glob pattern for frame filenames (default: '*').",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON to stdout.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point — parse args, run lint, return exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    frame_dir: Path = args.frames
    if not frame_dir.is_dir():
        print(f"Error: not a directory: {frame_dir}", file=sys.stderr)
        return 2

    try:
        violations = detect_temporal_artifacts(
            frame_dir=frame_dir,
            threshold=args.threshold,
            pattern=args.pattern,
        )
    except Exception as exc:
        print(f"Error during analysis: {exc}", file=sys.stderr)
        return 2

    # Report
    if args.json:
        import json
        result = {
            "frame_dir": str(frame_dir),
            "threshold": args.threshold,
            "violations": [
                {
                    "frame_a": v[0],
                    "frame_b": v[1],
                    "flow_a": round(v[2], 4),
                    "flow_b": round(v[3], 4),
                }
                for v in violations
            ],
            "passed": len(violations) == 0,
        }
        print(json.dumps(result, indent=2))
    else:
        if violations:
            print(f"\n{'=' * 60}")
            print(f"TEMPORAL CONSISTENCY LINT — {len(violations)} VIOLATION(S)")
            print(f"{'=' * 60}")
            for idx_a, idx_b, fa, fb in violations:
                denom = max(abs(fa), abs(fb), 1e-6)
                rel = abs(fb - fa) / denom * 100
                print(
                    f"  Frame pair {idx_a}→{idx_b}: "
                    f"flow {fa:.4f} → {fb:.4f}  "
                    f"(Δ = {rel:.1f}%)"
                )
            print(f"{'=' * 60}\n")
        else:
            print("Temporal consistency lint PASSED — no artifacts detected.")

    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
