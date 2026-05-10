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
from typing import Any, Callable, Optional

import imageio.v2 as iio
import numpy as np

_PIPELINE: Any = None  # cached HF pipeline

# rc9 (Howard 2026-05-09): callback frequency for progress UI updates.
# We call progress_callback(idx, total) every N frames so a 10800-frame run
# generates ~720 UI updates instead of 10800 (still feels real-time but
# avoids hammering the Tk event loop).
_PROGRESS_CALLBACK_EVERY_N_FRAMES = 15


def load_model(variant: str = "vits", device: str = "cpu"):
    """Load and cache the DepthAnything V2 model via HF transformers pipeline.

    Args:
        variant: "vits" | "vitb" | "vitl"
        device: "cpu", "cuda", or "dml" (DirectML for AMD/Intel/NVIDIA on Win)
    Returns:
        Cached HF pipeline.
    Raises:
        ValueError on unknown variant.
        RuntimeError if model load fails (network, weights mismatch).

    rc15.6 (Howard 2026-05-09 "我只有 amd 怎么办"): added "dml" device path.
    Strategy: HF transformers pipeline doesn't natively understand "dml",
    so we load with device="cpu" then move the model to torch_directml.
    device() afterward. This works because pipeline only uses `device` for
    initial placement; subsequent .to() moves the weights cleanly.
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

    # rc15.6: route DML through cpu-init + .to(dml_device).
    use_dml = (device == "dml")
    pipeline_device = "cpu" if use_dml else device

    try:
        _PIPELINE = pipeline(
            "depth-estimation",
            model=repo_map[variant],
            device=pipeline_device,
        )
    except Exception as e:
        raise RuntimeError(f"Failed to load DepthAnything V2 {variant}: {e}") from e

    if use_dml:
        try:
            import torch_directml  # type: ignore  # noqa: PLC0415
            # rc15.7-fix BUG R9 H3: explicitly use default_device() instead of
            # bare device() so dual-GPU systems with iGPU + dGPU pick the right
            # adapter (driver-recommended) instead of arbitrary enumeration
            # order. On 780M-only systems this is a no-op.
            try:
                dml_idx = torch_directml.default_device()
                dml_device = torch_directml.device(dml_idx)
            except Exception:
                # Older torch-directml may lack default_device(); fall back.
                dml_device = torch_directml.device()
            _PIPELINE.model.to(dml_device)
            _PIPELINE.device = dml_device  # transformers reads this for inputs
        except ImportError as e:
            raise RuntimeError(
                "device='dml' requested but torch_directml not installed. "
                "Install: pip install torch-directml"
            ) from e
        except Exception as e:
            raise RuntimeError(f"Failed to move model to DirectML: {e}") from e

    return _PIPELINE


def _write_exr(depth: np.ndarray, target: Path) -> None:
    """Write a single-channel float32 EXR with channel name 'Z'.

    Howard 2026-05-07: downsample 2× before write + use PIZ compression.
    Reason: DepthAnything V2 returns at video resolution (1280×720), which
    serializes to ~2.7 MB/frame even with default ZIP compression, pushing
    a 1801-frame tarball over GitHub's 2 GiB asset limit. Most ML depth
    networks downsample on ingest anyway, so 640×360 preserves all useful
    spatial info while bringing per-frame EXR to ~700 KB.

    Block-mean 2× downsampling is preferred over PIL bilinear here because
    we're outputting depth (a physical quantity), not a colour image — mean
    pooling preserves average distance correctly across each block.
    """
    import OpenEXR  # noqa: PLC0415
    import Imath  # noqa: PLC0415

    # 2× block-mean downsample. Trim to multiples-of-2 to keep .reshape exact.
    H, W = depth.shape
    new_H, new_W = H // 2, W // 2
    depth = depth[: new_H * 2, : new_W * 2].astype(np.float32)
    depth = depth.reshape(new_H, 2, new_W, 2).mean(axis=(1, 3)).astype(np.float32)

    h, w = depth.shape
    header = OpenEXR.Header(w, h)
    # PIZ_COMPRESSION uses Wavelet — better than ZIP for smooth float depth
    # surfaces. Combined with the 2× downsample, EXR drops from 2.7 MB to
    # ~400 KB per frame.
    header["compression"] = Imath.Compression(Imath.Compression.PIZ_COMPRESSION)
    header["channels"] = {"Z": Imath.Channel(Imath.PixelType(Imath.PixelType.FLOAT))}
    out = OpenEXR.OutputFile(str(target), header)
    out.writePixels({"Z": depth.tobytes()})
    out.close()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _video_total_frames(video_path: Path) -> int:
    """Best-effort frame count for a video file. Returns 0 on failure.

    rc9 (Howard 2026-05-09): used by the recorder UI so it can show
    `1240 / 10800 frames` instead of just `1240 / ?`. We probe the
    container metadata via imageio without decoding the stream;
    a missing/wrong count is non-fatal — the UI just shows `?`.
    """
    try:
        meta = iio.get_reader(str(video_path), format="FFMPEG").get_meta_data()
        # imageio reports either nframes (int) or duration*fps; either is fine.
        nframes = meta.get("nframes")
        if isinstance(nframes, int) and nframes > 0:
            return nframes
        duration = meta.get("duration")
        fps = meta.get("fps")
        if duration and fps:
            return int(round(float(duration) * float(fps)))
    except Exception as _e:
        # rc15.27 A-L2 (round 17): trace the silent fail so engineers
        # can debug missing-codec / corrupt-mp4 edge cases without
        # changing the behavior contract (still returns 0 on failure).
        try:
            import logging as _logging  # noqa: PLC0415
            _logging.getLogger(__name__).debug(
                "_video_total_frames: %s", _e
            )
        except Exception:
            pass
    return 0


def infer_depth_for_video(
    video_path: Path,
    output_dir: Path,
    *,
    model_variant: str = "vits",
    device: str = "cpu",
    progress_callback: Optional[Callable[[int, int], None]] = None,
    should_skip: Optional[Callable[[], bool]] = None,
) -> dict[int, str]:
    """Run DepthAnything V2 on every frame of video_path; write per-frame EXR.

    Args:
        video_path: input mp4 (or any imageio-readable video).
        output_dir: target dir for `frame_NNNNNN.exr` files.
        model_variant: vits | vitb | vitl (vits = ~25M params, fastest CPU)
        device: 'cpu' or 'cuda'
        progress_callback: rc9 — optional callable invoked every
            ``_PROGRESS_CALLBACK_EVERY_N_FRAMES`` frames as
            ``cb(frames_done, total_frames)``. ``total_frames`` is 0 when
            we can't probe the container. Used by the recorder GUI so the
            tester sees inference progress; safe to call from any thread
            since the GUI marshals back to Tk via ``self.after(0, ...)``.
            Callback exceptions are swallowed so a broken UI never breaks
            the inference run.
        should_skip: rc9 — optional zero-arg callable polled between
            frames. When it returns truthy the loop exits cleanly: the
            partial EXR files written so far are KEPT (caller is
            responsible for either packaging them as a partial dataset or
            removing the depth/ dir if a clean tarball is preferred). The
            returned manifest only includes frames written before the
            skip; this lets the recorder ship a tarball without depth/
            when the tester clicks "Skip depth maps" mid-inference.

    Returns:
        manifest: dict[frame_index → sha256 of EXR file bytes].

    Raises:
        FileNotFoundError: video_path missing.
        RuntimeError: any frame inference / write fails. The output_dir is
                      removed entirely (no partial outputs left behind).
                      NB: a clean ``should_skip`` return is NOT raised —
                      the call returns a (possibly partial) manifest.
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

    # rc9: probe the container so the UI can show a real denominator.
    total_frames = _video_total_frames(video_path)

    def _emit_progress(done: int, total: int) -> None:
        if progress_callback is None:
            return
        try:
            progress_callback(done, total)
        except Exception:
            # UI exceptions must never break inference. Swallow.
            pass

    # Initial 0/total so the UI flips straight from "loading model…" to
    # the progress bar without a blank gap.
    _emit_progress(0, total_frames)

    manifest: dict[int, str] = {}
    skipped = False
    try:
        try:
            reader = iio.get_reader(str(video_path), format="FFMPEG")
        except Exception as e:
            raise RuntimeError(f"failed to open video {video_path}: {e}") from e

        for frame_idx, frame in enumerate(reader):
            # rc9: cooperative cancellation. Checked BEFORE the heavy
            # inference call so a fast click on "Skip depth maps" doesn't
            # have to wait the full 1-3 sec/frame for the next iteration.
            if should_skip is not None:
                try:
                    if should_skip():
                        skipped = True
                        break
                except Exception:
                    # A misbehaving skip-check shouldn't crash inference.
                    pass

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
                # rc15.7-fix BUG R9 C1 (CRITICAL): explicit cleanup of DML
                # tensors. CPython refcount drops them on CPU but DML keeps
                # device-memory backing alive until non-deterministic GC.
                # Per-frame leak × 1800 frames OOMs 780M's 2GB UMA at ~100
                # frames. Explicit del + periodic gc.collect() fixes.
                del result, pt, depth, pil
            except Exception as e:
                raise RuntimeError(f"frame {frame_idx} inference/write failed: {e}") from e

            # rc9: progress every N frames + always on the last frame.
            done = frame_idx + 1
            if done % _PROGRESS_CALLBACK_EVERY_N_FRAMES == 0:
                _emit_progress(done, total_frames)
            # rc15.7-fix BUG R9 C1: GC every 50 frames as backstop for
            # accumulated tensor refs that escaped the per-frame del.
            if done % 50 == 0:
                import gc as _gc  # noqa: PLC0415
                _gc.collect()

        try:
            reader.close()
        except Exception as _ce:
            # rc15.27 A-L3 (round 17): trace silent reader.close fail.
            # Cleanup is best-effort but a corrupt mp4 raising on close
            # is useful diagnostic info.
            try:
                import logging as _logging  # noqa: PLC0415
                _logging.getLogger(__name__).debug(
                    "reader.close failed: %s", _ce
                )
            except Exception:
                pass

        # Final progress tick so the UI bar lands on 100% (or the partial
        # value at skip time) rather than the last N-aligned tick.
        _emit_progress(len(manifest), total_frames)

        if not manifest and not skipped:
            raise RuntimeError(f"no frames produced from {video_path}")

    except Exception:
        # Clean up partial output on any failure — IL10 (no partial fakes).
        # NB: a clean ``should_skip`` early-return is NOT an exception, so
        # partial EXR files are PRESERVED in that path (caller decides
        # whether to keep or drop them).
        if output_dir.exists():
            shutil.rmtree(output_dir, ignore_errors=True)
        raise

    return manifest
