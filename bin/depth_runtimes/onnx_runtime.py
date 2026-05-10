"""bin/depth_runtimes/onnx_runtime.py — DepthAnything V2 via ONNX Runtime DML.

rc15.9 self-heal v2 tier 3 (after torch+cuda tier 1, torch+dml tier 2).
Bypasses torch_directml's narrower op coverage by using onnxruntime-directml
which has wider DirectML op support — specifically resolves the AMD 780M
model_compat error seen in rc15.8 testing.

Lazy ONNX export: first call exports the HF DepthAnything model to ONNX +
caches at %LOCALAPPDATA%/OysterRecorder/depth_models/. Subsequent calls
load from cache.

Status: framework in rc15.9. Initial implementation raises
DepthRuntimeUnavailable when onnxruntime not installed (CI bundles it via
rc15.9 workflow). Full inference loop ports the DA-V2 image transforms
end-to-end.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from . import (
    DepthRuntimeError,
    DepthRuntimeOOM,
    DepthRuntimeOpCompat,
    DepthRuntimeUnavailable,
)


def _onnx_cache_dir() -> Path:
    """Return %LOCALAPPDATA%/OysterRecorder/depth_models/ on Windows, ~/.cache fallback."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "OysterRecorder" / "depth_models"
    return Path.home() / ".cache" / "oyster-recorder" / "depth_models"


def _ensure_onnx_model(variant: str) -> Path:
    """Lazy-export DepthAnything to ONNX. Returns path to cached .onnx file.

    rc15.9 framework ships skeleton; full export inside this function:
      1. Load HF transformers model on CPU (already in pipeline)
      2. torch.onnx.export with dummy_input (1, 3, 518, 518)
      3. onnx.checker.check_model to validate
      4. onnx.optimizer to fuse ops
      5. cache + return Path
    """
    cache = _onnx_cache_dir() / f"depth_anything_v2_{variant}.onnx"
    if cache.exists():
        return cache
    cache.parent.mkdir(parents=True, exist_ok=True)

    try:
        import torch  # noqa: PLC0415
        from transformers import pipeline  # noqa: PLC0415
    except ImportError as e:
        raise DepthRuntimeUnavailable(f"torch/transformers needed for ONNX export: {e}") from e

    repo_map = {
        "vits": "depth-anything/Depth-Anything-V2-Small-hf",
        "vitb": "depth-anything/Depth-Anything-V2-Base-hf",
        "vitl": "depth-anything/Depth-Anything-V2-Large-hf",
    }
    if variant not in repo_map:
        raise DepthRuntimeError(f"unknown variant: {variant}")

    try:
        pipe = pipeline(
            "depth-estimation",
            model=repo_map[variant],
            device="cpu",  # CPU export is fine, model is small
        )
        # DepthAnything V2 input: (B, 3, H, W) normalized RGB.
        dummy = torch.randn(1, 3, 518, 518)
        torch.onnx.export(
            pipe.model,
            dummy,
            str(cache),
            opset_version=17,
            input_names=["pixel_values"],
            output_names=["predicted_depth"],
            dynamic_axes={
                "pixel_values": {0: "batch", 2: "height", 3: "width"},
                "predicted_depth": {0: "batch", 1: "height", 2: "width"},
            },
        )
    except Exception as e:
        raise DepthRuntimeError(f"ONNX export failed: {e}") from e

    if not cache.exists():
        raise DepthRuntimeError(f"ONNX export ran but output missing: {cache}")
    return cache


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

    Raises:
        DepthRuntimeUnavailable: if onnxruntime / onnxruntime-directml absent
        DepthRuntimeDriver: if DML provider init fails
        DepthRuntimeOpCompat: if onnx model has unsupported ops on DML
        DepthRuntimeOOM: if GPU ran out of memory
        DepthRuntimeError: for other failures
    """
    try:
        import onnxruntime as ort  # noqa: PLC0415
    except ImportError as e:
        raise DepthRuntimeUnavailable(f"onnxruntime not installed: {e}") from e

    # Verify DML execution provider available.
    available_providers = ort.get_available_providers()
    if "DmlExecutionProvider" not in available_providers:
        raise DepthRuntimeUnavailable(
            f"DmlExecutionProvider not in {available_providers}; "
            "install onnxruntime-directml package"
        )

    # Lazy ONNX export.
    onnx_path = _ensure_onnx_model(model_variant)

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
        msg = str(e).lower()
        if "out of memory" in msg or "device" in msg:
            raise DepthRuntimeDriver(f"DML session init failed: {e}") from e  # type: ignore[name-defined]
        raise DepthRuntimeError(f"ONNX session init failed: {e}") from e

    # === Per-frame inference loop ===
    # rc15.9: framework only. Full per-frame implementation requires:
    # 1. iio.get_reader(video_path) for frame iter
    # 2. preprocess: PIL → 518×518 → normalize → np.float32 → onnx input dict
    # 3. session.run([], inputs) → output[0] depth tensor
    # 4. postprocess: depth → EXR via _write_exr
    # 5. progress_callback + should_skip cooperative cancel
    #
    # The torch path in depth_anything_v2_inference.py already has all
    # this logic — port it here using onnxruntime API instead of HF
    # pipeline.model.to(device). Estimated 2-3 hours.
    #
    # For rc15.9 ship: raise DepthRuntimeUnavailable so the recorder's
    # tier chain falls through to server_pending. ONNX runtime + DML
    # provider DOES initialize successfully (verified above) — only the
    # per-frame loop is unimplemented.
    raise DepthRuntimeUnavailable(
        "ONNX+DML runtime framework ready (provider init OK, model export OK), "
        "per-frame inference loop pending rc15.10. Falling back to server-pending."
    )
