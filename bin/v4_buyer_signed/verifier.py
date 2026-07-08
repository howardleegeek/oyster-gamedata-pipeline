"""V₄ buyer-signed reference verifier — implementation.

Per ``docs/SPEC_V4_BUYER_SIGNED_PROTOCOL.md``:

- ``v4_buyer_reference_diff(rec, neighbor, buyer_reference_path, video_path)``
- ABSTAIN per IL10/IL12 if reference is missing / unparseable / sig invalid
- Frames not in F1..F5 → PASS (out-of-scope, per § 4 step 3)
- Frames in F1..F5 → byte-compare canonicalized record against signed snapshot

Signing primitive: HMAC-SHA256 keyed off env ``BUYER_SHARED_SECRET``
(spec § 3.3 fallback; ed25519 deferred to v2 per open Q1).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# IL3 isolation: do NOT import from V₁/V₂/V₃ packages. Re-declare the
# minimal ResidualResult shape locally to keep V₄ free of cross-tier
# coupling (matches v3_physics_oracle's OracleResult pattern).


@dataclass(frozen=True)
class ResidualResult:
    """Mirror of bin.v1_claude_residuals.residuals.ResidualResult."""

    name: str
    passed: bool
    residual: float
    threshold: float
    note: str = ""


_NAME = "V4_buyer_reference_diff"
_REQUIRED_KEYS = {
    "schema_version",
    "dataset_id",
    "frame_indices",
    "snapshots",
    "video_frame_hashes",
    "signature",
}


def canonical_record(rec: dict[str, Any]) -> str:
    """Deterministic JSON of a frame record — keys sorted, no whitespace.

    Matches spec § 2.2: ``json.dumps(..., sort_keys=True, allow_nan=False,
    separators=(",", ":"))``. Producer must serialize byte-identically at
    verify time; any drift means producer nondeterminism (fix producer).
    """
    return json.dumps(rec, sort_keys=True, allow_nan=False, separators=(",", ":"))


def compute_signature(payload: dict[str, Any], shared_secret: str) -> str:
    """HMAC-SHA256 hex digest over canonical JSON of payload (sans signature)."""
    body = {k: v for k, v in payload.items() if k != "signature"}
    canon = json.dumps(body, sort_keys=True, allow_nan=False, separators=(",", ":"))
    mac = hmac.new(shared_secret.encode("utf-8"), canon.encode("utf-8"), hashlib.sha256)
    return mac.hexdigest()


def verify_signature(payload: dict[str, Any], shared_secret: str) -> bool:
    """Constant-time check of ``payload['signature']`` against recomputed HMAC."""
    sig = payload.get("signature")
    if not isinstance(sig, str) or not sig:
        return False
    expected = compute_signature(payload, shared_secret)
    return hmac.compare_digest(sig, expected)


def _abstain(reason: str) -> ResidualResult:
    """Build an ABSTAIN result. ``passed=False`` enforces IL12 strictly."""
    return ResidualResult(
        name=_NAME, passed=False, residual=-1.0, threshold=0.0, note=f"ABSTAIN:{reason}",
    )


def load_buyer_reference(path: str | Path) -> dict[str, Any] | None:
    """Read and JSON-parse a buyer_reference.json. Return None on error."""
    p = Path(path)
    if not p.exists() or not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        # Surface the failure (corrupt JSON, permission denied, race on
        # temp file, etc.) at DEBUG so silent-returning sites in
        # v4_buyer_reference_diff can still be diagnosed post-hoc.
        logger.debug(
            "load_buyer_reference: failed to read/parse %s: %s",
            p, exc,
        )
        return None


def v4_buyer_reference_diff(
    rec: dict[str, Any],
    neighbor: dict[str, Any] | None = None,
    buyer_reference_path: str | Path | None = None,
    video_path: str | Path | None = None,
) -> ResidualResult:
    """Verify ``rec`` matches its buyer-signed reference, if signed.

    See module docstring + spec § 4 for full per-frame logic.
    ``neighbor`` and ``video_path`` are accepted for ABI uniformity but
    only ``buyer_reference_path`` and ``rec['frame']`` drive this v1.
    """
    # --- IL10: artifact-missing ABSTAIN -------------------------------
    if buyer_reference_path is None:
        return _abstain("reference_missing")
    payload = load_buyer_reference(buyer_reference_path)
    if payload is None:
        return _abstain("reference_missing")

    # --- IL12: schema + signature preconditions -----------------------
    if not _REQUIRED_KEYS.issubset(payload.keys()):
        return _abstain("reference_schema_mismatch")

    secret = os.environ.get("BUYER_SHARED_SECRET", "")
    if not secret:
        return _abstain("signature_invalid")  # no key ⇒ cannot trust
    if not verify_signature(payload, secret):
        return _abstain("signature_invalid")

    # --- dataset_id binding (spec § 4.3, also catches wholesale swap) -
    rec_session = rec.get("session_id")
    if rec_session is not None and rec_session != payload["dataset_id"]:
        return ResidualResult(
            name=_NAME, passed=False, residual=1.0, threshold=0.0,
            note=f"FAIL:dataset_id_mismatch ref={payload['dataset_id']!r} rec={rec_session!r}",
        )

    # --- frame_idx scope check (spec § 4 step 3) ----------------------
    frame_idx = rec.get("frame", rec.get("frame_idx"))
    indices: list[int] = list(payload.get("frame_indices") or [])
    if frame_idx not in indices:
        return ResidualResult(
            name=_NAME, passed=True, residual=0.0, threshold=0.0,
            note="not_a_reference_frame",
        )

    # --- byte-compare against the signed snapshot ---------------------
    slot = indices.index(frame_idx)
    snapshots: list[dict[str, Any]] = list(payload.get("snapshots") or [])
    if slot >= len(snapshots):
        return _abstain("reference_schema_mismatch")
    expected_canon = canonical_record(snapshots[slot])
    actual_canon = canonical_record(rec)

    if expected_canon == actual_canon:
        return ResidualResult(
            name=_NAME, passed=True, residual=0.0, threshold=0.0,
            note=f"slot=F{slot+1} frame_idx={frame_idx} byte_match",
        )

    # Find first byte that differs for actionable debug detail.
    diff_offset = next(
        (i for i, (a, b) in enumerate(zip(expected_canon, actual_canon)) if a != b),
        min(len(expected_canon), len(actual_canon)),
    )
    return ResidualResult(
        name=_NAME, passed=False, residual=1.0, threshold=0.0,
        note=f"FAIL:byte_diff slot=F{slot+1} frame_idx={frame_idx} offset={diff_offset}",
    )
