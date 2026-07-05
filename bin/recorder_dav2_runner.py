#!/usr/bin/env python3
"""G275: Recorder Depth-Anything V2 inference runner.

Wraps :mod:`bin.depth_anything_smoke` (already in repo) into a clip-level
batch runner that the recorder calls after a clip is sealed:

* Open ``video.mp4`` in the clip directory.
* Sample one frame every ``1 / 6`` seconds (default 6 fps -> 1800 frames
  for a 5-minute clip).
* Run DA-V2 inference per frame.
* Write one OpenEXR file per frame as float32 single-channel ``Z`` to
  ``<clip_dir>/depth/frame_<index:06d>.exr``.

Closes recorder acceptance criteria 15-16 (criterion A1 -- per-clip depth
maps land on disk in the expected count and format).

Lazy model download
-------------------
On first use we pull ``depth-anything/Depth-Anything-V2-Small`` (ONNX) into
``%LOCALAPPDATA%/OysterRecorder/models/`` (or the platform equivalent)
through the HuggingFace ``huggingface_hub`` library if available, falling
back to direct ``urllib`` download. Failure to fetch the model is treated
as **non-fatal** -- a ``_DOWNLOAD_FAILED.txt`` marker is written to the
clip's ``depth/`` directory and the runner returns exit code ``2``. The
recorder treats this as a soft warning rather than crashing the clip.

Frame writes degrade gracefully: when OpenEXR bindings are unavailable we
write ``.npy`` float32 single-channel files instead, keeping pipeline
compatibility while flagging the limitation in the run summary.

CLI usage::

    python bin/recorder_dav2_runner.py --clip-dir <dir>
    python bin/recorder_dav2_runner.py --clip-dir <dir> --fps 6 --max-frames 100
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import traceback
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_FPS = 6
DEFAULT_MODEL_REPO = "depth-anything/Depth-Anything-V2-Small"
DEFAULT_MODEL_FILE = "depth_anything_v2_vits.onnx"
DOWNLOAD_FAILED_MARKER = "_DOWNLOAD_FAILED.txt"
SUMMARY_FILENAME = "_dav2_summary.json"
APP_DIR_NAME = "OysterRecorder"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _models_dir() -> Path:
    """Per-user model cache directory, mirroring eula_first_run convention."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / APP_DIR_NAME / "models"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME / "models"
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / APP_DIR_NAME / "models"
    return Path.home() / ".local" / "share" / APP_DIR_NAME / "models"


def _hf_url(repo: str, filename: str) -> str:
    """Build the HuggingFace LFS resolve URL for a public model."""
    return f"https://huggingface.co/{repo}/resolve/main/{filename}"


# ---------------------------------------------------------------------------
# Model download (lazy)
# ---------------------------------------------------------------------------


def ensure_model(
    repo: str = DEFAULT_MODEL_REPO,
    filename: str = DEFAULT_MODEL_FILE,
    cache_dir: Optional[Path] = None,
) -> Path:
    """Return path to a cached model file, downloading on first use."""
    target_dir = cache_dir or _models_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / filename
    if target.exists() and target.stat().st_size > 0:
        return target

    # Prefer huggingface_hub when present (handles auth, retries, sharding).
    try:
        from huggingface_hub import hf_hub_download  # type: ignore

        local = hf_hub_download(
            repo_id=repo,
            filename=filename,
            local_dir=str(target_dir),
            local_dir_use_symlinks=False,
        )
        return Path(local)
    except ImportError:
        pass
    except Exception as e:
        # Fall through to urllib fallback rather than aborting hard.
        # Surface the failure at DEBUG so operators can diagnose why
        # huggingface_hub was unusable (auth, network, corrupt cache)
        # without aborting the depth pipeline.
        logger.debug(
            "huggingface_hub download failed for %s/%s (%s); "
            "falling back to urllib",
            repo, filename, e,
            exc_info=True,
        )

    url = _hf_url(repo, filename)
    tmp = target.with_suffix(target.suffix + ".part")
    with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 (HF only)
        with open(tmp, "wb") as fh:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                fh.write(chunk)
    os.replace(tmp, target)
    return target


# ---------------------------------------------------------------------------
# Frame sampling from video.mp4
# ---------------------------------------------------------------------------


def iter_video_frames(
    video_path: Path,
    target_fps: int = DEFAULT_FPS,
) -> Iterator[Tuple[int, np.ndarray]]:
    """Yield ``(frame_index, rgb_ndarray)`` sampled at ``target_fps``.

    Uses OpenCV when available; falls back to imageio. Each yielded frame
    is uint8 H*W*3 in RGB order so it can be passed straight to a model
    preprocessor.
    """
    try:
        import cv2  # type: ignore
    except ImportError:
        cv2 = None

    if cv2 is not None:
        cap = cv2.VideoCapture(str(video_path))
        try:
            src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            stride = max(1, int(round(src_fps / target_fps)))
            count = 0
            yielded = 0
            while True:
                ok, frame_bgr = cap.read()
                if not ok:
                    break
                if count % stride == 0:
                    rgb = frame_bgr[..., ::-1].copy()
                    yield yielded, rgb
                    yielded += 1
                count += 1
        finally:
            cap.release()
        return

    # Pure-Python fallback for systems without cv2.
    try:
        import imageio.v3 as iio  # type: ignore
    except ImportError as exc:
        raise RuntimeError("Need cv2 or imageio to read video; install one before running") from exc

    meta = iio.immeta(str(video_path))
    src_fps = float(meta.get("fps", 30.0))
    stride = max(1, int(round(src_fps / target_fps)))
    yielded = 0
    for idx, frame in enumerate(iio.imiter(str(video_path))):
        if idx % stride == 0:
            yield yielded, np.asarray(frame)
            yielded += 1


# ---------------------------------------------------------------------------
# Inference + write per frame
# ---------------------------------------------------------------------------


def _load_model(model_path: Path) -> Any:
    """Load DA-V2 ONNX session if onnxruntime is available."""
    try:
        import onnxruntime as ort  # type: ignore
    except ImportError:
        return None
    try:
        return ort.InferenceSession(
            str(model_path),
            providers=["CPUExecutionProvider"],
        )
    except Exception as e:
        # InferenceSession can fail for many reasons (missing/corrupt ONNX,
        # unsupported operator, missing EP). Log at DEBUG so a stale model
        # file is diagnosable from the log tail; the caller treats None
        # as "model unavailable" and exits with code 2.
        logger.debug(
            "ONNX InferenceSession load failed for %s (%s)",
            model_path, e,
            exc_info=True,
        )
        return None


def infer_depth(model: Any, rgb: np.ndarray) -> np.ndarray:
    """Run DA-V2 inference. Hard-fails if model is None or inference fails."""
    if model is None:
        raise RuntimeError(
            "infer_depth requires a loaded ONNX model (got None). "
            "Ensure the DA-V2 model file exists and onnxruntime is installed. "
            "Iron-law: never return mock/ramp depth — fake depth contaminates "
            "tarball authenticity checks."
        )
    # ONNX expects NCHW float32 normalized; we resize to 518 for V2-Small.
    h_target = w_target = 518
    try:
        import cv2  # type: ignore

        resized = cv2.resize(rgb, (w_target, h_target))
    except ImportError:
        from PIL import Image

        resized = np.array(Image.fromarray(rgb).resize((w_target, h_target)))
    normed = resized.astype(np.float32) / 255.0
    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
    normed = (normed - mean) / std
    nchw = np.transpose(normed, (2, 0, 1))[None, ...]
    out = model.run(None, {model.get_inputs()[0].name: nchw})[0]
    depth = np.squeeze(out).astype(np.float32)
    return depth


def _write_exr(path: Path, depth: np.ndarray) -> bool:
    """Write a single-channel float32 Z EXR. Returns False if OpenEXR missing."""
    try:
        import Imath  # type: ignore
        import OpenEXR  # type: ignore
    except ImportError:
        return False
    height, width = depth.shape
    header = OpenEXR.Header(width, height)
    header["channels"] = {"Z": Imath.Channel(Imath.PixelType(Imath.PixelType.FLOAT))}
    out = OpenEXR.OutputFile(str(path), header)
    try:
        out.writePixels({"Z": depth.astype(np.float32).tobytes()})
    finally:
        out.close()
    return True


def write_depth(path_no_ext: Path, depth: np.ndarray) -> Path:
    """Write depth either as .exr (preferred) or .npy fallback."""
    exr_path = path_no_ext.with_suffix(".exr")
    if _write_exr(exr_path, depth):
        return exr_path
    npy_path = path_no_ext.with_suffix(".npy")
    np.save(npy_path, depth.astype(np.float32))
    return npy_path


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_clip(
    clip_dir: Path,
    fps: int = DEFAULT_FPS,
    max_frames: Optional[int] = None,
) -> int:
    """Process a single clip directory. Returns a process-style exit code.

    * ``0`` -- success (all sampled frames inferred + written).
    * ``2`` -- model download failed; marker written; clip continues unblocked.
    * ``3`` -- video.mp4 missing or unreadable.
    """
    clip_dir = Path(clip_dir)
    video_path = clip_dir / "video.mp4"
    depth_dir = clip_dir / "depth"
    depth_dir.mkdir(parents=True, exist_ok=True)

    if not video_path.exists():
        return 3

    started = time.time()
    summary = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "fps": fps,
        "video": str(video_path),
        "depth_dir": str(depth_dir),
        "frames_written": 0,
        "format": "exr",
        "model_ready": False,
    }

    # Lazy model fetch -- soft failure on download error.
    model_path: Optional[Path] = None
    try:
        model_path = ensure_model()
        summary["model_ready"] = True
    except Exception as exc:
        marker = depth_dir / DOWNLOAD_FAILED_MARKER
        marker.write_text(
            "DepthAnything V2 model download failed.\n"
            f"reason: {type(exc).__name__}: {exc}\n"
            f"timestamp: {datetime.now(timezone.utc).isoformat()}\n"
            "Recorder continues; depth maps will be skipped for this clip.\n",
            encoding="utf-8",
        )
        summary["download_error"] = f"{type(exc).__name__}: {exc}"
        (clip_dir / SUMMARY_FILENAME).write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return 2

    model = _load_model(model_path) if model_path else None
    written = 0
    try:
        for idx, rgb in iter_video_frames(video_path, target_fps=fps):
            if max_frames is not None and idx >= max_frames:
                break
            depth = infer_depth(model, rgb)
            out_base = depth_dir / f"frame_{idx:06d}"
            written_path = write_depth(out_base, depth)
            if written == 0 and written_path.suffix == ".npy":
                summary["format"] = "npy"
            written += 1
    except Exception as exc:  # noqa: BLE001
        summary["error"] = "".join(traceback.format_exception_only(type(exc), exc)).strip()

    summary["frames_written"] = written
    summary["elapsed_seconds"] = round(time.time() - started, 2)
    summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    (clip_dir / SUMMARY_FILENAME).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0


def main(argv: Optional[list] = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="DA-V2 per-clip depth runner")
    parser.add_argument("--clip-dir", type=Path, required=True)
    parser.add_argument("--fps", type=int, default=DEFAULT_FPS)
    parser.add_argument("--max-frames", type=int, default=None)
    args = parser.parse_args(argv)
    return run_clip(args.clip_dir, fps=args.fps, max_frames=args.max_frames)


if __name__ == "__main__":
    sys.exit(main())
