#!/usr/bin/env python3
"""Autoresearch: enumerate top 10 lint failure modes from vendor tarballs.

Scans vendor tarballs (.tar.gz/.tgz), extracts them, runs stdlib-only static
analysis (ast + compile), categorises failures, and prints the top-N ranked
by frequency.  Drives the spec backlog.

Usage::
    python bin/autoresearch_failure_modes.py --tarballs /path/to/tarballs_dir
    python bin/autoresearch_failure_modes.py --tarballs /path --top 15 -v
"""
from __future__ import annotations

import argparse
import ast
import collections
import logging
import os
import pathlib
import re
import sys
import tarfile
import tempfile
from typing import Dict, List

__version__ = "0.1.0"
__all__ = ["main"]
logger = logging.getLogger("autoresearch")

# Failure-mode taxonomy ---------------------------------------------------
FAILURE_MODES: Dict[str, str] = {
    "syntax_error": "Python syntax error (invalid source)",
    "undefined_name": "Reference to undefined name",
    "unused_import": "Imported name never used",
    "redefined_unused": "Name redefined but previous value unused",
    "undefined_local": "Local variable referenced before assignment",
    "duplicate_argument": "Duplicate keyword argument in call",
    "return_outside_function": "Return statement outside function",
    "yield_outside_function": "Yield statement outside function",
    "star_import": "Wildcard import (from X import *)",
    "late_future_import": "__future__ import not at top of file",
    "string_format_error": "Invalid %-format or f-string",
    "bare_except": "Bare except clause",
    "missing_docstring": "Module / class / function missing docstring",
    "line_too_long": "Line exceeds 120 characters",
    "trailing_whitespace": "Trailing whitespace on line",
    "mixed_indentation": "Mixed tabs and spaces",
    "other": "Unclassified lint issue",
}

# Regex patterns for text-level checks ------------------------------------
_RE_SYNTAX = re.compile(r"invalid syntax|unexpected indent|expected an indented block")
_RE_STAR = re.compile(r"^\s*from\s+\S+\s+import\s+\*", re.MULTILINE)
_RE_BARE = re.compile(r"^\s*except\s*:", re.MULTILINE)
_RE_FUTURE = re.compile(r"__future__")
_RE_LONG = re.compile(r".{121,}")
_RE_TRAIL = re.compile(r"[ \t]+$", re.MULTILINE)
_RE_MIXED = re.compile(r"^( +\t|\t+ )", re.MULTILINE)

# Lint helpers (stdlib-only) ----------------------------------------------

def _classify_compile_error(err: Exception) -> str:
    """Map a compile/AST error to a failure-mode key."""
    msg = str(err).lower()
    if _RE_SYNTAX.search(msg):
        return "syntax_error"
    if "not defined" in msg:
        return "undefined_name"
    if "assigned to but never used" in msg:
        return "unused_import"
    if "keyword argument repeated" in msg:
        return "duplicate_argument"
    if "'return' outside" in msg:
        return "return_outside_function"
    if "'yield' outside" in msg:
        return "yield_outside_function"
    return "syntax_error"


def _lint_source(source: str, filepath: str) -> List[str]:
    """Return a list of failure-mode keys found in *source*."""
    modes: List[str] = []
    try:
        compile(source, filepath, "exec")
    except (SyntaxError, Exception) as exc:
        logger.debug("Compile error in %r: %s", filepath, exc)
        return [_classify_compile_error(exc)]
    try:
        tree = ast.parse(source, filename=filepath)
    except Exception as e:
        logger.debug("AST parse error in %r: %s", filepath, e)
        return ["syntax_error"]
    if _RE_STAR.search(source):
        modes.append("star_import")
    if _RE_BARE.search(source):
        modes.append("bare_except")
    # __future__ not at top
    future_seen = False
    for line in source.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if _RE_FUTURE.search(s):
            if future_seen:
                modes.append("late_future_import")
            future_seen = True
        elif future_seen:
            break
    if _RE_LONG.search(source):
        modes.append("line_too_long")
    if _RE_TRAIL.search(source):
        modes.append("trailing_whitespace")
    if _RE_MIXED.search(source):
        modes.append("mixed_indentation")
    # Missing module-level docstring
    if not (tree.body and isinstance(tree.body[0], ast.Expr)
            and isinstance(tree.body[0].value, (ast.Constant, ast.Str))):
        modes.append("missing_docstring")
    return modes

# Tarball processing ------------------------------------------------------

_PY_EXTS = {".py", ".pyi"}
_TARBALL_SUF = {".tar.gz", ".tgz", ".tar.bz2", ".tar.xz", ".tar"}


def _extract_and_lint(tarball_path: str, tmpdir: str) -> List[str]:
    """Extract tarball, lint all .py files, return failure-mode list."""
    modes: List[str] = []
    try:
        with tarfile.open(tarball_path, "r:*") as tf:
            tf.extractall(tmpdir)
    except Exception as exc:
        logger.warning("Extract failed %s: %s", tarball_path, exc)
        return modes
    for root, _dirs, files in os.walk(tmpdir):
        for fname in files:
            if pathlib.Path(fname).suffix not in _PY_EXTS:
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as fh:
                    source = fh.read()
            except Exception as e:
                logger.debug("Failed to read %r: %s", fpath, e)
                continue
            modes.extend(_lint_source(source, fpath))
    return modes


def _find_tarballs(directory: str) -> List[str]:
    """Return sorted list of tarball paths inside *directory*."""
    results = [e.path for e in os.scandir(directory)
               if e.is_file() and any(e.name.endswith(s) for s in _TARBALL_SUF)]
    results.sort()
    return results

# Reporting ---------------------------------------------------------------

def _report_top(counter: collections.Counter, top_n: int,
                total_files: int, total_tarballs: int) -> None:
    """Print human-readable top-N failure-mode table."""
    sep = "=" * 72
    print(f"\n{sep}\n  Autoresearch Failure-Mode Report"
          f"\n  Tarballs scanned : {total_tarballs}"
          f"\n  Python files linted (approx): {total_files}\n{sep}\n")
    print(f"  {'Rank':<6} {'Count':>7}  {'Mode':<25}  Description")
    print(f"  {'-'*6} {'-'*7}  {'-'*25}  {'-'*40}")
    for rank, (mode, count) in enumerate(counter.most_common(top_n), 1):
        print(f"  {rank:<6} {count:>7}  {mode:<25}  {FAILURE_MODES.get(mode, 'Unknown')}")
    print(f"\n{sep}\n")

# CLI ---------------------------------------------------------------------

def main(argv: List[str] | None = None) -> int:
    """Entry-point: parse args, scan tarballs, report top failure modes."""
    parser = argparse.ArgumentParser(
        description="Enumerate top lint failure modes from vendor tarballs.")
    parser.add_argument("--tarballs", required=True,
                        help="Directory containing vendor tarballs.")
    parser.add_argument("--top", type=int, default=10,
                        help="Number of top failure modes to display (default: 10).")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Enable debug logging.")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s")
    if not os.path.isdir(args.tarballs):
        logger.error("Directory not found: %s", args.tarballs)
        return 2
    tarballs = _find_tarballs(args.tarballs)
    if not tarballs:
        logger.error("No tarballs found in %s", args.tarballs)
        return 1
    logger.info("Found %d tarball(s) in %s", len(tarballs), args.tarballs)
    mode_counter: collections.Counter = collections.Counter()
    total_files = 0
    for idx, tb in enumerate(tarballs, 1):
        logger.info("[%d/%d] %s", idx, len(tarballs), os.path.basename(tb))
        with tempfile.TemporaryDirectory() as tmpdir:
            modes = _extract_and_lint(tb, tmpdir)
            mode_counter.update(modes)
            total_files += max(len(modes), 1)
    if not mode_counter:
        logger.info("No failure modes detected across %d tarball(s).", len(tarballs))
        return 0
    _report_top(mode_counter, args.top, total_files, len(tarballs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
