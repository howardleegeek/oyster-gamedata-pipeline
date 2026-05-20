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
from typing import Any, Optional

import numpy as np
from PIL import Image


def lazy_import_torch() -> Any:
    """Lazy import torch to avoid mandatory dependency at import time."""
    try:
        import torch

        return torch
    except ImportError as exc:
        raise ImportError("torch required: pip install torch") from exc


def lazy_import_openexr() -> tuple[Any, Any]:
    """Lazy import OpenEXR modules for depth map I/O."""
    try:
        import OpenEXR
        import Imath

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


def load_depth_anything_v2_small() -> Any:
    """Load DepthAnything V2 Small model or return mock if unavailable."""
    torch = lazy_import_torch()
    try:
        from depth_anything_v2.dpt import DepthAnythingV2

        print("Loading DepthAnything V2 Small model...")
        model = DepthAnythingV2(encoder="vits", features=64, out_channels=[48, 96, 192, 384])
        model.eval()
        print("Model loaded successfully")
        return model
    except ImportError:
        raise RuntimeError(
            "depth_anything_smoke requires the depth_anything_v2 package. "
            "Install it or provide a real ONNX model path. "
            "Iron-law: never return a mock depth model — fake depth "
            "contaminates downstream tarball quality checks."
        )


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


def run_inference(model: "torch.nn.Module", image: Image.Image) -> np.ndarray:
    """Run depth inference on input image."""
    torch = lazy_import_torch()
    tensor = pre