#!/usr/bin/env python3
"""
DepthAnything V2 inference wrapper.

Provides functions to check model availability and run depth inference
on RGB images, saving results as OpenEXR files.
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)

# Lazy imports for optional dependencies
_torch = None
_transformers = None
_openexr = None


def _import_torch():
    global _torch
    if _torch is None:
        import torch

        _torch = torch
    return _torch


def _import_transformers():
    global _transformers
    if _transformers is None:
        from transformers import AutoImageProcessor, AutoModel

        _transformers = (AutoModel, AutoImageProcessor)
    return _transformers


def _import_openexr():
    global _openexr
    if _openexr is None:
        import Imath
        import OpenEXR

        _openexr = (OpenEXR, Imath)
    return _openexr


def is_available() -> bool:
    """Check if DepthAnything V2 inference is available."""
    try:
        _import_torch()
        _import_transformers()
        _import_openexr()
        return True
    except ImportError:
        return False


def infer_depth(
    rgb_frame_path: str,
    output_exr_path: str,
    near_plane: float = 0.5,
    far_plane: float = 30.0,
) -> bool:
    """
    Run DepthAnything-V2-Small inference on an RGB image and save as OpenEXR.

    Args:
        rgb_frame_path: Path to input RGB image (PNG or JPG).
        output_exr_path: Path for output OpenEXR file.
        near_plane: Near clipping plane for depth normalization.
        far_plane: Far clipping plane for depth normalization.

    Returns:
        True if inference succeeded, False otherwise.
    """
    if not is_available():
        return False

    try:
        torch = _import_torch()
        AutoModel, AutoImageProcessor = _import_transformers()
        OpenEXR, Imath = _import_openexr()
        from PIL import Image

        # Load and preprocess image
        image = Image.open(rgb_frame_path).convert("RGB")

        # Load model (cached after first load)
        if not hasattr(infer_depth, "_model"):
            infer_depth._model = AutoModel.from_pretrained(
                "Depth-Anything/Depth-Anything-V2-Small",
                torch_dtype=torch.float32,
            )
            infer_depth._model.eval()
            infer_depth._processor = AutoImageProcessor.from_pretrained(
                "Depth-Anything/Depth-Anything-V2-Small"
            )

        model = infer_depth._model
        processor = infer_depth._processor

        # Run inference
        inputs = processor(images=image, return_tensors="pt")
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        inputs = {k: v.to(device) for k, v in inputs.items()}

        with torch.no_grad():
            depth = model(**inputs).predicted_depth.squeeze().cpu().numpy()

        # Normalize depth to near/far range
        depth = (depth - depth.min()) / (depth.max() - depth.min() + 1e-6)
        depth = (depth * (far_plane - near_plane) + near_plane).astype(np.float32)

        # Save as OpenEXR with channel "Z"
        h, w = depth.shape
        header = OpenEXR.Header(w, h)
        header["channels"] = {"Z": Imath.Channel(Imath.PixelType.FLOAT)}
        out = OpenEXR.OutputFile(output_exr_path, header)
        out.writePixels({"Z": depth.tobytes()})
        out.close()
        return True

    except Exception as exc:
        logger.exception(
            "infer_depth failed for rgb=%s out=%s",
            rgb_frame_path,
            output_exr_path,
        )
        return False
