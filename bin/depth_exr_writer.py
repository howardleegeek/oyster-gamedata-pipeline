#!/usr/bin/env python3
"""
bin/depth_exr_writer.py — Generate PRD §3.4 depth EXR files post-record.

Per PRD §3.4 (oyster-audit/PRD-DATA-REQUIREMENTS.md):
  * Sampling rate: 6 fps (5 min × 6 = 1800 frames; NOT 30 fps, NOT 1 Hz)
  * Format: OpenEXR single-channel named "Z", float32
  * Units: meters along the optical Z axis
  * Invalid pixels: 0 (sky / transparent / clipped)
  * Filename: timestamp-aligned `000000.exr` (t=0) → `000005.exr`
    (t=0.83s @ 6fps) → ... → `001799.exr`
  * Resolution: 1920×1080 (matches mp4)
  * Location: `<session_dir>/depth/*.exr`

Pipeline
========
  1. Open ``session_dir/recording.mp4`` via cv2 (OpenCV).
  2. Read native fps (30 typical) and compute stride = round(native_fps / 6).
     We pull every Nth frame from the decode loop — this is much cheaper
     than decoding all 9000 frames then dropping 7200, because cv2 lets us
     decode-and-discard the headers without paying for the YUV→BGR convert
     on dropped frames (`cap.grab()`).
  3. For each sampled frame: run DepthAnything V2 Small inference via
     onnxruntime-directml. DML provider on AMD/Intel/NVIDIA iGPU; falls
     back to CPUExecutionProvider when DML is unavailable.
  4. Convert model output to metric depth in meters along Z.
     Until mc-mod ground-truth depth IPC ships in rc20, we use a Minecraft
     render-distance heuristic: 12 chunks default × 16 blocks = 192 m far
     plane. DepthAnything V2 returns relative *inverse* depth (larger =
     closer), so we invert + rescale to [near=0.1, far=192] meters. This
     is documented as APPROXIMATE — the model is monocular and uncalibrated;
     consumers should treat magnitudes as ordinal until mc-mod metric data
     replaces this path.
  5. Invalid-pixel mask = (top-5% rows of frame) ∨ (depth > FAR_PLANE_M).
     Set masked pixels to exactly 0.0 (PRD requires literal 0 for invalid).
  6. Write each frame as OpenEXR float32 single-channel "Z" to
     `session_dir/depth/<6-digit-zero-padded-index>.exr`.

Dependencies
============
Required (exit 2 with install hint if any is missing):
  * `pip install opencv-python-headless`     — frame decode (cv2)
  * `pip install onnxruntime-directml`       — Windows iGPU/dGPU inference
        (or `pip install onnxruntime` for CPU-only fallback; both ship the
        same `import onnxruntime as ort` namespace)
  * `pip install openexr`                    — Imath + OpenEXR Python bindings
        (Linux/macOS: also need `libopenexr-dev`; Windows: wheel ships .pyd)
  * `pip install numpy`                      — array math

All four are part of the PyInstaller bundle for the Windows installer (see
README PyInstaller spec, hidden-imports).

Model file
==========
DepthAnything V2 Small ONNX (~99 MB fp32, ~50 MB fp16). Resolution path:
  1. ``OYSTER_DEPTH_MODEL_PATH`` env var (absolute path), if set.
  2. ``<install_root>/depth_models/depth_anything_v2_small.onnx`` where
     ``<install_root>`` is the parent of ``bin/``.
  3. Per-user cache at ``%LOCALAPPDATA%/OysterRecorder/depth_models/`` on
     Windows, ``~/.cache/oyster-recorder/depth_models/`` elsewhere — matches
     ``bin/depth_runtimes/onnx_runtime.py`` cache convention.

If the model is missing the script exits 2 with a clear instruction to fetch
from ``huggingface.co/onnx-community/Depth-Anything-V2-Small`` (file
``onnx/model.onnx``) and place it at one of the paths above.

Runtime expectations
====================
  * AMD 780M iGPU via DML: ~150 ms/frame → 1800 × 0.15 = ~4.5 min total
  * NVIDIA RTX via DML:    ~50 ms/frame  → ~1.5 min total
  * Apple Silicon / CPU:   ~2 s/frame    → ~60 min total (logged as WARN
                                            so user knows to enable GPU)

CLI
===
  python bin/depth_exr_writer.py <session_dir>
  python bin/depth_exr_writer.py <session_dir> --model-path /abs/path/model.onnx
  python bin/depth_exr_writer.py <session_dir> --target-fps 6.0 --max-frames 1800
  python bin/depth_exr_writer.py <session_dir> --verbose

Wire-in
=======
``bin/finalize_session.py`` calls this between gameinfo.xlsx generation
(step 3) and audio_check.json generation (step 4) — see finalize step 4/5.
Opt-out: ``--no-depth`` flag on finalize_session OR ``OYSTER_SKIP_DEPTH=1``
env var for fast finalize during testing.

Exit codes
==========
  0  — success (≥ ``--max-frames`` EXR files written OR mp4 ended early
       and we wrote everything we could)
  1  — at least one frame failed to decode/write, but the rest are on disk
       (partial output kept)
  2  — missing dependency, missing model, missing recording.mp4 (nothing
       was written)

Idempotence
===========
Re-running on a session that already has ≥ ``--max-frames`` EXR files in
``depth/`` exits 0 immediately with a "already populated" message.

Author: Oyster GameData Recorder rc19
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path
from typing import Iterator, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# PRD §3.4: 6 fps, 1800 frames for a 5-min recording, 1920×1080.
PRD_TARGET_FPS: float = 6.0
PRD_EXPECTED_FRAMES: int = 1800
PRD_WIDTH: int = 1920
PRD_HEIGHT: int = 1080

# DepthAnything V2 native input resolution. Matches ImageNet ViT-Small.
DA_V2_INPUT_SIZE: int = 518
DA_V2_IMAGENET_MEAN: Tuple[float, float, float] = (0.485, 0.456, 0.406)
DA_V2_IMAGENET_STD: Tuple[float, float, float] = (0.229, 0.224, 0.225)

# Metric calibration heuristic — Minecraft render distance.
# Default render distance = 12 chunks × 16 blocks/chunk = 192 blocks = 192 m.
# 1 block = 1 m by MC convention. NEAR plane is camera clip plane (~0.1m).
MC_NEAR_PLANE_M: float = 0.1
MC_FAR_PLANE_M: float = 192.0

# Sky mask: top fraction of frame is treated as sky-likely. 5% of 1080 = 54 px.
# We also mask any post-calibration depth > FAR plane as invalid (clipped).
SKY_MASK_TOP_FRACTION: float = 0.05

# Default model file name. Matches `bin/depth_runtimes/onnx_runtime.py` cache.
DEFAULT_MODEL_FILENAME: str = "depth_anything_v2_small.onnx"

# Logging configuration. We use logging (not print) per
# rules/ecc-py-hooks.md.
log = logging.getLogger("depth_exr_writer")


# ---------------------------------------------------------------------------
# Dependency probing — fail fast with actionable install instructions
# ---------------------------------------------------------------------------


class MissingDependency(RuntimeError):
    """A required Python dependency is not installed."""


def _probe_dependencies() -> None:
    """Import every required dep up-front so we fail with one clear message.

    Raises MissingDependency on first missing dep, with the exact pip install
    command in the message. Calling this BEFORE we open any file keeps the
    error path uniform (no partial work).
    """
    missing: list[Tuple[str, str]] = []
    try:
        import cv2  # noqa: F401, PLC0415
    except ImportError:
        missing.append(("cv2", "pip install opencv-python-headless"))
    try:
        import numpy  # noqa: F401, PLC0415
    except ImportError:
        missing.append(("numpy", "pip install numpy"))
    try:
        import onnxruntime  # noqa: F401, PLC0415
    except ImportError:
        missing.append((
            "onnxruntime",
            "pip install onnxruntime-directml  (or onnxruntime for CPU)",
        ))
    try:
        import Imath  # noqa: F401, PLC0415
        import OpenEXR  # noqa: F401, PLC0415
    except ImportError:
        missing.append(("OpenEXR/Imath", "pip install openexr"))

    if missing:
        lines = ["Missing required dependencies:"]
        for name, hint in missing:
            lines.append(f"  - {name}: {hint}")
        raise MissingDependency("\n".join(lines))


# ---------------------------------------------------------------------------
# Model resolution
# ---------------------------------------------------------------------------


def _install_root() -> Path:
    """Return the parent dir of this script's parent dir — i.e. repo root."""
    return Path(__file__).resolve().parent.parent


def _platform_cache_dir() -> Path:
    """Per-user cache, mirroring bin/depth_runtimes/onnx_runtime.py."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "OysterRecorder" / "depth_models"
    return Path.home() / ".cache" / "oyster-recorder" / "depth_models"


def resolve_model_path(explicit: Optional[Path] = None) -> Path:
    """Return path to the DA-V2 ONNX file. Raises FileNotFoundError if missing.

    Search order:
      1. ``explicit`` CLI arg (if provided)
      2. ``OYSTER_DEPTH_MODEL_PATH`` env var
      3. ``<install_root>/depth_models/depth_anything_v2_small.onnx``
      4. ``<platform_cache>/depth_anything_v2_small.onnx``
    """
    if explicit is not None:
        if explicit.exists():
            return explicit
        raise FileNotFoundError(
            f"--model-path {explicit} does not exist"
        )

    env = os.environ.get("OYSTER_DEPTH_MODEL_PATH")
    if env:
        p = Path(env)
        if p.exists():
            return p
        raise FileNotFoundError(
            f"OYSTER_DEPTH_MODEL_PATH={env} does not exist"
        )

    candidates = [
        _install_root() / "depth_models" / DEFAULT_MODEL_FILENAME,
        _platform_cache_dir() / DEFAULT_MODEL_FILENAME,
    ]
    for c in candidates:
        if c.exists() and c.stat().st_size > 1_000_000:
            return c

    raise FileNotFoundError(
        "DepthAnything V2 Small ONNX model not found.\n"
        "Searched:\n"
        + "\n".join(f"  - {c}" for c in candidates)
        + "\n\nFix: download from huggingface.co/onnx-community/"
          "Depth-Anything-V2-Small (file: onnx/model.onnx, ~99 MB) and\n"
        f"copy to {candidates[0]} OR set OYSTER_DEPTH_MODEL_PATH=/abs/path."
    )


# ---------------------------------------------------------------------------
# ONNX Runtime session
# ---------------------------------------------------------------------------


def _new_inference_session(model_path: Path) -> Tuple[object, str]:
    """Build an onnxruntime InferenceSession with DML→CPU fallback.

    Returns (session, provider_name_used). Raises RuntimeError on full
    failure (which the caller maps to exit code 2).
    """
    import onnxruntime as ort  # type: ignore  # noqa: PLC0415

    available = set(ort.get_available_providers())
    # Preference order. DML first when present; CPU as last resort.
    preferred = ["DmlExecutionProvider", "CPUExecutionProvider"]
    providers = [p for p in preferred if p in available]
    if not providers:
        # Unknown ORT build — try whatever it has.
        providers = list(available) or ["CPUExecutionProvider"]

    last_err: Optional[Exception] = None
    for prov in providers:
        try:
            sess = ort.InferenceSession(str(model_path), providers=[prov])
            return sess, prov
        except Exception as exc:  # provider-specific init can fail
            last_err = exc
            log.warning("provider %s failed to init: %s — trying next", prov, exc)

    raise RuntimeError(
        f"Could not initialize any onnxruntime provider for {model_path}. "
        f"Last error: {last_err}"
    )


# ---------------------------------------------------------------------------
# Frame sampling at 6 fps from a 30 fps mp4
# ---------------------------------------------------------------------------


def iter_sampled_frames(
    video_path: Path,
    target_fps: float = PRD_TARGET_FPS,
    max_frames: int = PRD_EXPECTED_FRAMES,
) -> Iterator[Tuple[int, "object"]]:
    """Yield ``(sample_index, rgb_uint8_HxWx3)`` at target_fps.

    Uses ``cap.grab()`` (no decode) to skip frames cheaply, then
    ``cap.retrieve()`` + BGR→RGB on every Nth frame. This is materially
    faster than decoding every frame and dropping 5/6 of the output.
    """
    import cv2  # type: ignore  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"cv2 could not open video: {video_path}")

    try:
        src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        # Defensive: weird metadata can report 0 or NaN; clamp to 30.
        if not (1.0 <= src_fps <= 240.0):
            log.warning("video reports fps=%s; clamping to 30.0", src_fps)
            src_fps = 30.0
        stride = max(1, int(round(src_fps / float(target_fps))))
        log.info(
            "video native fps=%.3f, stride=%d (every %dth frame → ~%.2f fps)",
            src_fps,
            stride,
            stride,
            src_fps / stride,
        )

        sample_idx = 0
        raw_idx = 0
        while sample_idx < max_frames:
            if raw_idx % stride == 0:
                ok, frame_bgr = cap.read()
                if not ok:
                    break
                # cv2 returns BGR uint8. Most ONNX preprocs want RGB.
                rgb = frame_bgr[..., ::-1].copy()
                yield sample_idx, np.ascontiguousarray(rgb)
                sample_idx += 1
            else:
                # cap.grab() advances without decoding — fast skip.
                if not cap.grab():
                    break
            raw_idx += 1
    finally:
        cap.release()


# ---------------------------------------------------------------------------
# Preprocessing + inference + metric calibration
# ---------------------------------------------------------------------------


def _preprocess_rgb_to_nchw(rgb_uint8) -> "object":
    """Resize H×W×3 uint8 RGB → 1×3×518×518 normalized float32 NCHW."""
    import cv2  # type: ignore  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    resized = cv2.resize(rgb_uint8, (DA_V2_INPUT_SIZE, DA_V2_INPUT_SIZE))
    normed = resized.astype(np.float32) / 255.0
    mean = np.asarray(DA_V2_IMAGENET_MEAN, dtype=np.float32)
    std = np.asarray(DA_V2_IMAGENET_STD, dtype=np.float32)
    normed = (normed - mean) / std
    # H×W×C → C×H×W → 1×C×H×W
    nchw = np.transpose(normed, (2, 0, 1))[None, ...]
    return np.ascontiguousarray(nchw, dtype=np.float32)


def _calibrate_to_meters(relative_depth, target_h: int, target_w: int):
    """Convert DA-V2 relative-depth output → metric depth in meters along Z.

    DA-V2 outputs a per-pixel value that is monotonically related to
    *inverse* depth (larger = closer to camera). We:
      1. Resize to the mp4 resolution (1920×1080).
      2. Per-frame min-max normalize to [0, 1].
      3. Invert (1 - x) so larger value = farther from camera.
      4. Linear-map to [NEAR, FAR] meters via MC render-distance heuristic.

    Output: float32 array of shape (target_h, target_w), units = meters.
    """
    import cv2  # type: ignore  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    # 1. Resize to mp4 native resolution (PRD §3.4 requires 1920×1080).
    d = cv2.resize(
        relative_depth.astype(np.float32),
        (target_w, target_h),
        interpolation=cv2.INTER_LINEAR,
    )

    # 2. Per-frame min-max normalize. We guard against constant-frame
    #    (e.g. completely black title screen) by detecting tiny range.
    d_min = float(np.min(d))
    d_max = float(np.max(d))
    rng = d_max - d_min
    if rng < 1e-6:
        # Degenerate input — return all-invalid (PRD requires 0 for invalid).
        return np.zeros((target_h, target_w), dtype=np.float32)
    norm = (d - d_min) / rng  # in [0, 1], larger = closer

    # 3. Invert so larger = farther.
    farness = 1.0 - norm  # in [0, 1], 0=near 1=far

    # 4. Linear-map to [NEAR, FAR] meters. Documented placeholder — will be
    #    replaced when mc-mod metric depth IPC ships in rc20.
    meters = MC_NEAR_PLANE_M + farness * (MC_FAR_PLANE_M - MC_NEAR_PLANE_M)
    return meters.astype(np.float32)


def _apply_invalid_mask(depth_m, target_h: int, target_w: int):
    """Set sky-likely + clipped pixels to exactly 0.0 per PRD §3.4."""
    import numpy as np  # noqa: PLC0415

    out = depth_m.copy()
    # Top fraction of rows → sky-likely heuristic.
    sky_rows = int(round(SKY_MASK_TOP_FRACTION * target_h))
    if sky_rows > 0:
        out[:sky_rows, :] = 0.0
    # Anything calibrated beyond the far plane is clipped → invalid.
    out[out >= MC_FAR_PLANE_M] = 0.0
    # Negative / NaN guard.
    out[~np.isfinite(out)] = 0.0
    out[out < 0.0] = 0.0
    return out


# ---------------------------------------------------------------------------
# EXR write — single-channel float32 named "Z" per PRD §3.4
# ---------------------------------------------------------------------------


def write_exr_z_channel(depth_m, target: Path) -> None:
    """Write a single-channel float32 EXR with channel name 'Z'.

    PRD §3.4 is strict: the channel name MUST be the single character "Z",
    not "R" / "Y" / "depth" / anything else. Using a generic RGB header would
    break ingest validation.
    """
    import Imath  # type: ignore  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415
    import OpenEXR  # type: ignore  # noqa: PLC0415

    if depth_m.dtype != np.float32:
        depth_m = depth_m.astype(np.float32)
    h, w = depth_m.shape
    header = OpenEXR.Header(w, h)
    # PIZ compression: wavelet-based, well-suited to smooth depth surfaces.
    # ZIP is the alternative; PIZ trades a little CPU for ~30% smaller files.
    header["compression"] = Imath.Compression(Imath.Compression.PIZ_COMPRESSION)
    header["channels"] = {
        "Z": Imath.Channel(Imath.PixelType(Imath.PixelType.FLOAT))
    }
    out = OpenEXR.OutputFile(str(target), header)
    try:
        out.writePixels({"Z": depth_m.tobytes()})
    finally:
        out.close()


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------


def already_populated(depth_dir: Path, expected_min: int) -> bool:
    """Idempotence: return True if depth_dir already has ≥ expected_min EXRs."""
    if not depth_dir.exists():
        return False
    return sum(1 for _ in depth_dir.glob("*.exr")) >= expected_min


def run(
    session_dir: Path,
    *,
    model_path: Optional[Path] = None,
    target_fps: float = PRD_TARGET_FPS,
    max_frames: int = PRD_EXPECTED_FRAMES,
    verbose: bool = False,
) -> int:
    """Run the full pipeline. Returns CLI exit code.

    0 = success, 1 = partial (some frame errors but kept), 2 = fatal init.
    """
    # Validate session inputs first — bail early with a clear path.
    if not session_dir.exists() or not session_dir.is_dir():
        log.error("session_dir not found or not a directory: %s", session_dir)
        return 2
    video_path = session_dir / "recording.mp4"
    if not video_path.exists():
        log.error("recording.mp4 not found at %s", video_path)
        return 2

    depth_dir = session_dir / "depth"
    if already_populated(depth_dir, max_frames):
        log.info(
            "depth/ already has ≥ %d .exr files — skipping (idempotent)",
            max_frames,
        )
        return 0

    # Probe deps + model BEFORE creating output dir, so a missing dep
    # leaves the session untouched.
    try:
        _probe_dependencies()
    except MissingDependency as exc:
        log.error("%s", exc)
        return 2

    try:
        resolved_model = resolve_model_path(model_path)
    except FileNotFoundError as exc:
        log.error("%s", exc)
        return 2

    log.info("model: %s", resolved_model)
    try:
        sess, provider = _new_inference_session(resolved_model)
    except RuntimeError as exc:
        log.error("%s", exc)
        return 2
    log.info("onnxruntime provider: %s", provider)
    if provider == "CPUExecutionProvider":
        log.warning(
            "running on CPU — expect ~2s/frame × %d = ~%d min total. "
            "Install onnxruntime-directml for GPU acceleration.",
            max_frames,
            int(2 * max_frames / 60),
        )

    depth_dir.mkdir(parents=True, exist_ok=True)
    input_name = sess.get_inputs()[0].name

    t0 = time.time()
    written = 0
    errors = 0
    last_log_t = t0

    try:
        for sample_idx, rgb in iter_sampled_frames(
            video_path, target_fps=target_fps, max_frames=max_frames
        ):
            try:
                nchw = _preprocess_rgb_to_nchw(rgb)
                outputs = sess.run(None, {input_name: nchw})
                # DA-V2 typically returns shape (1, 518, 518) or (1, 1, 518, 518).
                raw = outputs[0]
                import numpy as np  # noqa: PLC0415

                depth_lowres = np.squeeze(raw).astype(np.float32)
                if depth_lowres.ndim != 2:
                    raise RuntimeError(
                        f"model output rank {depth_lowres.ndim} != 2"
                    )
                depth_m = _calibrate_to_meters(depth_lowres, PRD_HEIGHT, PRD_WIDTH)
                depth_m = _apply_invalid_mask(depth_m, PRD_HEIGHT, PRD_WIDTH)
                target = depth_dir / f"{sample_idx:06d}.exr"
                write_exr_z_channel(depth_m, target)
                written += 1
            except Exception as exc:
                errors += 1
                log.error("frame %06d failed: %s", sample_idx, exc)
                # Continue — PRD §3.4 prefers partial output to nothing.
                continue

            # Verbose periodic progress (every 5s) so user sees something.
            now = time.time()
            if verbose and now - last_log_t > 5.0:
                rate = written / max(now - t0, 1e-3)
                log.info(
                    "  ... %d/%d frames written, %d errors, %.2f fps",
                    written, max_frames, errors, rate,
                )
                last_log_t = now
    except Exception as exc:
        # Bulk failure (mp4 unreadable mid-stream, etc.)
        log.error("video iteration aborted: %s", exc)
        # Keep whatever we already wrote.
        # Fall through to summary.

    dt = time.time() - t0
    log.info(
        "wrote %d/%d .exr files in %.1fs (%d errors)",
        written, max_frames, dt, errors,
    )

    if written == 0:
        return 1 if errors > 0 else 2
    return 1 if errors > 0 else 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate PRD §3.4 depth/*.exr files from recording.mp4",
    )
    parser.add_argument("session_dir", type=Path, help="recording session dir")
    parser.add_argument(
        "--model-path",
        type=Path,
        default=None,
        help="path to depth_anything_v2_small.onnx (overrides default search)",
    )
    parser.add_argument(
        "--target-fps",
        type=float,
        default=PRD_TARGET_FPS,
        help="sampling fps (default %(default)s — PRD §3.4)",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=PRD_EXPECTED_FRAMES,
        help="max EXR files to write (default %(default)d — 5 min × 6 fps)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="enable periodic progress logging",
    )
    args = parser.parse_args(argv)

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    return run(
        args.session_dir,
        model_path=args.model_path,
        target_fps=args.target_fps,
        max_frames=args.max_frames,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    sys.exit(main())
