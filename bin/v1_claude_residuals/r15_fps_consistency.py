"""R15 — Frame-rate vs ffprobe video.mp4 consistency.

Per IL10 (Producer-Artifact Honesty): when the cross-modal artifact
(video.mp4) is absent / unreadable / schema-invalid, the residual MUST
ABSTAIN — never silent-PASS. ABSTAIN is encoded as
``passed=False, note="ABSTAIN:<reason>"`` to match the V₁ convention
(see SPEC_R13_MULTIMODAL.md § 1, § 5.4).

Threshold: |action_camera.fps − probe.avg_frame_rate| < 0.5 fps.
NTSC tolerance: declared 30 vs probed 30000/1001 (≈29.97) → diff 0.03 → PASS.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess

from .residuals import ResidualResult


_FFPROBE_CMD = (
    "ffprobe", "-v", "error", "-select_streams", "v:0",
    "-show_entries", "stream=avg_frame_rate",
    "-of", "json",
)


def _abstain(reason: str) -> ResidualResult:
    return ResidualResult("R15", False, math.nan, 0.5, f"ABSTAIN:{reason}")


def _parse_rational(rat: str) -> float:
    """Parse ffprobe rational ``"30000/1001"`` → float; ``den==0`` → 0.0."""
    if "/" in rat:
        num, den = rat.split("/", 1)
        d = float(den)
        return float(num) / d if d != 0 else 0.0
    return float(rat)


def r15_fps_consistency(rec: dict, video_path: str | None = None) -> ResidualResult:
    """Verify ``rec['fps']`` matches ffprobe ``avg_frame_rate`` of video.

    ABSTAIN when:
      * ``video_path`` is None (caller didn't bind a video)
      * video file missing
      * ffprobe binary unavailable
      * ffprobe exits non-zero / emits malformed JSON
      * ``avg_frame_rate`` denominator is 0 (corrupt stream)
      * ``rec['fps']`` is missing / non-numeric
    """
    declared = rec.get("fps")
    if declared is None:
        return _abstain("no_fps_in_action")
    try:
        declared_f = float(declared)
    except (TypeError, ValueError):
        return _abstain("no_fps_in_action")

    if video_path is None:
        return _abstain("no_video_file")
    if not os.path.isfile(video_path):
        return _abstain("no_video_file")
    if shutil.which("ffprobe") is None:
        return _abstain("ffprobe_unavailable")

    try:
        proc = subprocess.run(
            [*_FFPROBE_CMD, video_path],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return _abstain(f"ffprobe_failed:{type(e).__name__}")
    if proc.returncode != 0:
        return _abstain("ffprobe_failed")

    try:
        data = json.loads(proc.stdout)
        rate_str = data["streams"][0]["avg_frame_rate"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        return _abstain("ffprobe_failed")

    probed = _parse_rational(rate_str)
    if probed == 0.0:
        return _abstain("zero_fps_video")

    diff = abs(declared_f - probed)
    threshold = 0.5
    note = (
        ""
        if diff < threshold
        else f"declared={declared_f:.3f} probed={probed:.3f} diff={diff:.3f}"
    )
    return ResidualResult("R15", diff < threshold, diff, threshold, note)
