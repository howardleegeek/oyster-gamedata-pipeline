#!/usr/bin/env python3
"""Run Depth Anything V2 depth estimation via ONNX Runtime.

Drop-in replacement for bin/run_da_v2_depth.py using ONNX Runtime + DirectML.
Works on any Windows 10+ GPU (AMD, Intel, NVIDIA, integrated) without CUDA.

Usage:
    python3 bin/run_da_v2_depth_onnx.py --input path/to/image.png --output depth/
    python3 bin/run_da_v2_depth_onnx.py --input-dir frames/ --output depth/
"""
import argparse
import json
import os
import pathlib
import sys
import time
import warnings
from datetime import datetime, timezone

import numpy as np
from PIL import Image

warnings.filterwarnings("ignore")


def get_providers():
    """Select best available ONNX Runtime execution providers.

    Priority: DirectML (Windows GPU) > CUDA > CoreML (Mac) > CPU
    """
    import onnxruntime as ort

    available = ort.get_available_providers()
    providers = []
    if "DmlExecutionProvider" in available:
        providers.append("DmlExecutionProvider")
    if "CUDAExecutionProvider" in available:
        providers.append("CUDAExecutionProvider")
    if "CoreMLExecutionProvider" in available:
        providers.append("CoreMLExecutionProvider")
    providers.append("CPUExecutionProvider")
    return providers


def load_processor(model_id: str):
    """Load the image processor from HuggingFace transformers."""
    from transformers import AutoImageProcessor

    return AutoImageProcessor.from_pretrained(model_id)


def run_inference(sess, processor, image: Image.Image) -> np.ndarray:
    """Run depth inference on a single image.

    Returns depth map as numpy array (H, W).
    """
    inputs = processor(images=image, return_tensors="np")
    pv = inputs["pixel_values"]
    outputs = sess.run(None, {"pixel_values": pv})
    depth = outputs[0].squeeze()
    return depth


def normalize_depth(depth: np.ndarray) -> Image.Image:
    """Normalize depth map to 0-255 grayscale image."""
    d_min = depth.min()
    d_max = depth.max()
    if d_max - d_min < 1e-9:
        normalized = np.zeros_like(depth, dtype=np.uint8)
    else:
        normalized = ((depth - d_min) / (d_max - d_min) * 255).astype(np.uint8)
    return Image.fromarray(normalized, mode="L")


def write_source_marker(output_dir: pathlib.Path):
    """Write .source marker to disambiguate backend used."""
    source_path = output_dir / ".source"
    source_data = {
        "kind": "monocular_da_v2_onnx",
        "backend": "onnxruntime",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with open(source_path, "w") as f:
        json.dump(source_data, f, indent=2)


def main():
    parser = argparse.ArgumentParser(
        description="Run Depth Anything V2 depth estimation via ONNX Runtime"
    )
    parser.add_argument(
        "--input",
        type=str,
        default=None,
        help="Path to a single input image",
    )
    parser.add_argument(
        "--input-dir",
        type=str,
        default=None,
        help="Directory of input images to process",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="depth",
        help="Output directory for depth maps (default: depth)",
    )
    parser.add_argument(
        "--onnx-path",
        type=str,
        default=None,
        help="Path to ONNX model file (auto-downloads if not specified)",
    )
    parser.add_argument(
        "--model-id",
        type=str,
        default="depth-anything/Depth-Anything-V2-Small-hf",
        help="HuggingFace model ID for the image processor",
    )
    parser.add_argument(
        "--format",
        choices=["png", "npy", "both"],
        default="both",
        help="Output format: png, npy, or both (default: both)",
    )
    args = parser.parse_args()

    if not args.input and not args.input_dir:
        parser.error("Either --input or --input-dir is required")

    output_dir = pathlib.Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Resolve ONNX model path
    if args.onnx_path:
        onnx_path = pathlib.Path(args.onnx_path)
    else:
        # Try cached location first
        cache_dir = pathlib.Path.home() / ".cache" / "oyster" / "da-v2-small-onnx"
        onnx_path = cache_dir / "depth_anything_v2_small.onnx"
        if not onnx_path.exists():
            print("ONNX model not found locally. Downloading...")
            # Import and run download
            sys.path.insert(0, str(pathlib.Path(__file__).parent))
            from download_da_v2_onnx import download_model

            onnx_path = download_model()

    if not onnx_path.exists():
        print(f"ERROR: ONNX model not found at {onnx_path}")
        sys.exit(1)

    # Load ONNX session with best available providers
    print(f"Loading ONNX model: {onnx_path}")
    import onnxruntime as ort

    providers = get_providers()
    print(f"  Execution providers: {providers}")
    sess = ort.InferenceSession(str(onnx_path), providers=providers)

    # Load image processor
    print(f"Loading image processor: {args.model_id}")
    processor = load_processor(args.model_id)

    # Collect input images
    if args.input:
        input_files = [pathlib.Path(args.input)]
    else:
        input_dir = pathlib.Path(args.input_dir)
        extensions = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp"}
        input_files = sorted(
            f for f in input_dir.iterdir() if f.suffix.lower() in extensions
        )

    print(f"Processing {len(input_files)} image(s)...")
    t0 = time.time()

    for i, img_path in enumerate(input_files):
        img = Image.open(img_path).convert("RGB")
        depth = run_inference(sess, processor, img)

        base_name = img_path.stem
        if args.format in ("png", "both"):
            depth_img = normalize_depth(depth)
            out_png = output_dir / f"{base_name}_depth.png"
            depth_img.save(out_png)
        if args.format in ("npy", "both"):
            out_npy = output_dir / f"{base_name}_depth.npy"
            np.save(out_npy, depth)

        if (i + 1) % 10 == 0 or i == len(input_files) - 1:
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            print(f"  {i + 1}/{len(input_files)} ({rate:.1f} img/s)")

    total_time = time.time() - t0
    print(f"\nDone: {len(input_files)} images in {total_time:.1f}s")

    # Write source marker
    write_source_marker(output_dir)
    print(f"Source marker written to {output_dir / '.source'}")


if __name__ == "__main__":
    main()
