"""depth_anything_v2_inference.py — D1 real depth inference module.

Howard 2026-05-06: Iron Law NO PLACEHOLDER. This module runs DepthAnything
V2 ViT-S inference per video frame and writes per-frame EXR. If anything
fails (model load, frame decode, EXR write), abort — never fall back to
synthetic depth.

Implementation history:
  - First LLM-generated draft used a hallucinated HF path
    (`LiheYoung/depth-anything-v2-small` → 404) and a PyPI package
    (`depth-anything-v2`) that conflicted with our torchvision.
  - Howard rewrote to use `transformers` library's depth-estimation
    pipeline against `depth-anything/Depth-Anything-V2-Small-hf` (the
    real HF model). Verified locally: model loads (~100MB), inference
    returns a per-frame depth tensor.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any

import imageio.v2 as iio
import numpy as np

_PIPELINE: Any = None  # cached HF pipeline


def load_model(variant: str = "vits", device: str = "cpu"):
    """Load and cache the DepthAnything V2 model via HF transformers pipeline.

    Args:
        variant: "vits" | "vitb" | "vitl"
        device: "cpu" or "cuda"
    Returns:
        Cached HF pipeline.
    Raises:
        ValueError on unknown variant.
        RuntimeError if model load fails (network, weights mismatch).
    """
    global _PIPELINE
    if _PIPELINE is not None:
        return _PIPELINE

    repo_map = {
        "vits": "depth-anything/Depth-Anything-V2-Small-hf",
        "vitb": "depth-anything/Depth-Anything-V2-Base-hf",
        "vitl": "depth-anything/Depth-Anything-V2-Large-hf",
    }
    if variant not in repo_map:
        raise ValueError(f"variant must be one of {list(repo_map)}, got {variant!r}")

    try:
        from transformers import pipeline  # noqa: PLC0415
    except ImportError as e:
        raise RuntimeError(
            "transformers package required. Install: pip install transformers"
        ) from e

    try:
        _PIPELINE = pipeline(
            "depth-estimation",
            model=repo_map[variant],
            device=device,
        )
    except Exception as e:
        raise RuntimeError(f"Failed to load DepthAnything V2 {variant}: {e}") from e

    return _PIPELINE


def _write_exr(depth: np.ndarray, target: Path) -> None:
    """Write a single-channel float32 EXR with channel name 'Z'."""
    import OpenEXR  # noqa: PLC0415
    import Imath  # noqa: PLC0415

    h, w = depth.shape
    header = OpenEXR.Header(w, h)
    header["channels"] = {"Z": Imath.Channel(Imath.PixelType(Imath.PixelType.FLOAT))}
    out = OpenEXR.OutputFile(str(target), header)
    out.writePixels({"Z": depth.astype(np.float32).tobytes()})
    out.close()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def infer_depth_for_video(
    video_path: Path,
    output_dir: Path,
    *,
    model_variant: str = "vits",
    device: str = "cpu",
) -> dict[int, str]:
    """Run DepthAnything V2 on every frame of video_path; write per-frame EXR.

    Args:
        video_path: input mp4 (or any imageio-readable video).
        output_dir: target dir for `frame_NNNNNN.exr` files.
        model_variant: vits | vitb | vitl (vits = ~25M params, fastest CPU)
        device: 'cpu' or 'cuda'

    Returns:
        manifest: dict[frame_index → sha256 of EXR file bytes].

    Raises:
        FileNotFoundError: video_path missing.
        RuntimeError: any frame inference / write fails. The output_dir is
                      removed entirely (no partial outputs left behind).
    """
    video_path = Path(video_path)
    output_dir = Path(output_dir)

    if not video_path.exists():
        raise FileNotFoundError(f"video missing: {video_path}")

    # Load model first so we fail fast on weights problems (before opening video).
    model = load_model(model_variant, device)

    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    from PIL import Image  # noqa: PLC0415

    manifest: dict[int, str] = {}
    try:
        try:
            reader = iio.get_reader(str(video_path), format="FFMPEG")
        except Exception as e:
            raise RuntimeError(f"failed to open video {video_path}: {e}") from e

        for frame_idx, frame in enumerate(reader):
            try:
                pil = Image.fromarray(frame.astype(np.uint8))
                result = model(pil)
                # transformers pipeline returns {"predicted_depth": Tensor, "depth": PIL.Image}
                pt = result.get("predicted_depth")
                if pt is None:
                    raise RuntimeError(
                        f"frame {frame_idx}: pipeline returned no predicted_depth"
                    )
                depth = pt.squeeze().detach().cpu().numpy().astype(np.float32)

                target = output_dir / f"frame_{frame_idx:06d}.exr"
                _write_exr(depth, target)
                manifest[frame_idx] = _sha256(target)
            except Exception as e:
                raise RuntimeError(f"frame {frame_idx} inference/write failed: {e}") from e

        try:
            reader.close()
        except Exception:
            pass

        if not manifest:
            raise RuntimeError(f"no frames produced from {video_path}")

    except Exception:
        # Clean up partial output on any failure — IL10 (no partial fakes).
        if output_dir.exists():
            shutil.rmtree(output_dir, ignore_errors=True)
        raise

    return manifest
