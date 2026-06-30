#!/usr/bin/env python3
"""stamp_real_metadata.py — D15: stamp 'oyster-real-screen-capture' metadata.

Adds an FFmpeg metadata `comment=oyster-real-screen-capture` plus
`composer=oyster-recorder-vN.M.Z` to a video file so the D5 authenticity
validator can distinguish a real screen capture from a synthetic
testsrc — even when the screen content is static (no frame variance).

Howard 2026-05-07: closes the static-desktop false-positive in D5.
A real screen capture during a quiet desktop session is real data; a
testsrc placeholder is fake. Without this stamp, D5 cannot tell them
apart by content alone.

Usage:
    bin/stamp_real_metadata.py video.mp4 [--recorder-version lite-v0.24.0]

The file is rewritten in place via ffmpeg `-c copy -metadata`. No re-encode.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


REAL_COMMENT_TAG = "oyster-real-screen-capture"


def stamp_video(video_path: Path, *, recorder_version: str = "lite-v0.24.0") -> dict:
    """Stamp metadata into the video file. Rewrites in place atomically.

    Args:
        video_path: target video. Must exist + be a valid mp4 / mkv.
        recorder_version: tag for the `composer` metadata field.

    Returns:
        dict with keys: input, size_bytes_before, size_bytes_after,
        ffmpeg_returncode.

    Raises:
        FileNotFoundError: input missing.
        RuntimeError: ffmpeg fails.
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(video_path)

    size_before = video_path.stat().st_size

    # write to a temp file in same dir, then rename atomically
    with tempfile.NamedTemporaryFile(
        suffix=video_path.suffix,
        dir=str(video_path.parent),
        delete=False,
    ) as tf:
        tmp_out = Path(tf.name)

    try:
        cmd = [
            "ffmpeg",
            "-y",
            "-i", str(video_path),
            "-c", "copy",
            "-metadata", f"comment={REAL_COMMENT_TAG}",
            "-metadata", f"composer=oyster-recorder-{recorder_version}",
            str(tmp_out),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if res.returncode != 0:
            tmp_out.unlink(missing_ok=True)
            raise RuntimeError(f"ffmpeg failed (rc={res.returncode}): {res.stderr[-500:]}")

        # atomic replace
        tmp_out.replace(video_path)

        return {
            "input": str(video_path),
            "size_bytes_before": size_before,
            "size_bytes_after": video_path.stat().st_size,
            "ffmpeg_returncode": 0,
        }
    finally:
        if tmp_out.exists():
            tmp_out.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    """CLI entry-point for stamping video metadata.

    Args:
        argv: Command-line arguments. If None, uses sys.argv.

    Returns:
        Exit code: 0 on success, 2 if video file not found,
        3 on other errors.
    """
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("video", type=Path)
    p.add_argument("--recorder-version", type=str, default="lite-v0.24.0")
    args = p.parse_args(argv)

    try:
        info = stamp_video(args.video, recorder_version=args.recorder_version)
    except FileNotFoundError as e:
        print(f"ERROR: input not found: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 3

    import json as _json
    print(_json.dumps(info, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
