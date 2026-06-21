#!/usr/bin/env python3
"""
utc_timestamps.py — UTC timestamp helper + naive ``datetime.now()`` auditor.

Closes audit gap G281: recorder code must emit timezone-aware UTC timestamps
so clips collected from different machines can be merged on a single
timeline. This module provides

* :func:`now_utc_iso` — canonical "right now in UTC, ISO 8601, Z-suffixed"
  timestamp string used by the recorder + buyer pipeline.
* :func:`audit_recorder_module` — a static auditor that scans a Python file
  for naive ``datetime.now()`` / ``datetime.utcnow()`` calls and prints the
  offending lines so they can be migrated.

The auditor uses :mod:`ast`, not regex, so it tolerates comments, string
literals containing ``datetime.now()`` text, and aliased imports (``from
datetime import datetime as DT``).

Usage::

    # at runtime in the recorder
    from utc_timestamps import now_utc_iso
    record["captured_at"] = now_utc_iso()

    # at audit time in CI
    python3 bin/utc_timestamps.py audit src/recorder/clip_writer.py
"""
from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence

# ---------- runtime helper ------------------------------------------------ #


def now_utc_iso() -> str:
    """Return current UTC time as ``YYYY-MM-DDTHH:MM:SSZ`` (seconds precision).

    Implementation detail: we replace the trailing ``+00:00`` produced by
    :meth:`datetime.isoformat` with a literal ``Z`` so the string matches the
    ingest schema exactly.
    """
    iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    # isoformat on a tz-aware UTC datetime ends with "+00:00".
    if iso.endswith("+00:00"):
        return iso[:-6] + "Z"
    return iso


# ---------- static auditor ----------------------------------------------- #


@dataclass(frozen=True)
class NaiveDatetimeFinding:
    """One offending call site in the audited file."""
    path: Path
    lineno: int
    col_offset: int
    snippet: str
    reason: str


# Names that mean "the datetime class" after we resolve imports.
def _collect_datetime_names(tree: ast.AST) -> set[str]:
    """Find every local name that refers to ``datetime.datetime``."""
    names: set[str] = set()
    for node in ast.walk(tree):
        # `import datetime` -> `datetime` module
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "datetime":
                    names.add(alias.asname or "datetime")
        # `from datetime import datetime [as DT]`
        if isinstance(node, ast.ImportFrom) and node.module == "datetime":
            for alias in node.names:
                if alias.name == "datetime":
                    names.add(alias.asname or "datetime")
    if not names:
        # Best-effort default — many files do `import datetime` and reference
        # `datetime.datetime`.
        names.add("datetime")
    return names


def _call_target(node: ast.Call) -> Optional[str]:
    """Render the called expression as ``a.b.c`` (or ``None`` if irregular)."""
    parts: List[str] = []
    cur: ast.AST = node.func
    while True:
        if isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
            continue
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
            break
        return None
    parts.reverse()
    return ".".join(parts)


def _has_tz_kwarg(call: ast.Call) -> bool:
    """Return True if the call passes a ``tz=`` keyword argument."""
    for kw in call.keywords:
        if kw.arg == "tz":
            return True
    # Positional call: datetime.now(timezone.utc) — first positional arg
    # is the tz. We treat any positional arg as 'has tz'.
    return bool(call.args)


def audit_recorder_module(path: Path | str) -> List[NaiveDatetimeFinding]:
    """Scan a Python file and return findings for naive datetime calls.

    A finding is emitted for either:

    * ``datetime.now()`` with no ``tz=`` argument, or
    * any call to ``datetime.utcnow()`` (deprecated, naive by definition).

    Calls like ``datetime.now(timezone.utc)`` or ``datetime.now(tz=UTC)`` are
    accepted.

    The function ALSO prints each finding to stdout for shell-driven use, so
    callers can rely on the return value or just pipe stdout into CI logs.

    Args:
        path: Path to a ``.py`` source file.

    Returns:
        A list of :class:`NaiveDatetimeFinding` (empty if the file is clean).

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        SyntaxError: If the file fails to parse. Caller should treat this as
            an audit failure.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"audit target missing: {p}")

    source = p.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(p))
    source_lines = source.splitlines()

    dt_names = _collect_datetime_names(tree)
    findings: List[NaiveDatetimeFinding] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = _call_target(node)
        if target is None:
            continue

        # `datetime.now` / `datetime.datetime.now` / aliased `DT.now`
        head, _, tail = target.rpartition(".")
        if tail not in {"now", "utcnow"}:
            continue

        # The "head" should refer to one of our datetime names. We accept:
        #   datetime.now           -> head="datetime"
        #   datetime.datetime.now  -> head="datetime.datetime"
        #   DT.now (aliased)       -> head="DT"
        head_root = head.split(".")[0] if head else ""
        if head_root not in dt_names:
            continue

        if tail == "utcnow":
            reason = "datetime.utcnow() is deprecated and returns a naive value"
            findings.append(_make_finding(p, node, source_lines, reason))
            continue

        if not _has_tz_kwarg(node):
            reason = "datetime.now() called without tz= (naive datetime)"
            findings.append(_make_finding(p, node, source_lines, reason))

    for f in findings:
        print(f"{f.path}:{f.lineno}:{f.col_offset}: {f.reason}\n    {f.snippet}")

    return findings


def _make_finding(
    path: Path,
    node: ast.Call,
    source_lines: Sequence[str],
    reason: str,
) -> NaiveDatetimeFinding:
    snippet = ""
    if 0 < node.lineno <= len(source_lines):
        snippet = source_lines[node.lineno - 1].strip()
    return NaiveDatetimeFinding(
        path=path,
        lineno=node.lineno,
        col_offset=node.col_offset,
        snippet=snippet,
        reason=reason,
    )


# ---------- CLI ---------------------------------------------------------- #


def _cli(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description="UTC timestamp helper + auditor.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("now", help="Print now_utc_iso() and exit.")

    audit = sub.add_parser("audit", help="Audit a Python file for naive datetime.now().")
    audit.add_argument("paths", nargs="+", type=Path)

    args = p.parse_args(argv)

    if args.cmd == "now":
        print(now_utc_iso())
        return 0

    if args.cmd == "audit":
        total = 0
        for path in args.paths:
            try:
                findings = audit_recorder_module(path)
            except SyntaxError as exc:
                print(f"{path}: parse error: {exc}", file=sys.stderr)
                total += 1
                continue
            total += len(findings)
        return 0 if total == 0 else 1

    return 1  # pragma: no cover


if __name__ == "__main__":
    sys.exit(_cli())
