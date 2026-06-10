#!/usr/bin/env python3
"""Run Depth Anything V2 depth estimation via ONNX Runtime.

Drop-in replacement for bin/run_da_v2_depth.py using ONNX Runtime + DirectML.
Works on any Windows 10+ GPU (AMD, Intel, NVIDIA, integrated) without CUDA.

Usage:
    python3 bin/run_da_v2_depth_onnx.py --input path/to/image.png --output depth/
    python3 bin/run_da_v2_depth_onnx.py --input-dir frames/ --output depth/
"""

import argparse
import pathlib
import struct
import sys
import time
import warnings
from datetime import datetime, timezone
from typing import Any

import numpy as np
from PIL import Image

warnings.filterwarnings("ignore")


def has_external_weights(model_path: pathlib.Path | str | None) -> bool:
    """Return true when an ONNX model has sibling external initializer weights."""

    if model_path is None:
        return False
    onnx_path = pathlib.Path(model_path)
    return onnx_path.with_suffix(onnx_path.suffix + ".data").exists()


def get_providers(model_path: pathlib.Path | str | None = None) -> list[str]:
    """Select best available ONNX Runtime execution providers.

    Priority: DirectML (Windows GPU) > CUDA > CoreML (Mac) > CPU
    """
    import onnxruntime as ort

    available = ort.get_available_providers()
    skip_coreml = has_external_weights(model_path)
    providers: list[str] = []
    if "DmlExecutionProvider" in available:
        providers.append("DmlExecutionProvider")
    if "CUDAExecutionProvider" in available:
        providers.append("CUDAExecutionProvider")
    if "CoreMLExecutionProvider" in available and not skip_coreml:
        providers.append("CoreMLExecutionProvider")
    providers.append("CPUExecutionProvider")
    return providers


def create_inference_session(onnx_path: pathlib.Path, providers: list[str]) -> Any:
    """Create an ONNX Runtime session, falling back to CPU if an EP fails."""

    import onnxruntime as ort

    try:
        return ort.InferenceSession(str(onnx_path), providers=providers)
    except Exception as exc:
        if providers == ["CPUExecutionProvider"]:
            raise
        print(
            "WARNING: ONNX Runtime failed to initialize with providers "
            f"{providers}: {exc}. Retrying with ['CPUExecutionProvider'].",
            file=sys.stderr,
        )
        return ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])


def test_get_providers_skips_coreml_for_external_weights(
    tmp_path: pathlib.Path, monkeypatch: Any
) -> None:
    """CoreML is skipped for split ONNX weights while other accelerators remain."""

    model_path = tmp_path / "model.onnx"
    model_path.touch()
    model_path.with_suffix(model_path.suffix + ".data").touch()
    monkeypatch.setattr(
        "onnxruntime.get_available_providers",
        lambda: [
            "DmlExecutionProvider",
            "CUDAExecutionProvider",
            "CoreMLExecutionProvider",
            "CPUExecutionProvider",
        ],
    )

    providers = get_providers(model_path)

    assert providers == [
        "DmlExecutionProvider",
        "CUDAExecutionProvider",
        "CPUExecutionProvider",
    ]


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


def resize_depth_for_exr(depth: np.ndarray) -> np.ndarray:
    """Resize DA-V2 output to the canonical 1920x1080 EXR shape."""

    depth_img = Image.fromarray(depth.astype(np.float32), mode="F")
    depth_1920 = depth_img.resize((1920, 1080), Image.BILINEAR)
    return np.asarray(depth_1920, dtype=np.float32)


def write_exr_z(path: pathlib.Path, depth: np.ndarray) -> None:
    """Write a single-channel float32 OpenEXR file without extra dependencies."""

    depth = np.asarray(depth, dtype="<f4")
    if depth.ndim != 2:
        raise ValueError(f"expected 2D depth array, got shape={depth.shape}")

    height, width = depth.shape

    def attr(name: str, type_name: str, value: bytes) -> bytes:
        return (
            name.encode("ascii")
            + b"\x00"
            + type_name.encode("ascii")
            + b"\x00"
            + struct.pack("<i", len(value))
            + value
        )

    channels = b"Z\x00" + struct.pack("<iB3xii", 2, 0, 1, 1) + b"\x00"
    header = b"".join(
        [
            attr("channels", "chlist", channels),
            attr("compression", "compression", b"\x00"),
            attr("dataWindow", "box2i", struct.pack("<iiii", 0, 0, width - 1, height - 1)),
            attr("displayWindow", "box2i", struct.pack("<iiii", 0, 0, width - 1, height - 1)),
            attr("lineOrder", "lineOrder", b"\x00"),
            attr("pixelAspectRatio", "float", struct.pack("<f", 1.0)),
            attr("screenWindowCenter", "v2f", struct.pack("<ff", 0.0, 0.0)),
            attr("screenWindowWidth", "float", struct.pack("<f", 1.0)),
        ]
    )
    header += b"\x00"

    line_size = width * 4
    chunk_size = 8 + line_size
    first_chunk_offset = 4 + 4 + len(header) + height * 8

    with path.open("wb") as f:
        f.write(struct.pack("<I", 20000630))
        f.write(struct.pack("<I", 2))
        f.write(header)
        for y in range(height):
            f.write(struct.pack("<q", first_chunk_offset + y * chunk_size))
        for y in range(height):
            f.write(struct.pack("<iI", y, line_size))
            f.write(depth[y].tobytes(order="C"))


def write_source_marker(output_dir: pathlib.Path) -> None:
    """Write .source marker to disambiguate backend used."""

    source_path = output_dir / ".source"
    source_path.write_text(
        "\n".join(
            [
                "kind: monocular_da_v2",
                "backend: onnxruntime",
                f"timestamp: {datetime.now(timezone.utc).isoformat()}",
                "model: depth-anything/Depth-Anything-V2-Small-hf",
                "units: relative",
                "view_space_z: false",
                "legacy_kind: monocular_da_v2_onnx",
            ]
        )
        + "\n"
    )


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
        "--frames-dir",
        dest="input_dir",
        type=str,
        default=None,
        help="Alias for --input-dir",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="depth",
        help="Output directory for depth maps (default: depth)",
    )
    parser.add_argument(
        "--depth-dir",
        dest="output",
        type=str,
        default=None,
        help="Alias for --output",
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
        choices=["exr", "png", "npy", "both"],
        default="exr",
        help="Output format: exr, png, npy, or both (default: exr)",
    )
    args = parser.parse_args()

    if not args.input and not args.input_dir:
        parser.error("Either --input or --input-dir is required")

    output_dir = pathlib.Path(args.output or "depth")
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
    providers = get_providers(onnx_path)
    print(f"  Execution providers: {providers}")
    sess = create_inference_session(onnx_path, providers)

    # Load image processor
    print(f"Loading image processor: {args.model_id}")
    processor = load_processor(args.model_id)

    # Collect input images
    if args.input:
        input_files = [pathlib.Path(args.input)]
    else:
        input_dir = pathlib.Path(args.input_dir)
        extensions = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp"}
        input_files = sorted(f for f in input_dir.iterdir() if f.suffix.lower() in extensions)

    print(f"Processing {len(input_files)} image(s)...")
    t0 = time.time()

    for i, img_path in enumerate(input_files):
        img = Image.open(img_path).convert("RGB")
        depth = run_inference(sess, processor, img)

        base_name = img_path.stem
        if args.format == "exr":
            out_exr = output_dir / f"{i:06d}.exr"
            write_exr_z(out_exr, resize_depth_for_exr(depth))
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
