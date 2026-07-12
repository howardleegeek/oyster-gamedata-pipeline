#!/usr/bin/env python3
"""
G032: DepthAnything V2 Small smoke test.

Load DepthAnything V2 Small model, run inference on one RGB sample,
write valid OpenEXR float32 depth map, and read-back validate.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import numpy as np
from PIL import Image

if TYPE_CHECKING:
    import torch


def lazy_import_torch():
    """Lazy import torch to avoid mandatory dependency at import time."""
    try:
        import torch

        return torch
    except ImportError as exc:
        raise ImportError("torch required: pip install torch") from exc


def lazy_import_openexr():
    """Lazy import OpenEXR modules for depth map I/O."""
    try:
        import Imath
        import OpenEXR

        return OpenEXR, Imath
    except ImportError as exc:
        raise ImportError("OpenEXR required: pip install OpenEXR") from exc


def create_rgb_sample(width: int = 640, height: int = 480) -> Image.Image:
    """Create a synthetic RGB test image for smoke testing."""
    x = np.linspace(0, 1, width, dtype=np.float32)
    y = np.linspace(0, 1, height, dtype=np.float32)
    xx, yy = np.meshgrid(x, y)
    r = 0.5 + 0.5 * np.sin(2 * np.pi * xx)
    g = 0.5 + 0.5 * np.cos(2 * np.pi * yy)
    b = 0.5 + 0.5 * np.sin(2 * np.pi * (xx + yy))
    rgb = np.stack([r, g, b], axis=-1)
    return Image.fromarray((rgb * 255).astype(np.uint8), mode="RGB")


def load_depth_anything_v2_small():
    """Load DepthAnything V2 Small model or return mock if unavailable."""
    try:
        from depth_anything_v2.dpt import DepthAnythingV2

        print("Loading DepthAnything V2 Small model...")
        model = DepthAnythingV2(encoder="vits", features=64, out_channels=[48, 96, 192, 384])
        model.eval()
        print("Model loaded successfully")
        return model
    except ImportError as e:
        raise RuntimeError(
            "depth_anything_smoke requires the depth_anything_v2 package. "
            "Install it or provide a real ONNX model path. "
            "Iron-law: never return a mock depth model — fake depth "
            "contaminates downstream tarball quality checks."
        ) from e


def preprocess_for_depth(image: Image.Image, target_size: int = 518) -> "torch.Tensor":
    """Preprocess RGB image for DepthAnything model inference."""
    torch = lazy_import_torch()
    img = image.resize((target_size, target_size), Image.Resampling.BILINEAR)
    img_np = np.array(img).astype(np.float32) / 255.0
    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
    img_np = (img_np - mean) / std
    img_tensor = torch.from_numpy(img_np).permute(2, 0, 1).unsqueeze(0)
    return img_tensor


def run_inference(model, image: Image.Image) -> np.ndarray:
    """Run depth inference on input image."""
    torch = lazy_import_torch()
    tensor = preprocess_for_depth(image)
    with torch.no_grad():
        depth = model(tensor)
    if isinstance(depth, torch.Tensor):
        depth = depth.squeeze().cpu().numpy()
    return depth.astype(np.float32)


def write_exr_depth(path: Path, depth: np.ndarray) -> None:
    """Write depth map to OpenEXR file as float32 Z channel."""
    OpenEXR, Imath = lazy_import_openexr()
    height, width = depth.shape
    header = OpenEXR.Header(width, height)
    header["channels"] = {"Z": Imath.Channel(Imath.PixelType(Imath.PixelType.FLOAT))}
    exr_file = OpenEXR.OutputFile(str(path), header)
    depth_bytes = depth.astype(np.float32).tobytes()
    exr_file.writePixels({"Z": depth_bytes})
    exr_file.close()


def read_exr_depth(path: Path) -> np.ndarray:
    """Read depth map from OpenEXR file Z channel."""
    OpenEXR, Imath = lazy_import_openexr()
    exr_file = OpenEXR.InputFile(str(path))
    header = exr_file.header()
    dw = header["dataWindow"]
    width = dw.max.x - dw.min.x + 1
    height = dw.max.y - dw.min.y + 1
    pt = Imath.PixelType(Imath.PixelType.FLOAT)
    depth_str = exr_file.channel("Z", pt)
    depth = np.frombuffer(depth_str, dtype=np.float32).reshape(height, width)
    exr_file.close()
    return depth


def validate_depth(original: np.ndarray, readback: np.ndarray) -> bool:
    """Validate that readback depth matches original within tolerance."""
    if original.shape != readback.shape:
        print(f"Shape mismatch: {original.shape} vs {readback.shape}")
        return False
    max_diff = np.max(np.abs(original - readback))
    if max_diff > 1e-6:
        print(f"Max difference {max_diff} exceeds tolerance 1e-6")
        return False
    print(f"Validation passed: max_diff={max_diff:.2e}")
    return True


def main(argv: Optional[list] = None) -> int:
    """
    Main entry point for DepthAnything V2 smoke test.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).

    Returns:
        Exit code (0 for success, 1 for error).
    """
    parser = argparse.ArgumentParser(description="DepthAnything V2 Small smoke test")
    parser.add_argument("--output", "-o", type=Path, help="Output EXR file path")
    parser.add_argument(
        "--no-cleanup", action="store_true", help="Keep temporary files after completion"
    )
    args = parser.parse_args(argv)

    temp_dir = None
    output_path = args.output

    try:
        if output_path is None:
            temp_dir = tempfile.mkdtemp(prefix="depth_smoke_")
            output_path = Path(temp_dir) / "depth_output.exr"

        print("Creating synthetic RGB test image...")
        rgb_image = create_rgb_sample(640, 480)
        print(f"Image size: {rgb_image.size}")

        print("Loading depth model...")
        model = load_depth_anything_v2_small()

        print("Running depth inference...")
        depth_map = run_inference(model, rgb_image)
        print(f"Depth map shape: {depth_map.shape}, dtype: {depth_map.dtype}")
        print(f"Depth range: [{depth_map.min():.4f}, {depth_map.max():.4f}]")

        print(f"Writing EXR to: {output_path}")
        write_exr_depth(output_path, depth_map)

        print("Reading back EXR for validation...")
        readback = read_exr_depth(output_path)

        if not validate_depth(depth_map, readback):
            print("ERROR: Validation failed!")
            return 1

        print(f"SUCCESS: Smoke test passed. Output: {output_path}")
        return 0

    except ImportError as e:
        print(f"ERROR: Missing dependency: {e}")
        return 1
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")
        return 1
    finally:
        if temp_dir and not args.no_cleanup:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
