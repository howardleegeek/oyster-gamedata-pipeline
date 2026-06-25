"""R22 — Depth-content SHA-256 manifest binding (D-04 defense).

Closes red-team attack D-04 (Bucket D, CRITICAL): an attacker renames
or shuffles ``depth/*.exr`` files. R16 only counts files via
``Path.glob`` so the count stays correct and the count-only residual
votes PASS. R22 binds each file to its content-hash at recording time
and re-verifies on the consumer side.

Spec source: docs/RED_TEAM_TAXONOMY.md § D-04.

Design notes
------------
- Stdlib only: ``hashlib.sha256`` over ``mmap``-friendly chunked reads,
  ``json`` for the manifest, ``pathlib.Path`` for IO. No openpyxl /
  numpy / OpenEXR — so the residual stays callable in the lite
  consumer harness even when the heavy depth deps are absent.
- Strict equality on every manifest entry. A single mismatched or
  missing file trips ``residual = mismatched + missing`` with
  ``passed = False``. The manifest is authoritative — extra files
  that the manifest does not list are tolerated (an attacker can't
  *add* trustable content without the recorder co-signing it).
- ABSTAIN per IL10 ("ambiguous evidence → don't vote") for the four
  legitimate "I don't know" cases:
    1. ``depth_dir`` is None or directory absent
    2. ``manifest_path`` is None or file absent
    3. manifest JSON is unreadable / malformed
    4. manifest is not a flat ``{filename: sha256_hex}`` mapping
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from .residuals import ResidualResult

_CHUNK = 1 << 20  # 1 MiB — keeps a 1080p EXR (~8 MB) at 8 reads.


def _sha256_file(path: Path) -> str:
    """Stream-hash a file in 1 MiB chunks; returns lowercase hex digest."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def r22_depth_hash(
    rec: dict,
    neighbor: dict | None = None,
    depth_dir: str | Path | None = None,
    manifest_path: str | Path | None = None,
) -> ResidualResult:
    """Verify every file listed in ``depth_manifest.json`` matches its
    recorded SHA-256 in ``depth_dir``.

    Parameters
    ----------
    rec
        Frame record (kept in signature for orchestrator uniformity;
        R22 is dataset-level so ``rec`` is not consulted directly).
    neighbor
        Unused; kept in signature for orchestrator uniformity.
    depth_dir
        Directory holding the ``*.exr`` files.
        None / missing → ABSTAIN.
    manifest_path
        Path to ``depth_manifest.json`` (flat ``{filename: sha256_hex}``
        mapping written by the producer).
        None / missing / malformed → ABSTAIN.

    Returns
    -------
    ResidualResult
        ``passed`` True iff every manifest entry hashes correctly.
        Residual = (mismatched + missing). ABSTAIN cases set
        ``residual = NaN`` and prefix ``note`` with ``"ABSTAIN:"``.
    """
    threshold = 0.0  # strict — any rename / swap / corruption is malicious

    # IL10 ABSTAIN gate 1: no depth_dir.
    if depth_dir is None:
        return ResidualResult("R22", False, math.nan, threshold, "ABSTAIN:no_depth_dir")
    dpath = Path(depth_dir)
    if not dpath.is_dir():
        return ResidualResult("R22", False, math.nan, threshold, "ABSTAIN:no_depth_dir")

    # IL10 ABSTAIN gate 2: no manifest_path.
    if manifest_path is None:
        return ResidualResult("R22", False, math.nan, threshold, "ABSTAIN:no_manifest_path")
    mpath = Path(manifest_path)
    if not mpath.is_file():
        return ResidualResult("R22", False, math.nan, threshold, "ABSTAIN:manifest_not_found")

    # IL10 ABSTAIN gate 3: corrupt JSON.
    try:
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return ResidualResult("R22", False, math.nan, threshold, f"ABSTAIN:manifest_unreadable:{e}")

    # IL10 ABSTAIN gate 4: wrong shape (must be {str: str}).
    if not isinstance(manifest, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in manifest.items()
    ):
        return ResidualResult("R22", False, math.nan, threshold, "ABSTAIN:manifest_bad_shape")

    # Verify every manifest entry. Manifest is authoritative — extra
    # unlisted files in depth/ are tolerated (only the recorder can sign
    # new entries; an attacker adding files can't forge a hash).
    mismatched = 0
    missing = 0
    for filename, expected_sha in manifest.items():
        fpath = dpath / filename
        if not fpath.is_file():
            missing += 1
            continue
        actual_sha = _sha256_file(fpath)
        if actual_sha.lower() != expected_sha.lower():
            mismatched += 1

    residual = float(mismatched + missing)
    if residual == 0.0:
        return ResidualResult("R22", True, 0.0, threshold)
    return ResidualResult(
        "R22",
        False,
        residual,
        threshold,
        f"mismatched={mismatched} missing={missing} of {len(manifest)} listed",
    )
