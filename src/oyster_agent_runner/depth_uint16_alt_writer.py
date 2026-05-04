#!/usr/bin/env python3
"""
G151 · depth_uint16_alt_writer.py

Cluster C: Alternative uint16 PNG mm depth output alongside float32 EXR.
Follows ScanNet++ / Habitat convention for 4x smaller depth files.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
from PIL import Image

__all__ = ["convert_depth_to_uint16_png", "main"]

logger = logging.getLogger(__name__)
DEPTH_SCALE_M_TO_MM = 1000.0
UINT16_MAX = 65535


def convert_depth_to_uint16_png(
    depth_float: np.ndarray,
    output_path: Path,
    scale: float = DEPTH_SCALE_M_TO_MM,
    clip_max: float = UINT16_MAX,
) -> Path:
    """Convert float32 depth (meters) to uint16 PNG (millimeters)."""
    if depth_float.ndim not in (2, 3):
        raise ValueError(f"Depth must be 2D or 3D, got shape {depth_float.shape}")
    if depth_float.ndim == 3:
        depth_float = depth_float.squeeze(axis=2)

    depth_mm = np.clip(depth_float * scale, 0, clip_max).astype(np.uint16)
    Image.fromarray(depth_mm, mode="I;16").save(output_path)
    logger.info(f"Wrote uint16 depth PNG: {output_path}")
    return output_path


def read_exr_depth(exr_path: Path) -> np.ndarray:
    """Read float32 depth from EXR file using imageio (lazy import)."""
    if not exr_path.exists():
        raise FileNotFoundError(f"EXR file not found: {exr_path}")
    try:
        import imageio.v3 as iio
    except ImportError as e:
        raise ImportError("imageio required for EXR. Install: pip install imageio") from e
    return iio.imread(str(exr_path)).astype(np.float32)


def read_npy_depth(npy_path: Path) -> np.ndarray:
    """Read float32 depth from numpy .npy file."""
    if not npy_path.exists():
        raise FileNotFoundError(f"NPY file not found: {npy_path}")
    return np.load(str(npy_path)).astype(np.float32)


def process_depth_file(
    input_path: Path,
    output_path: Path | None = None,
    scale: float = DEPTH_SCALE_M_TO_MM,
) -> Path:
    """Process a depth file (.exr or .npy) and write uint16 PNG output."""
    input_path = Path(input_path)
    output_path = Path(output_path) if output_path else input_path.with_suffix(".png")

    suffix = input_path.suffix.lower()
    if suffix == ".exr":
        depth = read_exr_depth(input_path)
    elif suffix == ".npy":
        depth = read_npy_depth(input_path)
    else:
        raise ValueError(f"Unsupported depth format: {suffix}. Use .exr or .npy")

    return convert_depth_to_uint16_png(depth, output_path, scale=scale)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for depth_uint16_alt_writer."""
    parser = argparse.ArgumentParser(
        description="Convert float32 depth to uint16 PNG (mm) format.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n  %(prog)s depth.exr\n  %(prog)s depth.exr -o depth_uint16.png",
    )
    parser.add_argument("input", type=Path, help="Input depth file (.exr or .npy)")
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="Output PNG path (default: input stem + .png)")
    parser.add_argument("--scale", type=float, default=DEPTH_SCALE_M_TO_MM,
                        help=f"Scale factor (default: {DEPTH_SCALE_M_TO_MM})")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s - %(levelname)s - %(message)s")

    try:
        output = process_depth_file(args.input, args.output, args.scale)
        print(f"Output: {output}")
        return 0
    except FileNotFoundError as e:
        logger.error(f"File not found: {e}")
        return 1
    except ImportError as e:
        logger.error(f"Missing dependency: {e}")
        return 2
    except ValueError as e:
        logger.error(f"Invalid input: {e}")
        return 3
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return 4


if __name__ == "__main__":
    sys.exit(main())
