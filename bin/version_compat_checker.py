#!/usr/bin/env python3
"""
G251 · Recorder ↔ Pipeline Version Compatibility Checker

Distinct from :mod:`bin.version_compatibility_check` (which checks the
**game** version, i.e. Minecraft).  This module checks the **recorder
client** version against the buyer pipeline's compatibility matrix
before a tarball is accepted into ingest / lint.

Flow:
    1. Reader extracts ``MANIFEST.json`` (or ``manifest.yaml``) from
       an uploaded tarball; ``recorder_version`` is a top-level field.
    2. :func:`check_recorder_compat` looks the version up in the matrix
       (``compat_matrix.json``) and returns a :class:`CompatResult`.
    3. The caller (Next.js API route, lint runner, ingest worker) uses
       ``result.accepted`` to decide whether to proceed, surfacing
       ``result.upgrade_url`` and ``result.reason`` in the rejection
       message so the tester knows exactly how to remediate.

Matrix shape (see ``bin/compat_matrix.json``):

    {
      "entries": {
        "v0.28.0-rc19.0.1": {
          "min_pipeline":  "0.1.0-rc8",
          "lint_version":  38,
          "deprecated":    false,
          "deprecation_reason": "...",
          "support_window_end": "2026-04-30"
        }
      }
    }

Iron-law: no "graceful unknown" path.  If the recorder version isn't
in the matrix (and no wildcard matches), the tarball is REJECTED with
an upgrade pointer rather than silently accepted.  Same goes for
deprecated entries past their ``support_window_end`` date.

CLI:
    # From a manifest file
    python -m bin.version_compat_checker --manifest path/to/MANIFEST.json

    # From an uploaded tarball
    python -m bin.version_compat_checker --tarball path/to/clip.tar.gz

    # Just check a version literal
    python -m bin.version_compat_checker --version v0.28.0-rc19.0.1

Exit codes:
    0 — accepted
    1 — rejected (incompatible)
    2 — input error (bad file, malformed manifest)
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import re
import sys
import tarfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

DEFAULT_MATRIX_PATH: Path = Path(__file__).resolve().parent / "compat_matrix.json"

DEFAULT_UPGRADE_URL: str = (
    "https://github.com/howardleegeek/oyster-gamedata-pipeline/releases/latest"
)

# Recorder version regex — same shape as the update-server proxy.
_RECORDER_VER_RE: re.Pattern[str] = re.compile(
    r"^v?\d+\.\d+\.\d+(?:-[A-Za-z0-9.\-]+)?$"
)

# Pipeline (this repo) version regex — accepts e.g. "0.1.0-rc8".
_PIPELINE_VER_RE: re.Pattern[str] = re.compile(
    r"^\d+\.\d+\.\d+(?:-[A-Za-z0-9.\-]+)?$"
)

MANIFEST_VERSION_KEYS: tuple[str, ...] = (
    "recorder_version",
    "recorderVersion",
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ManifestError(ValueError):
    """The manifest file is missing, malformed, or has no recorder_version."""


class MatrixError(RuntimeError):
    """The compatibility matrix file is missing or malformed."""


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class CompatResult:
    """Outcome of a compatibility check.

    Attributes:
        accepted: True when the recorder version is supported.
        recorder_version: the version extracted from the manifest.
        matched_entry: the matrix key that matched (None when no match).
        reason: human-readable explanation suitable for the rejection
            email / API response.
        upgrade_url: where the tester can grab a supported recorder.
        min_pipeline: matrix-declared minimum pipeline version (None
            when no match).
        lint_version: matrix-declared lint schema version (None when
            no match).
        deprecated: True when the matched entry is past its support
            window or explicitly marked.
    """

    accepted: bool
    recorder_version: Optional[str]
    matched_entry: Optional[str]
    reason: str
    upgrade_url: str
    min_pipeline: Optional[str]
    lint_version: Optional[int]
    deprecated: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Version parsing & wildcard matching
# ---------------------------------------------------------------------------


def _normalise(version: str) -> str:
    """Strip surrounding whitespace and a single leading ``v``."""
    s = version.strip()
    return s[1:] if s.lower().startswith("v") else s


def _split_pre(version: str) -> tuple[tuple[int, int, int], list[str]]:
    """Split into semver tuple + prerelease segments."""
    s = _normalise(version)
    base, _, pre = s.partition("-")
    parts = base.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"unparseable version: {version!r}")
    semver = (int(parts[0]), int(parts[1]), int(parts[2]))
    pre_parts = pre.split(".") if pre else []
    return semver, pre_parts


def is_pipeline_at_least(pipeline_ver: str, required: str) -> bool:
    """Return True when ``pipeline_ver`` >= ``required`` per (semver, pre)."""
    a_semver, a_pre = _split_pre(pipeline_ver)
    b_semver, b_pre = _split_pre(required)
    if a_semver != b_semver:
        return a_semver > b_semver
    # No-pre > any-pre
    if not a_pre and not b_pre:
        return True
    if not a_pre:
        return True
    if not b_pre:
        return False
    return _compare_pre(a_pre, b_pre) >= 0


def _compare_pre(a: list[str], b: list[str]) -> int:
    """Numeric-aware comparison of prerelease segments."""
    for i in range(max(len(a), len(b))):
        ai = a[i] if i < len(a) else ""
        bi = b[i] if i < len(b) else ""
        if ai == bi:
            continue
        if ai.isdigit() and bi.isdigit():
            return (int(ai) > int(bi)) - (int(ai) < int(bi))
        if ai.isdigit():
            return -1
        if bi.isdigit():
            return 1
        # split alphabetic prefix + trailing digits
        am = re.match(r"^([A-Za-z]+)(\d+)?$", ai)
        bm = re.match(r"^([A-Za-z]+)(\d+)?$", bi)
        if am and bm and am.group(1) == bm.group(1):
            ad = int(am.group(2) or 0)
            bd = int(bm.group(2) or 0)
            return (ad > bd) - (ad < bd)
        return (ai > bi) - (ai < bi)
    return 0


def _matches_wildcard(version: str, pattern: str) -> bool:
    """Return True when ``version`` matches a wildcard pattern like ``v0.28.0-rc19.x``.

    ``.x`` matches the rest of the version string (greedy, including
    further dotted components), and ``*`` matches any run of characters.
    """
    if ".x" not in pattern and "*" not in pattern:
        return False
    # Escape, then translate our wildcards to their regex equivalents.
    regex = re.escape(pattern)
    # ``\.x`` (the escaped form of ``.x``) -> ``\.[A-Za-z0-9.\-]+`` so
    # the wildcard absorbs the remaining dotted patch suffix.
    regex = regex.replace(r"\.x", r"\.[A-Za-z0-9.\-]+")
    # ``\*`` -> ``.*``
    regex = regex.replace(r"\*", r".*")
    return bool(re.match(f"^{regex}$", version))


# ---------------------------------------------------------------------------
# Matrix loading
# ---------------------------------------------------------------------------


def load_matrix(path: Path | str = DEFAULT_MATRIX_PATH) -> dict[str, Any]:
    """Load and validate the compatibility matrix.

    Raises:
        MatrixError: file missing or shape invalid.
    """
    p = Path(path)
    if not p.is_file():
        raise MatrixError(f"compat matrix not found: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MatrixError(f"could not load matrix at {p}: {exc}") from exc
    if not isinstance(data, dict) or "entries" not in data:
        raise MatrixError(f"matrix at {p} missing top-level 'entries'")
    if not isinstance(data["entries"], dict):
        raise MatrixError(f"matrix at {p}: 'entries' must be a dict")
    return data


# ---------------------------------------------------------------------------
# Manifest extraction (filesystem JSON, or tarball-embedded JSON / YAML)
# ---------------------------------------------------------------------------


def extract_version_from_manifest_text(text: str) -> Optional[str]:
    """Pull recorder_version from a manifest text blob (JSON or YAML-ish).

    We deliberately avoid pulling in a YAML dep — manifests we control are
    JSON.  If the text is YAML, we fall back to a simple key:value line
    scan, which is sufficient for the top-level ``recorder_version`` field
    every recorder writes.
    """
    text = text.strip()
    if not text:
        return None
    # Try JSON first.
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            for key in MANIFEST_VERSION_KEYS:
                v = data.get(key)
                if isinstance(v, str) and v:
                    return v
    except json.JSONDecodeError as exc:
        # Manifest is not valid JSON; fall back to YAML line-scan below.
        # Log the parse error at DEBUG so malformed manifests are diagnosable
        # without aborting the version-extraction pipeline.
        logger.debug("manifest text is not valid JSON, falling back to line scan: %s", exc)
    # Line-scan fallback for YAML / non-JSON.
    line_re = re.compile(
        r"^\s*(recorder_version|recorderVersion)\s*[:=]\s*['\"]?([^'\"\s]+)['\"]?\s*$"
    )
    for line in text.splitlines():
        m = line_re.match(line)
        if m:
            return m.group(2)
    return None


def extract_version_from_manifest_file(path: Path | str) -> str:
    """Read ``path`` and return its recorder_version field."""
    p = Path(path)
    if not p.is_file():
        raise ManifestError(f"manifest not found: {p}")
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"could not read manifest {p}: {exc}") from exc
    v = extract_version_from_manifest_text(text)
    if v is None:
        raise ManifestError(
            f"manifest {p} contains no recorder_version field "
            f"(checked keys: {', '.join(MANIFEST_VERSION_KEYS)})"
        )
    return v


# Candidate names of the embedded manifest inside an uploaded tarball, in
# priority order.  First match wins.
TARBALL_MANIFEST_NAMES: tuple[str, ...] = (
    "MANIFEST.json",
    "manifest.json",
    "manifest.yaml",
    "manifest.yml",
)


def extract_version_from_tarball(path: Path | str) -> str:
    """Open a tarball read-only and pull recorder_version from its manifest."""
    p = Path(path)
    if not p.is_file():
        raise ManifestError(f"tarball not found: {p}")
    try:
        with tarfile.open(p, "r:*") as tar:
            for member in tar.getmembers():
                base = Path(member.name).name
                if base in TARBALL_MANIFEST_NAMES:
                    f = tar.extractfile(member)
                    if f is None:
                        continue
                    blob = f.read(8 * 1024 * 1024)  # cap at 8 MiB
                    try:
                        text = blob.decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                    v = extract_version_from_manifest_text(text)
                    if v is not None:
                        return v
    except tarfile.TarError as exc:
        raise ManifestError(f"could not read tarball {p}: {exc}") from exc
    raise ManifestError(
        f"tarball {p} contains no manifest with a recognised recorder_version field"
    )


# ---------------------------------------------------------------------------
# Compatibility check
# ---------------------------------------------------------------------------


def _today() -> _dt.date:
    """Test hook — return today's date."""
    return _dt.date.today()


def _parse_iso_date(s: str) -> Optional[_dt.date]:
    try:
        return _dt.date.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def _lookup_entry(
    recorder_version: str, matrix: dict[str, Any]
) -> tuple[Optional[str], Optional[dict[str, Any]]]:
    """Return (matched_key, entry_dict) for the recorder version.

    Exact match first; wildcard family entries (e.g. ``v0.28.0-rc19.x``)
    are tried second so explicit pins always win.
    """
    entries = matrix.get("entries") or {}
    if recorder_version in entries:
        return recorder_version, entries[recorder_version]
    # Wildcard fallback
    for key, entry in entries.items():
        if _matches_wildcard(recorder_version, key):
            return key, entry
    return None, None


def check_recorder_compat(
    recorder_version: Optional[str],
    *,
    matrix: Optional[dict[str, Any]] = None,
    matrix_path: Path | str = DEFAULT_MATRIX_PATH,
    pipeline_version: Optional[str] = None,
    upgrade_url: str = DEFAULT_UPGRADE_URL,
    today: Optional[_dt.date] = None,
) -> CompatResult:
    """Decide whether a recorder version is accepted by the pipeline.

    Args:
        recorder_version: the value pulled from the manifest.
        matrix: in-memory matrix (tests pass this directly).
        matrix_path: file to load matrix from when ``matrix`` is None.
        pipeline_version: when provided, also enforces
            ``pipeline_version >= entry.min_pipeline``.
        upgrade_url: surface in the rejection message.
        today: clock override.

    Returns:
        :class:`CompatResult`.
    """
    if not recorder_version:
        return CompatResult(
            accepted=False,
            recorder_version=None,
            matched_entry=None,
            reason="manifest is missing recorder_version",
            upgrade_url=upgrade_url,
            min_pipeline=None,
            lint_version=None,
            deprecated=False,
        )

    rv = recorder_version.strip()
    if not _RECORDER_VER_RE.match(rv):
        return CompatResult(
            accepted=False,
            recorder_version=rv,
            matched_entry=None,
            reason=f"recorder_version {rv!r} is not a recognised version string",
            upgrade_url=upgrade_url,
            min_pipeline=None,
            lint_version=None,
            deprecated=False,
        )

    matrix = matrix if matrix is not None else load_matrix(matrix_path)
    matched_key, entry = _lookup_entry(rv, matrix)
    if entry is None:
        return CompatResult(
            accepted=False,
            recorder_version=rv,
            matched_entry=None,
            reason=(
                f"recorder version {rv} is not in the compatibility matrix. "
                f"Please upgrade to the latest release."
            ),
            upgrade_url=upgrade_url,
            min_pipeline=None,
            lint_version=None,
            deprecated=False,
        )

    min_pipeline = entry.get("min_pipeline") if isinstance(entry, dict) else None
    lint_version = entry.get("lint_version") if isinstance(entry, dict) else None
    deprecated_flag = bool(entry.get("deprecated", False)) if isinstance(entry, dict) else False
    sw_end = (
        _parse_iso_date(entry.get("support_window_end", "")) if isinstance(entry, dict) else None
    )
    now = today if today is not None else _today()
    past_window = sw_end is not None and now > sw_end

    if deprecated_flag or past_window:
        reason_bits = [f"recorder version {rv} is deprecated"]
        if isinstance(entry, dict):
            why = entry.get("deprecation_reason")
            if isinstance(why, str) and why:
                reason_bits.append(f"({why})")
        if past_window and sw_end is not None:
            reason_bits.append(f"support ended {sw_end.isoformat()}")
        reason_bits.append("please upgrade to the latest release.")
        return CompatResult(
            accepted=False,
            recorder_version=rv,
            matched_entry=matched_key,
            reason=" ".join(reason_bits),
            upgrade_url=upgrade_url,
            min_pipeline=min_pipeline if isinstance(min_pipeline, str) else None,
            lint_version=lint_version if isinstance(lint_version, int) else None,
            deprecated=True,
        )

    # Optional pipeline-side enforcement
    if pipeline_version is not None and isinstance(min_pipeline, str):
        if not _PIPELINE_VER_RE.match(pipeline_version.strip()):
            return CompatResult(
                accepted=False,
                recorder_version=rv,
                matched_entry=matched_key,
                reason=(
                    f"pipeline_version {pipeline_version!r} is not a recognised "
                    "version string"
                ),
                upgrade_url=upgrade_url,
                min_pipeline=min_pipeline,
                lint_version=lint_version if isinstance(lint_version, int) else None,
                deprecated=False,
            )
        if not is_pipeline_at_least(pipeline_version.strip(), min_pipeline):
            return CompatResult(
                accepted=False,
                recorder_version=rv,
                matched_entry=matched_key,
                reason=(
                    f"pipeline {pipeline_version} is older than the minimum "
                    f"({min_pipeline}) required by recorder {rv}. "
                    "Upgrade the buyer pipeline before ingesting these tarballs."
                ),
                upgrade_url=upgrade_url,
                min_pipeline=min_pipeline,
                lint_version=lint_version if isinstance(lint_version, int) else None,
                deprecated=False,
            )

    return CompatResult(
        accepted=True,
        recorder_version=rv,
        matched_entry=matched_key,
        reason=f"recorder {rv} is supported (matched {matched_key})",
        upgrade_url=upgrade_url,
        min_pipeline=min_pipeline if isinstance(min_pipeline, str) else None,
        lint_version=lint_version if isinstance(lint_version, int) else None,
        deprecated=False,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="version_compat_checker",
        description=(
            "G251 · Reject tarballs from recorder versions incompatible "
            "with the current pipeline.  Reads ``recorder_version`` from "
            "an uploaded tarball's manifest and compares it against "
            "``bin/compat_matrix.json``."
        ),
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--tarball",
        type=Path,
        help="Path to an uploaded tarball — manifest will be extracted from it.",
    )
    src.add_argument(
        "--manifest",
        type=Path,
        help="Path to a manifest file (JSON or YAML).",
    )
    src.add_argument(
        "--version",
        type=str,
        help="Recorder version literal (e.g. v0.28.0-rc19.0.1).",
    )
    parser.add_argument(
        "--matrix",
        type=Path,
        default=DEFAULT_MATRIX_PATH,
        help=f"Path to compat matrix (default: {DEFAULT_MATRIX_PATH}).",
    )
    parser.add_argument(
        "--pipeline-version",
        type=str,
        default=None,
        help="Optional pipeline version to enforce min_pipeline >=.",
    )
    parser.add_argument(
        "--upgrade-url",
        type=str,
        default=DEFAULT_UPGRADE_URL,
        help="URL shown in the rejection message.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the result as JSON on stdout.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.tarball:
            recorder_version: Optional[str] = extract_version_from_tarball(args.tarball)
        elif args.manifest:
            recorder_version = extract_version_from_manifest_file(args.manifest)
        else:
            recorder_version = args.version
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        result = check_recorder_compat(
            recorder_version,
            matrix_path=args.matrix,
            pipeline_version=args.pipeline_version,
            upgrade_url=args.upgrade_url,
        )
    except MatrixError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        json.dump(result.to_dict(), sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        verdict = "ACCEPTED" if result.accepted else "REJECTED"
        print(f"{verdict} — {result.reason}")
        if not result.accepted:
            print(f"  upgrade: {result.upgrade_url}")
            if result.matched_entry:
                print(f"  matched_entry: {result.matched_entry}")

    return 0 if result.accepted else 1


if __name__ == "__main__":
    sys.exit(main())
