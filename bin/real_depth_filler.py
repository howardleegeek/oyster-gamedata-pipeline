#!/usr/bin/env python3
"""
R003 · bin/real_depth_filler.py — DepthAnything V2 真深度推理 → OpenEXR

Process RGB frames with DepthAnything V2 Small and output OpenEXR float32 depth maps.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)


def lazy_load_depth_pipeline(
    model_id: str = "depth-anything/Depth-Anything-V2-Small-hf",
) -> Any:
    """
    Lazy import torch + transformers, returns transformers.pipeline('depth-estimation', ...).
    Raises RuntimeError with install hint if torch/transformers missing.
    """
    try:
        import torch  # noqa: F401
        from transformers import pipeline
    except Exception as e:
        raise RuntimeError(
            "Missing dependencies. Install with: pip install torch transformers"
        ) from e

    device = select_device()
    torch_dtype = _get_torch_dtype(device)

    pipe = pipeline(
        "depth-estimation",
        model=model_id,
        device=device,
        torch_dtype=torch_dtype,
    )
    return pipe


def select_device() -> str:
    """Auto-detect: cuda > mps > cpu. Returns string for torch."""
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
    except Exception as e:  # noqa: BLE001
        logger.debug("torch device probe failed; falling back to cpu: %s", e, exc_info=True)
    return "cpu"


def _get_torch_dtype(device: str) -> Any:
    """Return fp16 for MPS/CUDA, fp32 for CPU."""
    try:
        import torch

        if device in ("cuda", "mps"):
            return torch.float16
        return torch.float32
    except Exception as e:  # noqa: BLE001
        logger.debug("torch dtype probe failed; returning None: %s", e, exc_info=True)
        return None


def normalize_depth_to_metric(
    depth_relative: "np.ndarray",
    near_m: float = 0.5,
    far_m: float = 30.0,
) -> "np.ndarray":
    """
    Convert relative depth (model output, dimensionless [0,1]) to metric meters.
    Sky pixels (relative depth > 0.99 = far) → 0 (invalid marker).
    Returns float32 array same shape as input.
    """
    import numpy as np

    depth_relative = np.asarray(depth_relative, dtype=np.float32)

    # Sky / invalid pixels: relative depth > 0.99 → 0.0
    sky_mask = depth_relative > 0.99

    # Linear mapping: 0 → near_m, 1 → far_m (but sky is handled separately)
    # Actually, for depth estimation models, higher relative value = closer
    # But the spec says relative=0 → near, relative=1 → far (before sky handling)
    # Let's follow the spec: linear interpolation from near to far
    depth_m = near_m + (far_m - near_m) * depth_relative

    # Sky pixels → 0.0
    depth_m[sky_mask] = 0.0

    return depth_m.astype(np.float32)


def write_exr_float32(path: str, depth_m: "np.ndarray") -> None:
    """
    Save single-channel 'Z' float32 OpenEXR.
    Lazy import OpenEXR + Imath. Raises RuntimeError if missing.
    """
    try:
        import Imath
        import OpenEXR
    except ImportError as e:
        raise RuntimeError(
            "Missing OpenEXR. Install with: brew install openexr && pip install OpenEXR"
        ) from e

    import numpy as np

    height, width = depth_m.shape

    # Create EXR header
    header = OpenEXR.Header(width, height)
    header["channels"] = {"Z": Imath.Channel(Imath.PixelType(Imath.PixelType.FLOAT))}

    # Convert depth to binary float32 data
    depth_float32 = depth_m.astype(np.float32)
    z_data = depth_float32.tobytes()

    # Write EXR file
    exr_file = OpenEXR.OutputFile(path, header)
    exr_file.writePixels({"Z": z_data})
    exr_file.close()


def _verify_exr_channel(path: str) -> bool:
    """Verify that the EXR file has a 'Z' channel."""
    try:
        import OpenEXR

        exr_file = OpenEXR.InputFile(path)
        channels = exr_file.header()["channels"]
        return "Z" in channels
    except Exception as e:  # noqa: BLE001
        logger.debug("EXR channel probe failed for %s; returning False: %s", path, e, exc_info=True)
        return False


def infer_batch(
    rgb_dir: str,
    out_dir: str,
    model_id: str = "depth-anything/Depth-Anything-V2-Small-hf",
    near_m: float = 0.5,
    far_m: float = 30.0,
    batch_size: int = 4,
    device: str | None = None,
) -> int:
    """
    Process all *.png in rgb_dir, write *.exr to out_dir. Returns count written.
    Filename mapping: frame_NNNNNN.png → NNNNNN.exr
    """
    import numpy as np
    from PIL import Image

    # Get list of PNG files
    rgb_path = Path(rgb_dir)
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    png_files = sorted(rgb_path.glob("*.png"))
    if not png_files:
        return 0

    # Determine device
    if device is None:
        device = select_device()

    # Load pipeline
    pipe = lazy_load_depth_pipeline(model_id)

    count_written = 0
    total_frames = len(png_files)
    start_time = time.time()

    # Process in batches
    current_batch_size = batch_size

    i = 0
    while i < total_frames:
        batch_end = min(i + current_batch_size, total_frames)
        batch_files = png_files[i:batch_end]

        try:
            # Load images
            images = []
            for png_file in batch_files:
                img = Image.open(png_file).convert("RGB")
                images.append(img)

            # Run inference
            results = pipe(images)

            # Process each result
            for idx, (png_file, result) in enumerate(zip(batch_files, results)):
                # Extract depth from result
                if hasattr(result, "depth"):
                    depth_relative = np.array(result.depth)
                elif isinstance(result, dict) and "depth" in result:
                    depth_relative = np.array(result["depth"])
                else:
                    # Assume result is the depth directly
                    depth_relative = np.array(result)

                # Normalize to metric depth
                depth_m = normalize_depth_to_metric(depth_relative, near_m, far_m)

                # Generate output filename: frame_NNNNNN.png → NNNNNN.exr
                # Or just use the stem with 6-digit padding
                stem = png_file.stem
                # Try to extract frame number if it matches frame_NNNNNN pattern
                if stem.startswith("frame_"):
                    frame_num = stem[6:]
                else:
                    frame_num = stem
                # Ensure 6-digit padding
                try:
                    frame_idx = int(frame_num)
                    out_name = f"{frame_idx:06d}.exr"
                except ValueError:
                    out_name = f"{stem}.exr"

                out_file = out_path / out_name

                # Write EXR
                write_exr_float32(str(out_file), depth_m)
                count_written += 1

            # Progress logging every 100 frames
            if (i + current_batch_size) // 100 > i // 100 or batch_end == total_frames:
                elapsed = time.time() - start_time
                frames_done = batch_end
                if frames_done > 0:
                    eta = elapsed / frames_done * (total_frames - frames_done)
                    print(f"Progress: {frames_done}/{total_frames} frames, " f"ETA: {eta:.1f}s")

            i = batch_end

        except RuntimeError as e:
            if "out of memory" in str(e).lower() and current_batch_size > 1:
                # OOM: reduce batch size and retry
                print("OOM detected, reducing batch size to 1")
                current_batch_size = 1
                # Clear cache if CUDA
                try:
                    import torch

                    if device == "cuda":
                        torch.cuda.empty_cache()
                except ImportError as exc:
                    msg = (
                        "real_depth_filler: torch not available for cache clear "
                        "during OOM recovery: %s"
                    )
                    logger.debug(msg, exc)
                continue
            else:
                raise

    # Verify first EXR file
    if count_written > 0:
        first_exr = sorted(out_path.glob("*.exr"))[0]
        if not _verify_exr_channel(str(first_exr)):
            raise RuntimeError(f"Verification failed: {first_exr} does not have 'Z' channel")
        print(f"Verified: {first_exr} has 'Z' channel")

    return count_written


def main(argv: list[str] | None = None) -> int:
    """CLI: --rgb-dir / --out-dir / --near / --far / --batch-size / --device"""
    parser = argparse.ArgumentParser(description="DepthAnything V2 depth estimation → OpenEXR")
    parser.add_argument(
        "--rgb-dir",
        required=True,
        help="Input directory containing RGB PNG frames",
    )
    parser.add_argument(
        "--out-dir",
        required=True,
        help="Output directory for EXR depth maps",
    )
    parser.add_argument(
        "--near",
        type=float,
        default=0.5,
        help="Near plane distance in meters (default: 0.5)",
    )
    parser.add_argument(
        "--far",
        type=float,
        default=30.0,
        help="Far plane distance in meters (default: 30.0)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=4,
        help="Batch size for inference (default: 4)",
    )
    parser.add_argument(
        "--device",
        type=str,
        default=None,
        help="Device to use (cuda/mps/cpu, auto-detect if not specified)",
    )

    args = parser.parse_args(argv)

    try:
        count = infer_batch(
            rgb_dir=args.rgb_dir,
            out_dir=args.out_dir,
            near_m=args.near,
            far_m=args.far,
            batch_size=args.batch_size,
            device=args.device,
        )
        print(f"Successfully processed {count} frames")
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
