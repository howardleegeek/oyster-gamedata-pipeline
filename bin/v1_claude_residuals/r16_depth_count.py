"""R16 — Depth-Frame-Count vs Video-Duration Consistency.

Cross-modal residual checking that the number of depth/*.exr files matches
the expected count derived from the video duration assuming uniform 6 fps
depth sampling (PRD: "每秒均匀抽取 6 张").

Spec source: docs/SPEC_R13_MULTIMODAL.md § R16.

Design notes
------------
- Stdlib only: ``pathlib.Path`` for file enumeration, ``math.ceil`` for
  expected-count arithmetic. No ffprobe call here — caller passes the
  already-probed ``video_duration_sec``.
- Threshold: ±2 frames (end-of-stream slack — depth thread may emit one
  fewer or one extra around recording stop; ±3 would absorb a real bug).
- ABSTAIN per IL10 (Iron Law 10: ambiguous evidence → don't vote): we
  return ``passed=False`` with note prefix ``"ABSTAIN:"`` when we cannot
  attribute fault — missing depth_dir, missing/non-positive duration.
"""

from __future__ import annotations

import math
from pathlib import Path

from .residuals import ResidualResult


def r16_depth_count(
    rec: dict,
    depth_dir: str | None = None,
    video_duration_sec: float | None = None,
) -> ResidualResult:
    """Count of ``depth/*.exr`` files vs ``ceil(duration × 6 fps)``.

    Parameters
    ----------
    rec
        Frame record (kept in signature for orchestrator uniformity; R16
        is dataset-level so ``rec`` is not consulted directly).
    depth_dir
        Directory containing ``depth_*.exr`` files. None → ABSTAIN.
    video_duration_sec
        Probed video duration. None or ≤ 0 → ABSTAIN (sentinel for
        "ffprobe failed / no duration metadata").

    Returns
    -------
    ResidualResult
        ``passed`` is True iff ``|actual − expected| ≤ 2``. ABSTAIN cases
        return ``passed=False`` with a ``note`` starting ``"ABSTAIN:"``
        so the BFT orchestrator can distinguish "I don't know" from
        "I vote FAIL".
    """
    threshold = 2.0

    # IL10 ABSTAIN gates ---------------------------------------------------
    if depth_dir is None:
        return ResidualResult("R16", False, math.inf, threshold, "ABSTAIN:no_depth_dir")
    dir_path = Path(depth_dir)
    if not dir_path.is_dir():
        return ResidualResult("R16", False, math.inf, threshold, "ABSTAIN:no_depth_dir")
    if video_duration_sec is None or video_duration_sec <= 0:
        return ResidualResult("R16", False, math.inf, threshold, "ABSTAIN:no_video_duration")

    # Count and arithmetic -------------------------------------------------
    actual = sum(1 for _ in dir_path.glob("*.exr"))
    expected = math.ceil(float(video_duration_sec) * 6.0)
    diff = float(abs(actual - expected))

    passed = diff <= threshold
    note = "" if passed else f"expected={expected} actual={actual} diff={int(diff)}"
    return ResidualResult("R16", passed, diff, threshold, note)
