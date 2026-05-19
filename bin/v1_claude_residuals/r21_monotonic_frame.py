"""V₁ R21 — monotonic frame-index residual (closes D-02 reorder attack).

The D-02 adversarial attack reorders or duplicates the ``frame`` field in
action_camera.json so downstream consumers replay events out of sequence.
R09/R13 only check semantic content of a single record — they cannot detect
that frame 5 appears before frame 4. R21 enforces the global ordering
invariant: for every adjacent pair (rec, neighbor) the producer hands us,
``rec.frame`` must be strictly less than ``neighbor.frame``.

Per IL10 (Producer-Artifact Honesty): when the ``frame`` field is missing
or non-int on either side, ABSTAIN — never silent-PASS. ABSTAIN is encoded
as ``passed=False, residual=NaN, note="ABSTAIN:<reason>"``.

Threshold: 0 (strict). residual = max(0, rec.frame − neighbor.frame + 1)
counts how many positions out of order; 0 means in-order, 1 means duplicate,
≥2 means a true reorder.
"""
from __future__ import annotations

import math

from .residuals import ResidualResult


def r21_monotonic_frame(
    rec: dict,
    neighbor: dict | None = None,
) -> ResidualResult:
    """V₁ R21: rec.frame < neighbor.frame (strict monotonic increase).

    Args:
        rec: action_camera record at position N (must contain int ``frame``)
        neighbor: action_camera record at position N+1, or None for the
            last frame in the stream (PASS — nothing to compare against).

    Returns:
        ResidualResult with passed=True iff neighbor is None OR
        rec["frame"] < neighbor["frame"]. Duplicates and reorders FAIL.

    ABSTAIN encoding: passed=False, residual=NaN,
    note="ABSTAIN:frame_index_missing".
    """
    threshold = 0.0
    if neighbor is None:
        return ResidualResult("R21", True, 0.0, threshold, "last_frame_no_neighbor")

    rec_f = rec.get("frame")
    nbr_f = neighbor.get("frame")
    if not isinstance(rec_f, int) or not isinstance(nbr_f, int):
        return ResidualResult(
            "R21", False, math.nan, threshold, "ABSTAIN:frame_index_missing",
        )

    residual = float(max(0, rec_f - nbr_f + 1))
    if residual == 0.0:
        return ResidualResult("R21", True, 0.0, threshold, f"{rec_f} < {nbr_f}")
    return ResidualResult(
        "R21", False, residual, threshold,
        f"out_of_order: rec.frame={rec_f} >= neighbor.frame={nbr_f}",
    )
