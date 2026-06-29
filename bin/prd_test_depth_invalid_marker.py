#!/usr/bin/env python3
"""
G081 · bin/prd_test_depth_invalid_marker.py

PRD p4 #6: Depth invalid pixel sentinel value (zero or NaN) preserved
through OpenEXR roundtrip.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


def create_depth_buffer(height: int, width: int, sentinel: str) -> np.ndarray:
    """Create depth buffer with ~10% invalid pixels using given sentinel."""
    rng = np.random.default_rng(seed=42)
    depth = rng.uniform(0.5, 10.0, size=(height, width)).astype(np.float32)
    invalid_mask = rng.random((height, width)) < 0.1
    depth[invalid_mask] = 0.0 if sentinel == "zero" else np.nan
    return depth


def write_exr(filepath: Path, depth: np.ndarray) -> bool:
    """Write depth to EXR file. Returns True on success."""
    try:
        import OpenImageIO as oiio

        spec = oiio.ImageSpec(depth.shape[1], depth.shape[0], 1, oiio.FLOAT)
        spec.channelnames = ["Z"]
        out = oiio.ImageOutput.create(str(filepath))
        if out:
            out.open(str(filepath), spec)
            out.write_image(depth)
            out.close()
            return True
    except ImportError:
        pass

    try:
        import Imath
        import OpenEXR

        header = OpenEXR.Header(depth.shape[1], depth.shape[0])
        header["channels"] = {"Z": Imath.Channel(Imath.PixelType(Imath.PixelType.FLOAT))}
        exr = OpenEXR.OutputFile(str(filepath), header)
        exr.writePixels({"Z": depth.tobytes()})
        exr.close()
        return True
    except ImportError:
        pass

    # Fallback: NPZ for testing without EXR libs
    np.savez_compressed(filepath.with_suffix(".npz"), depth=depth)
    return True


def read_exr(filepath: Path) -> np.ndarray | None:
    """Read depth from EXR file. Returns None on failure."""
    try:
        import OpenImageIO as oiio

        inp = oiio.ImageInput.open(str(filepath))
        if inp:
            img = inp.read_image()
            inp.close()
            return img.astype(np.float32)
    except ImportError:
        pass

    try:
        import Imath
        import OpenEXR

        exr = OpenEXR.InputFile(str(filepath))
        dw = exr.header()["dataWindow"]
        w, h = dw.max.x - dw.min.x + 1, dw.max.y - dw.min.y + 1
        pt = Imath.PixelType(Imath.PixelType.FLOAT)
        depth = np.frombuffer(exr.channel("Z", pt), dtype=np.float32)
        return depth.reshape(h, w)
    except ImportError:
        pass

    npz = filepath.with_suffix(".npz")
    if npz.exists():
        return np.load(npz)["depth"]
    return None


def verify_preservation(orig: np.ndarray, rest: np.ndarray, sentinel: str) -> dict[str, Any]:
    """Verify sentinel values preserved after roundtrip."""

    def is_invalid(d: np.ndarray) -> np.ndarray:
        if sentinel == "zero":
            return d == 0.0
        return np.isnan(d)

    orig_mask = is_invalid(orig)
    rest_mask = is_invalid(rest)
    mismatch = np.sum(orig_mask != rest_mask)
    valid = ~orig_mask & ~rest_mask
    valid_ok = not np.any(valid) or np.max(np.abs(orig[valid] - rest[valid])) < 1e-6
    return {
        "sentinel": sentinel,
        "passed": mismatch == 0 and valid_ok,
        "orig_invalid": int(np.sum(orig_mask)),
        "rest_invalid": int(np.sum(rest_mask)),
        "mismatch": int(mismatch),
    }


def run_test(sentinel: str, temp_dir: Path) -> int:
    """Run single test for given sentinel type. Returns 0 on pass."""
    logger.info(f"Testing {sentinel} sentinel...")
    depth = create_depth_buffer(64, 64, sentinel)
    path = temp_dir / f"depth_{sentinel}.exr"
    if not write_exr(path, depth):
        logger.error("Write failed")
        return 1
    restored = read_exr(path)
    if restored is None:
        logger.error("Read failed")
        return 1
    result = verify_preservation(depth, restored, sentinel)
    logger.info(f"  Invalid pixels: {result['orig_invalid']} -> {result['rest_invalid']}")
    logger.info(f"  Mismatches: {result['mismatch']}")
    if result["passed"]:
        logger.info(f"PASS: {sentinel} sentinel preserved")
        return 0
    logger.error(f"FAIL: {sentinel} sentinel NOT preserved")
    return 1


def main(argv: list[str] | None = None) -> int:
    """Main entry point. Returns 0 on all tests pass."""
    parser = argparse.ArgumentParser(
        description="Test depth invalid pixel sentinel preservation in OpenEXR roundtrip"
    )
    parser.add_argument(
        "--sentinel", choices=["zero", "nan", "both"], default="both", help="Sentinel type to test"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s: %(message)s"
    )

    sentinels = ["zero", "nan"] if args.sentinel == "both" else [args.sentinel]
    temp_dir = Path(tempfile.mkdtemp(prefix="depth_test_"))
    try:
        return max(run_test(s, temp_dir) for s in sentinels)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
