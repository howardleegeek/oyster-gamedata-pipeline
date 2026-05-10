"""bin/depth_runtimes/onnx_runtime.py — DepthAnything V2 via ONNX Runtime DML.

rc15.9 self-heal v2 tier 3 (after torch+cuda tier 1, torch+dml tier 2).
Bypasses torch_directml's narrower op coverage by using onnxruntime-directml
which has wider DirectML op support — specifically resolves the AMD 780M
× DepthAnything V2 model_compat error seen in rc15.8 testing.

Strategy: pre-converted HF community ONNX (`onnx-community/Depth-Anything-
V2-Small`). Avoids fragile runtime torch.onnx.export. Downloaded lazily
on first use to %LOCALAPPDATA%/OysterRecorder/depth_models/.

PRD compliance: produces same `frame_{idx:06d}.exr` files as the torch
path (single-channel float32 Z, 2× downsampled to ~640×360, PIZ
compressed). Buyer ingest pipeline cannot distinguish torch-DML from
ONNX-DML output — same model weights, same EXR format.
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import Any, Callable, Optional

# Re-export exception types so callers can catch generically.
from . import (
    DepthRuntimeDriver,
    DepthRuntimeError,
    DepthRuntimeOOM,
    DepthRuntimeOpCompat,
    DepthRuntimeUnavailable,
)


# ImageNet normalization — matches DepthAnything V2 training.
_IMAGENET_MEAN = (0.485, 0.456, 0.406)
_IMAGENET_STD = (0.229, 0.224, 0.225)
_INPUT_SIZE = 518  # DepthAnything V2 native input resolution
_PROGRESS_CALLBACK_EVERY_N_FRAMES = 15

# HF community pre-converted ONNX (avoids fragile runtime torch.onnx.export).
# Repo: onnx-community/Depth-Anything-V2-Small
# File: onnx/model.onnx (~99 MB, fp32; or model_fp16.onnx ~50 MB)
_ONNX_REPO = "onnx-community/Depth-Anything-V2-Small"
_ONNX_FILENAME = "onnx/model.onnx"
# rc15.15 C3: real model is ~99 MB. 10MB sanity check (rc15.9 original) lets
# truncated downloads (e.g. 50MB partial) silently pass and crash later in
# InferenceSession with a confusing protobuf parse error. 80MB floor is
# tight enough to catch all observed truncations + loose enough to absorb
# minor HF re-uploads.
_ONNX_MIN_SIZE_BYTES = 80 * 1024 * 1024
_ONNX_MAX_SIZE_BYTES = 200 * 1024 * 1024  # paranoid upper-bound


def _onnx_cache_dir() -> Path:
    """Return %LOCALAPPDATA%/OysterRecorder/depth_models/ on Windows, ~/.cache fallback."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "OysterRecorder" / "depth_models"
    return Path.home() / ".cache" / "oyster-recorder" / "depth_models"


def _is_valid_onnx_cache(path: Path) -> bool:
    """rc15.15 C3: validate cached ONNX is full + parseable."""
    if not path.exists():
        return False
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if not (_ONNX_MIN_SIZE_BYTES <= size <= _ONNX_MAX_SIZE_BYTES):
        return False
    # Cheap sniff: every ONNX file starts with protobuf magic for ModelProto.
    # Field 1 = ir_version (varint) so byte 0 is 0x08 (field=1, wire=varint).
    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
        if not head or head[0] != 0x08:
            return False
    except OSError:
        return False
    return True


def _ensure_onnx_model(variant: str = "vits") -> Path:
    """Lazy-download pre-converted DepthAnything V2 ONNX from HF community.

    Returns path to cached .onnx file. Raises DepthRuntimeUnavailable if
    huggingface_hub missing or network failure on first run.

    rc15.15 C3: validates cached file isn't truncated; on partial-download
    detection, removes the stale file and re-downloads.
    """
    cache = _onnx_cache_dir() / f"depth_anything_v2_{variant}.onnx"
    if _is_valid_onnx_cache(cache):
        return cache
    if cache.exists():
        # Stale or truncated. Nuke + re-download.
        try:
            cache.unlink()
        except OSError:
            pass
    cache.parent.mkdir(parents=True, exist_ok=True)

    try:
        from huggingface_hub import hf_hub_download  # noqa: PLC0415
    except ImportError as e:
        raise DepthRuntimeUnavailable(
            f"huggingface_hub required to download ONNX model: {e}"
        ) from e

    try:
        downloaded = hf_hub_download(
            repo_id=_ONNX_REPO,
            filename=_ONNX_FILENAME,
            cache_dir=str(cache.parent / "_hf_cache"),
        )
        # Copy/symlink to predictable cache path.
        import shutil  # noqa: PLC0415
        shutil.copy2(downloaded, cache)
    except Exception as e:
        raise DepthRuntimeError(
            f"failed to download {_ONNX_REPO}/{_ONNX_FILENAME}: {e}"
        ) from e

    # rc15.15 C3: verify the freshly-copied file is valid (network can
    # truncate, disk can fill mid-copy).
    if not _is_valid_onnx_cache(cache):
        try:
            actual_size = cache.stat().st_size if cache.exists() else 0
        except OSError:
            actual_size = -1
        try:
            cache.unlink()
        except OSError:
            pass
        raise DepthRuntimeError(
            f"ONNX model verification failed after download: "
            f"size={actual_size} not in [{_ONNX_MIN_SIZE_BYTES}, {_ONNX_MAX_SIZE_BYTES}] "
            f"or magic mismatch. Removed corrupted cache."
        )

    return cache


def _preprocess_frame(frame_array, target_size: int = _INPUT_SIZE):
    """Convert HxWx3 uint8 RGB array → 1×3×518×518 float32 ImageNet-normalized."""
    import numpy as np  # noqa: PLC0415
    from PIL import Image  # noqa: PLC0415

    pil = Image.fromarray(frame_array.astype(np.uint8))
    pil = pil.resize((target_size, target_size), Image.BILINEAR)
    arr = np.asarray(pil, dtype=np.float32) / 255.0
    # HWC → CHW
    arr = arr.transpose(2, 0, 1)
    # ImageNet normalize per-channel
    for c in range(3):
        arr[c] = (arr[c] - _IMAGENET_MEAN[c]) / _IMAGENET_STD[c]
    # Add batch dim
    return arr[np.newaxis, ...].astype(np.float32)


def _write_exr(depth_array, target_path: Path) -> None:
    """Write single-channel float32 EXR with channel name 'Z'.

    Matches torch path's _write_exr in depth_anything_v2_inference.py:
    2× block-mean downsample, PIZ compression. Identical wire format
    so buyer ingest can't tell which runtime produced the file.
    """
    import numpy as np  # noqa: PLC0415
    import OpenEXR  # noqa: PLC0415
    import Imath  # noqa: PLC0415

    H, W = depth_array.shape
    new_H, new_W = H // 2, W // 2
    depth = depth_array[: new_H * 2, : new_W * 2].astype(np.float32)
    depth = depth.reshape(new_H, 2, new_W, 2).mean(axis=(1, 3)).astype(np.float32)

    h, w = depth.shape
    header = OpenEXR.Header(w, h)
    header["compression"] = Imath.Compression(Imath.Compression.PIZ_COMPRESSION)
    header["channels"] = {"Z": Imath.Channel(Imath.PixelType(Imath.PixelType.FLOAT))}
    out = OpenEXR.OutputFile(str(target_path), header)
    out.writePixels({"Z": depth.tobytes()})
    out.close()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda fh=fh: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _classify_ort_exception(exc: Exception) -> Exception:
    """Map onnxruntime errors to our typed exception hierarchy."""
    msg = str(exc).lower()
    name = type(exc).__name__.lower()
    if "out of memory" in msg or "outofmemory" in name or "allocator" in msg:
        return DepthRuntimeOOM(f"{type(exc).__name__}: {exc}")
    if "directml" in msg or "dxgi" in msg or "d3d" in msg or "device" in msg:
        return DepthRuntimeDriver(f"{type(exc).__name__}: {exc}")
    if "shape" in msg or "tensor" in msg or "rank" in msg or "dim" in msg:
        return DepthRuntimeOpCompat(f"{type(exc).__name__}: {exc}")
    return DepthRuntimeError(f"{type(exc).__name__}: {exc}")


def infer_depth_for_video(
    video_path: Path,
    output_dir: Path,
    *,
    model_variant: str = "vits",
    progress_callback: Optional[Callable[[int, int], None]] = None,
    should_skip: Optional[Callable[[], bool]] = None,
    **_: Any,
) -> dict[int, str]:
    """ONNX Runtime DirectML inference path.

    Returns dict[frame_idx → sha256 hex]. Same shape as torch path so the
    recorder's existing fallback chain receives a uniform return type.

    Raises:
        DepthRuntimeUnavailable: if onnxruntime / onnxruntime-directml absent
        DepthRuntimeDriver: if DML provider init fails
        DepthRuntimeOpCompat: if ONNX model has unsupported ops on DML
        DepthRuntimeOOM: if GPU ran out of memory
        DepthRuntimeError: for other failures
    """
    try:
        import imageio.v3 as iio  # noqa: PLC0415
        import numpy as np  # noqa: PLC0415
        import onnxruntime as ort  # noqa: PLC0415
    except ImportError as e:
        raise DepthRuntimeUnavailable(f"onnxruntime/imageio/numpy missing: {e}") from e

    available_providers = ort.get_available_providers()
    if "DmlExecutionProvider" not in available_providers:
        raise DepthRuntimeUnavailable(
            f"DmlExecutionProvider not in {available_providers}; "
            "install onnxruntime-directml package"
        )

    # Lazy ONNX download from HF community.
    try:
        onnx_path = _ensure_onnx_model(model_variant)
    except DepthRuntimeError:
        raise
    except Exception as e:
        raise DepthRuntimeError(f"ONNX model fetch failed: {e}") from e

    # Create DML session.
    try:
        session_options = ort.SessionOptions()
        session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        session = ort.InferenceSession(
            str(onnx_path),
            sess_options=session_options,
            providers=["DmlExecutionProvider", "CPUExecutionProvider"],
        )
    except Exception as e:
        raise _classify_ort_exception(e)

    # Identify input/output names dynamically (HF ONNX uses 'pixel_values').
    input_name = session.get_inputs()[0].name
    output_name = session.get_outputs()[0].name

    output_dir.mkdir(parents=True, exist_ok=True)

    # Count total frames for progress.
    try:
        meta = iio.immeta(str(video_path))
        total_frames = int(meta.get("nframes") or meta.get("duration", 0) * meta.get("fps", 30))
    except Exception:
        total_frames = 0

    if progress_callback is not None:
        try:
            progress_callback(0, total_frames)
        except Exception:
            pass

    manifest: dict[int, str] = {}
    skipped = False

    try:
        reader = iio.imiter(str(video_path))
        for frame_idx, frame in enumerate(reader):
            if should_skip is not None:
                try:
                    if should_skip():
                        skipped = True
                        break
                except Exception:
                    pass

            try:
                input_tensor = _preprocess_frame(np.asarray(frame))
                outputs = session.run(
                    [output_name],
                    {input_name: input_tensor},
                )
                depth = outputs[0]
                # Output shape: (1, 1, 518, 518) or (1, 518, 518). Squeeze to (518, 518).
                while depth.ndim > 2:
                    depth = depth.squeeze(0)
                depth = depth.astype(np.float32)

                target = output_dir / f"frame_{frame_idx:06d}.exr"
                _write_exr(depth, target)
                manifest[frame_idx] = _sha256(target)

                # Memory cleanup (DML tensors).
                del input_tensor, outputs, depth
            except Exception as e:
                raise _classify_ort_exception(e)

            done = frame_idx + 1
            if done % _PROGRESS_CALLBACK_EVERY_N_FRAMES == 0:
                if progress_callback is not None:
                    try:
                        progress_callback(done, total_frames)
                    except Exception:
                        pass
            if done % 50 == 0:
                import gc as _gc  # noqa: PLC0415
                _gc.collect()
    except DepthRuntimeError:
        raise
    except Exception as e:
        raise _classify_ort_exception(e)

    if skipped:
        # Best-effort cleanup of partial frames.
        for f in output_dir.glob("frame_*.exr"):
            try:
                f.unlink()
            except Exception:
                pass
        raise DepthRuntimeError("user skipped during ONNX inference")

    if progress_callback is not None:
        try:
            progress_callback(len(manifest), total_frames or len(manifest))
        except Exception:
            pass

    return manifest
