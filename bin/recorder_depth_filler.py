#!/usr/bin/env python3
"""
bin/recorder_depth_filler.py — Per-clip depth-EXR generator.

Wraps :mod:`bin.real_depth_filler` (and :mod:`bin.depth_anything_smoke` for a
fast smoke pass) into a single, recorder-friendly pipeline:

  1.  Take a clip directory containing ``video.mp4``.
  2.  Use ``ffmpeg`` to extract RGB frames at exactly **6 fps** (PRD frame
      cadence) into a temporary ``rgb/`` directory.
  3.  Call :func:`bin.real_depth_filler.infer_batch` on those frames to
      produce one DepthAnything-V2 monocular OpenEXR per frame
      (``frame_NNNNNN.exr``) under ``<clip-dir>/depth/``.
  4.  Verify exactly **1800** files were written (300 s × 6 fps = PRD spec
      buyer-grade clip length); log a warning if fewer.

This is a NEW FILE that ``bin/recorder_consumer_lite.py`` (or its Rust
successor) can spawn as a sub-process *after* ffmpeg finishes finalising
``video.mp4``.  ``recorder_consumer_lite.py`` itself is **not edited**.

Standalone CLI:

    python3 bin/recorder_depth_filler.py --clip-dir <dir> [--fps 6]

Spec: G261 (W31 wave). PP0 priority. ~180 lines.
"""
from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

# Allow running this file directly (`python3 bin/recorder_depth_filler.py`).
_BIN_DIR = Path(__file__).resolve().parent
if str(_BIN_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR.parent))

#: PRD per-clip frame cadence — 6 fps over a 300-second buyer-grade clip
#: equals exactly 1800 frames.  Recorder may produce shorter test clips, so
#: this is a *target*, not a hard requirement.
DEFAULT_FPS: int = 6
DEFAULT_CLIP_SECONDS: int = 300
EXPECTED_FRAME_COUNT: int = DEFAULT_FPS * DEFAULT_CLIP_SECONDS  # 1800

LOG = logging.getLogger("recorder_depth_filler")


def find_clip_video(clip_dir: Path) -> Path:
    """Return the clip's primary video file.

    Args:
        clip_dir: Directory containing ``video.mp4``.

    Returns:
        Absolute path to the video.

    Raises:
        FileNotFoundError: No ``.mp4`` present.
    """
    canonical = clip_dir / "video.mp4"
    if canonical.is_file():
        return canonical.resolve()
    candidates = sorted(p for p in clip_dir.glob("*.mp4") if p.is_file())
    if not candidates:
        raise FileNotFoundError(f"no video.mp4 in {clip_dir}")
    return candidates[0].resolve()


def extract_frames(
    video_path: Path,
    out_rgb_dir: Path,
    fps: int = DEFAULT_FPS,
    ffmpeg_bin: str = "ffmpeg",
) -> int:
    """Extract RGB frames from ``video_path`` at ``fps`` into ``out_rgb_dir``.

    Output filename pattern: ``frame_%06d.png`` so that
    :func:`bin.real_depth_filler.infer_batch` will produce
    ``frame_NNNNNN.exr``.

    Args:
        video_path: Source MP4.
        out_rgb_dir: Destination directory for PNGs (created if missing).
        fps: Target sampling rate.
        ffmpeg_bin: ffmpeg binary path override.

    Returns:
        Number of PNG files written.

    Raises:
        FileNotFoundError: ffmpeg missing.
        RuntimeError: ffmpeg returned non-zero or wrote zero frames.
    """
    if shutil.which(ffmpeg_bin) is None:
        raise FileNotFoundError(f"ffmpeg '{ffmpeg_bin}' not on PATH")

    out_rgb_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(out_rgb_dir / "frame_%06d.png")
    cmd = [
        ffmpeg_bin,
        "-y",
        "-i", str(video_path),
        "-vf", f"fps={fps}",
        "-vsync", "vfr",
        pattern,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed (rc={proc.returncode}): {proc.stderr[-512:]}"
        )
    pngs = sorted(out_rgb_dir.glob("frame_*.png"))
    if not pngs:
        raise RuntimeError(f"ffmpeg produced no frames in {out_rgb_dir}")
    return len(pngs)


def run_depth_inference(
    rgb_dir: Path,
    out_depth_dir: Path,
    model_id: str = "depth-anything/Depth-Anything-V2-Small-hf",
    near_m: float = 0.5,
    far_m: float = 30.0,
    batch_size: int = 4,
    device: Optional[str] = None,
) -> int:
    """Run DA-V2 inference on every PNG in ``rgb_dir``.

    Delegates to :func:`bin.real_depth_filler.infer_batch`.

    Args:
        rgb_dir: Directory of frame_NNNNNN.png files.
        out_depth_dir: Destination directory for ``frame_NNNNNN.exr``.
        model_id: HuggingFace pipeline model id.
        near_m: Near-clipping plane (metres).
        far_m: Far-clipping plane (metres).
        batch_size: Per-batch image count (auto-shrinks on OOM).
        device: ``"cuda"`` / ``"mps"`` / ``"cpu"`` / None for auto.

    Returns:
        Number of EXR files actually written.
    """
    # Lazy import: keeps `--help` and unit tests fast and avoids importing
    # torch in the recorder process unless inference is actually requested.
    from bin.real_depth_filler import infer_batch  # type: ignore

    out_depth_dir.mkdir(parents=True, exist_ok=True)
    return int(
        infer_batch(
            rgb_dir=str(rgb_dir),
            out_dir=str(out_depth_dir),
            model_id=model_id,
            near_m=near_m,
            far_m=far_m,
            batch_size=batch_size,
            device=device,
        )
    )


def fill_clip_depth(
    clip_dir: Path,
    fps: int = DEFAULT_FPS,
    expected_frames: int = EXPECTED_FRAME_COUNT,
    model_id: str = "depth-anything/Depth-Anything-V2-Small-hf",
    near_m: float = 0.5,
    far_m: float = 30.0,
    batch_size: int = 4,
    device: Optional[str] = None,
    ffmpeg_bin: str = "ffmpeg",
    keep_rgb: bool = False,
) -> Path:
    """End-to-end: extract frames → run DA-V2 → write ``<clip-dir>/depth/``.

    Args:
        clip_dir: Directory with ``video.mp4``.
        fps: Frame extraction rate (default 6).
        expected_frames: Expected EXR count (warn if fewer).
        model_id: DA-V2 HuggingFace id.
        near_m: Near-clipping plane.
        far_m: Far-clipping plane.
        batch_size: Inference batch size.
        device: Device override.
        ffmpeg_bin: ffmpeg binary override.
        keep_rgb: If True, keep ``rgb/`` for QA; otherwise discard.

    Returns:
        Path to ``<clip-dir>/depth/``.
    """
    clip_dir = Path(clip_dir).resolve()
    if not clip_dir.is_dir():
        raise NotADirectoryError(clip_dir)
    video = find_clip_video(clip_dir)
    depth_dir = clip_dir / "depth"

    if keep_rgb:
        rgb_dir = clip_dir / "rgb"
        rgb_dir.mkdir(exist_ok=True)
        n_frames = extract_frames(video, rgb_dir, fps=fps, ffmpeg_bin=ffmpeg_bin)
        n_exr = run_depth_inference(
            rgb_dir, depth_dir, model_id, near_m, far_m, batch_size, device
        )
    else:
        with tempfile.TemporaryDirectory(prefix="oyster_depth_rgb_") as td:
            rgb_dir = Path(td)
            n_frames = extract_frames(video, rgb_dir, fps=fps, ffmpeg_bin=ffmpeg_bin)
            n_exr = run_depth_inference(
                rgb_dir, depth_dir, model_id, near_m, far_m, batch_size, device
            )

    LOG.info(
        "clip=%s frames=%d exr=%d expected=%d",
        clip_dir.name, n_frames, n_exr, expected_frames,
    )
    if n_exr < expected_frames:
        LOG.warning(
            "wrote %d/%d EXR frames — clip may be shorter than %ds at %d fps",
            n_exr, expected_frames, DEFAULT_CLIP_SECONDS, fps,
        )
    return depth_dir


def main(argv: Optional[list] = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Generate per-clip DepthAnything-V2 EXR depth maps."
    )
    parser.add_argument("--clip-dir", required=True, help="Directory containing video.mp4")
    parser.add_argument(
        "--fps", type=int, default=DEFAULT_FPS, help="Frame extraction fps (default 6)"
    )
    parser.add_argument("--expected-frames", type=int, default=EXPECTED_FRAME_COUNT,
                        help="Expected EXR count (warn-only)")
    parser.add_argument("--model-id", default="depth-anything/Depth-Anything-V2-Small-hf")
    parser.add_argument("--near-m", type=float, default=0.5)
    parser.add_argument("--far-m", type=float, default=30.0)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--device", default=None, help="cuda/mps/cpu (auto if unset)")
    parser.add_argument("--ffmpeg-bin", default="ffmpeg")
    parser.add_argument("--keep-rgb", action="store_true", help="Retain extracted RGB frames")
    parser.add_argument("--log-level", default="INFO", help="DEBUG/INFO/WARNING/ERROR")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    try:
        out = fill_clip_depth(
            clip_dir=Path(args.clip_dir),
            fps=args.fps,
            expected_frames=args.expected_frames,
            model_id=args.model_id,
            near_m=args.near_m,
            far_m=args.far_m,
            batch_size=args.batch_size,
            device=args.device,
            ffmpeg_bin=args.ffmpeg_bin,
            keep_rgb=args.keep_rgb,
        )
    except (FileNotFoundError, NotADirectoryError, RuntimeError) as exc:
        print(f"[recorder_depth_filler] ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"[recorder_depth_filler] depth dir: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
