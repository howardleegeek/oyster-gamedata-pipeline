"""R18 — Session-ID manifest binding (Frankenstein-splice defense).

Closes red-team attack B-05: an attacker concatenates clip A's
``action_camera.json`` with clip B's ``video.mp4`` and clip C's
``depth/`` directory. Each artifact passes its own residual but the
recombined dataset is fraudulent. R18 binds every artifact to a single
``session_id`` UUID written by the recorder at start, then verifies the
ID matches in the manifest *and* in every per-frame action_camera record.

Spec source: docs/RED_TEAM_TAXONOMY.md § R18.

Design notes
------------
- Stdlib only: ``json`` for the manifest, ``pathlib.Path`` for IO. No
  external deps so the residual stays callable in headless harnesses.
- Strict equality: a single mismatched frame trips ``residual=1.0``
  with ``passed=False``. We do NOT tolerate partial matches — even
  one foreign frame could be the splice boundary.
- ABSTAIN per IL10 ("ambiguous evidence → don't vote") for the four
  legitimate "I don't know" cases:
    1. ``manifest_path`` is None / file absent
    2. manifest JSON has no ``session_id`` key
    3. manifest ``session_id`` is the empty string
    4. ``rec`` (a frame) has no ``session_id`` field at all
       (legacy data predating this residual — escalate via spec, not
       by voting FAIL on every old clip).
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .residuals import ResidualResult


def r18_session_manifest(
    rec: dict,
    neighbor: dict | None = None,
    manifest_path: str | Path | None = None,
) -> ResidualResult:
    """Verify ``rec['session_id']`` matches ``manifest['session_id']``.

    Parameters
    ----------
    rec
        Single frame record from ``action_camera.json``. Must carry
        ``session_id`` once the recorder is at lite-v0.21.0+.
    neighbor
        Unused; kept in signature for orchestrator uniformity (other
        kinematic residuals take a neighbor frame).
    manifest_path
        Path to ``session_manifest.json`` written by the recorder.
        None or missing → ABSTAIN.

    Returns
    -------
    ResidualResult
        ``passed`` True iff session_id matches. ABSTAIN cases set
        ``residual=NaN`` and prefix ``note`` with ``"ABSTAIN:"`` so
        the BFT orchestrator can distinguish "I don't know" from
        "I vote FAIL".
    """
    threshold = 0.0  # strict equality — any drift is malicious

    # IL10 ABSTAIN gate 1: no manifest path provided.
    if manifest_path is None:
        return ResidualResult("R18", False, math.nan, threshold, "ABSTAIN:no_manifest_path")
    mpath = Path(manifest_path)
    if not mpath.is_file():
        return ResidualResult("R18", False, math.nan, threshold, "ABSTAIN:manifest_not_found")

    # Parse manifest. JSON errors surface as ABSTAIN — corrupt file is
    # not the frame's fault, so we don't vote FAIL on rec.
    try:
        manifest: dict[str, Any] = json.loads(mpath.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return ResidualResult("R18", False, math.nan, threshold, f"ABSTAIN:manifest_unreadable:{e}")

    # IL10 ABSTAIN gate 2: manifest lacks session_id.
    if "session_id" not in manifest:
        return ResidualResult("R18", False, math.nan, threshold, "ABSTAIN:manifest_no_session_id")
    manifest_sid = manifest["session_id"]

    # IL10 ABSTAIN gate 3: empty / falsy session_id (uninitialized).
    if not isinstance(manifest_sid, str) or manifest_sid == "":
        return ResidualResult(
            "R18", False, math.nan, threshold, "ABSTAIN:manifest_session_id_empty"
        )

    # IL10 ABSTAIN gate 4: legacy frame with no session_id at all.
    if "session_id" not in rec:
        return ResidualResult("R18", False, math.nan, threshold, "ABSTAIN:frame_no_session_id")

    # Strict equality check — splice attack lands here.
    frame_sid = rec["session_id"]
    if frame_sid != manifest_sid:
        return ResidualResult(
            "R18",
            False,
            1.0,
            threshold,
            f"session_id mismatch: frame={frame_sid!r} manifest={manifest_sid!r}",
        )
    return ResidualResult("R18", True, 0.0, threshold)
