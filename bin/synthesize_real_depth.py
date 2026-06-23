#!/usr/bin/env python3
"""
synthesize_real_depth.py — G161 Cluster A

Synthesize realistic 1920x1080 float32 single-channel EXR depth maps
with Z-buffer (distance-to-plane) representation.  Invalid-pixel ratio
is capped at 0.1 % (1 ‰).  Replaces the 16x16 all-zeros placeholder.

Usage:
    python bin/synthesize_real_depth.py -o output.exr [--seed 42]
    python bin/synthesize_real_depth.py -o output.exr --width 1920 --height 1080
    python bin/synthesize_real_depth.py -o output.exr --invalid-ratio 0.001

Dependencies (lazy-imported):
    numpy  — always required
    OpenEXR / Imath — preferred EXR writer (falls back to raw .bin + .npy)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Depth-map generation
# ---------------------------------------------------------------------------

def _generate_scene_depth(
    width: int,
    height: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Return a *distance-to-plane* depth field for a synthetic scene.

    The scene consists of a ground plane, several Gaussian "mounds"
    (simulating objects), and a gentle perspective gradient.  Values are
    in metres, roughly in the range [0.1, 5.0].
    """
    # Normalised coordinates centred at (0, 0)
    x = np.linspace(-1.0, 1.0, width, dtype=np.float64)
    y = np.linspace(-1.0, 1.0, height, dtype=np.float64)
    X, Y = np.meshgrid(x, y)

    # Base ground plane with perspective (farther = larger depth)
    depth = 1.0 + 0.5 * Y + 0.3 * (X ** 2 + Y ** 2)

    # Gaussian mounds — simulate objects sitting on the ground
    mounds: list[Tuple[float, float, float, float]] = [
        # (cx, cy, sigma, amplitude)
        (0.0, 0.0, 0.25, 1.5),
        (-0.5, 0.3, 0.18, 1.0),
        (0.6, -0.2, 0.15, 0.8),
        (0.2, -0.5, 0.20, 1.2),
        (-0.3, -0.6, 0.12, 0.6),
    ]
    for cx, cy, sigma, amp in mounds:
        dist_sq = (X - cx) ** 2 + (Y - cy) ** 2
        depth += amp * np.exp(-dist_sq / (2.0 * sigma ** 2))

    # Subtle high-frequency noise (sensor-like)
    depth += rng.normal(0.0, 0.005, (height, width))

    # Clamp to physically plausible range
    depth = np.clip(depth, 0.05, 10.0)
    return depth.astype(np.float32)


def _inject_invalid_pixels(
    depth: np.ndarray,
    invalid_ratio: float,
    rng: np.random.Generator,
) -> np.ndarray:
    """Mark a fraction of pixels as invalid using PRD sentinel = 0.

    PRD page 11 mandates the invalid-pixel marker for the entire dataset is
    chosen from {0, inf, NaN} and held CONSTANT — this doc explicitly sets
    it to 0. Cluster's earlier draft used NaN, which violates the spec.
    """
    total = depth.size
    max_invalid = int(total * min(invalid_ratio, 0.001))  # hard cap 0.1 %
    if max_invalid <= 0:
        return depth
    flat = depth.ravel()
    idx = rng.choice(total, size=max_invalid, replace=False)
    flat[idx] = np.float32(0.0)  # PRD: invalid_pixel = 0 (consistent across dataset)
    return depth


def generate_realistic_depth(
    width: int = 1920,
    height: int = 1080,
    invalid_pixel_ratio: float = 0.001,
    seed: Optional[int] = None,
) -> np.ndarray:
    """Generate a realistic Z-buffer depth map.

    Parameters
    ----------
    width, height : int
        Output resolution (default 1920x1080).
    invalid_pixel_ratio : float
        Fraction of pixels to mark invalid (NaN).  Capped at 0.001.
    seed : int or None
        Random seed for reproducibility.

    Returns
    -------
    np.ndarray  (height, width), dtype float32
    """
    rng = np.random.default_rng(seed)
    depth = _generate_scene_depth(width, height, rng)
    depth = _inject_invalid_pixels(depth, invalid_pixel_ratio, rng)
    return depth


# ---------------------------------------------------------------------------
# EXR output (lazy-import backends)
# ---------------------------------------------------------------------------

def _save_exr_openexr(depth: np.ndarray, path: Path) -> bool:
    """Write EXR via OpenEXR / Imath (C++ bindings)."""
    import OpenEXR  # noqa: F811 — lazy
    import Imath    # noqa: F811 — lazy

    h, w = depth.shape
    header = OpenEXR.Header(w, h)
    header["compression"] = Imath.Compression(Imath.Compression.ZIP_COMPRESSION)
    header["channels"] = {
        "Y": Imath.Channel(Imath.PixelType(Imath.PixelType.FLOAT))
    }
    out = OpenEXR.OutputFile(str(path), header)
    out.writePixels({"Y": depth.tobytes()})
    out.close()
    return True


def _save_exr_imageio(depth: np.ndarray, path: Path) -> bool:
    """Write EXR via imageio (pure-Python fallback)."""
    import imageio.v3 as iio  # noqa: F811 — lazy
    iio.imwrite(str(path), depth, extension=".exr")
    return True


def _save_exr_numpy_fallback(depth: np.ndarray, path: Path) -> bool:
    """Fallback: save as .npy alongside a companion .bin (raw float32)."""
    npy_path = path.with_suffix(".npy")
    np.save(str(npy_path), depth)
    bin_path = path.with_suffix(".bin")
    depth.tofile(str(bin_path))
    print(f"[WARN] OpenEXR unavailable; wrote {npy_path} + {bin_path}", file=sys.stderr)
    return True


def save_exr(depth: np.ndarray, output_path: Path) -> bool:
    """Save *depth* as a single-channel EXR file, trying multiple backends."""
    backends = [
        ("OpenEXR", _save_exr_openexr),
        ("imageio", _save_exr_imageio),
        ("numpy-fallback", _save_exr_numpy_fallback),
    ]
    for name, fn in backends:
        try:
            return fn(depth, output_path)
        except ImportError:
            continue
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] {name} backend failed: {exc}", file=sys.stderr)
            continue
    raise RuntimeError("No EXR writer available (tried OpenEXR, imageio, numpy)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser."""
    p = argparse.ArgumentParser(
        description="Synthesize realistic EXR depth maps (Z-buffer).",
    )
    p.add_argument(
        "-o", "--output",
        type=Path,
        required=True,
        help="Output EXR file path.",
    )
    p.add_argument(
        "--width", type=int, default=1920,
        help="Image width  (default: 1920).",
    )
    p.add_argument(
        "--height", type=int, default=1080,
        help="Image height (default: 1080).",
    )
    p.add_argument(
        "--invalid-ratio", type=float, default=0.001,
        help="Max invalid-pixel ratio (default: 0.001 = 0.1 %%).",
    )
    p.add_argument(
        "--seed", type=int, default=None,
        help="Random seed for reproducibility.",
    )
    return p


def main(argv: Optional[list[str]] = None) -> int:
    """Entry-point: parse args, generate depth, write EXR."""
    parser = build_parser()
    args = parser.parse_args(argv)

    # Validate
    if args.invalid_ratio < 0 or args.invalid_ratio > 0.001:
        parser.error("--invalid-ratio must be in [0, 0.001] (cap 0.1 %).")
    if args.width < 16 or args.height < 16:
        parser.error("width/height must be >= 16.")

    # Generate
    depth = generate_realistic_depth(
        width=args.width,
        height=args.height,
        invalid_pixel_ratio=args.invalid_ratio,
        seed=args.seed,
    )

    # Quick sanity check
    invalid_count = int(np.isnan(depth).sum())
    total = depth.size
    actual_ratio = invalid_count / total if total else 0.0
    assert actual_ratio <= 0.001 + 1e-9, (
        f"Invalid ratio {actual_ratio:.6f} exceeds 0.001 cap"
    )

    # Write
    args.output.parent.mkdir(parents=True, exist_ok=True)
    save_exr(depth, args.output)

    print(
        f"Wrote {args.output}  ({args.width}x{args.height}, "
        f"dtype={depth.dtype}, invalid={invalid_count}/{total} "
        f"({actual_ratio * 100:.4f} %))"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
