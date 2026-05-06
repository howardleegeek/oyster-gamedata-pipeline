"""V₁ R23 — video codec/format residual (closes D-05 transcode attack).

D-05 transcodes video.mp4 with H.264 instead of PRD-required H.265, or
alters resolution. R15 only checks fps. R23 enforces PRD literal:
codec_name == "hevc", width == 1920, height == 1080.

Per IL10: video_path None / file missing / ffprobe missing / ffprobe
failure → ABSTAIN (passed=False, residual=NaN, note="ABSTAIN:<reason>").

Threshold: 0 (strict). residual = count of mismatched fields.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from pathlib import Path

from .residuals import ResidualResult


_FFPROBE_CMD = ("ffprobe", "-v", "error", "-show_streams", "-of", "json")
_EXPECTED_CODEC = "hevc"
_EXPECTED_WIDTH = 1920
_EXPECTED_HEIGHT = 1080


def _abstain(reason: str) -> ResidualResult:
    return ResidualResult("R23", False, math.nan, 0.0, f"ABSTAIN:{reason}")


def r23_video_codec(
    rec: dict,
    neighbor: dict | None = None,
    video_path: str | Path | None = None,
) -> ResidualResult:
    """Verify video.mp4 is H.265 / 1920×1080 per PRD.

    ABSTAIN when:
      * ``video_path`` is None (caller didn't bind a video)
      * video file missing
      * ffprobe binary unavailable
      * ffprobe exits non-zero / emits malformed JSON
      * no video stream in ffprobe output
    """
    if video_path is None:
        return _abstain("no_video_file")
    path_str = str(video_path)
    if not os.path.isfile(path_str):
        return _abstain("no_video_file")
    if shutil.which("ffprobe") is None:
        return _abstain("ffprobe_unavailable")

    try:
        proc = subprocess.run(
            [*_FFPROBE_CMD, path_str],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return _abstain(f"ffprobe_failed:{type(e).__name__}")
    if proc.returncode != 0:
        return _abstain("ffprobe_failed")

    try:
        data = json.loads(proc.stdout)
        streams = data.get("streams", [])
        video_stream = next(
            (s for s in streams if s.get("codec_type") == "video"), None,
        )
        if video_stream is None and streams:
            video_stream = streams[0]
    except (json.JSONDecodeError, TypeError):
        return _abstain("ffprobe_failed")

    if not video_stream:
        return _abstain("no_video_stream")

    mismatches: list[str] = []
    codec = video_stream.get("codec_name")
    width = video_stream.get("width")
    height = video_stream.get("height")
    if codec != _EXPECTED_CODEC:
        mismatches.append(f"codec={codec!r}!={_EXPECTED_CODEC!r}")
    if width != _EXPECTED_WIDTH:
        mismatches.append(f"width={width}!={_EXPECTED_WIDTH}")
    if height != _EXPECTED_HEIGHT:
        mismatches.append(f"height={height}!={_EXPECTED_HEIGHT}")

    residual = float(len(mismatches))
    if residual == 0.0:
        return ResidualResult("R23", True, 0.0, 0.0, "hevc 1920x1080")
    return ResidualResult("R23", False, residual, 0.0, "; ".join(mismatches))
