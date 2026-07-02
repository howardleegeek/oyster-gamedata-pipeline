#!/usr/bin/env python3
"""recorder_utc_timestamps.py — Audit & fix datetime.now() calls lacking UTC tzinfo.

Scans Python source files for bare ``datetime.now()`` invocations and replaces
them with ``datetime.now(timezone.utc)``.  Exposes ``RecorderConsumerLite`` for
downstream modules needing UTC-aware timestamps without heavy deps.

Usage::

    python3 bin/recorder_utc_timestamps.py [--dry-run] [--fix] [paths ...]
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence, Tuple


class RecorderConsumerLite:
    """Lightweight UTC timestamp helper for recorder pipelines."""

    @staticmethod
    def utc_now() -> datetime:
        """Return the current UTC time as an aware ``datetime``."""
        return datetime.now(timezone.utc)

    @staticmethod
    def utc_now_iso() -> str:
        """Return the current UTC time as an ISO-8601 string."""
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def utc_from_ts(ts: float) -> datetime:
        """Convert a POSIX timestamp to an aware UTC ``datetime``."""
        return datetime.fromtimestamp(ts, tz=timezone.utc)


def _find_bare_now_calls(source: str) -> List[Tuple[int, str]]:
    """Return (lineno, line_text) for bare ``datetime.now()`` calls via AST."""
    hits: List[Tuple[int, str]] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return hits
    lines = source.splitlines()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or node.args or node.keywords:
            continue
        func = node.func
        if (isinstance(func, ast.Attribute) and func.attr == "now"
                and isinstance(func.value, ast.Name) and func.value.id == "datetime"):
            ln = node.lineno
            hits.append((ln, lines[ln - 1].rstrip() if ln <= len(lines) else ""))
    return hits


def _fix_source(source: str) -> str:
    """Replace bare datetime.now() with datetime.now(timezone.utc)."""
    fixed = re.sub(
        r"(?<!\w)datetime\.now\s*\(\s*\)(?!\s*timezone)",
        "datetime.now(timezone.utc)", source,
    )
    if fixed != source and not re.search(r"from\s+datetime\s+import\b.*\btimezone\b", source):
        m = re.search(r"(from\s+datetime\s+import\s+)([^\n]+)", fixed)
        if m:
            existing = m.group(2).strip()
            if "timezone" not in existing:
                fixed = fixed[:m.start()] + f"{m.group(1)}{existing}, timezone" + fixed[m.end():]
        else:
            lines = fixed.splitlines(True)
            idx = 0
            for i, ln in enumerate(lines):
                if ln.startswith("#!") or ln.startswith('"""') or ln.startswith("'''"):
                    idx = i + 1
                    if ('"""' in ln or "'''" in ln) and ln.count('"""') < 2 and ln.count("'''") < 2:
                        for j in range(i + 1, len(lines)):
                            if '"""' in lines[j] or "'''" in lines[j]:
                                idx = j + 1
                                break
                    continue
                if ln.strip():
                    break
                idx = i + 1
            lines.insert(idx, "from datetime import timezone\n")
            fixed = "".join(lines)
    return fixed


def _discover_python_files(paths: Sequence[str]) -> List[Path]:
    """Collect Python files under *paths*."""
    found: List[Path] = []
    for p in paths:
        path = Path(p)
        if path.is_file() and path.suffix == ".py":
            found.append(path)
        elif path.is_dir():
            for root, _dirs, files in os.walk(path):
                for fname in files:
                    if fname.endswith(".py"):
                        found.append(Path(root) / fname)
    return sorted(found)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry-point: audit (and optionally fix) recorder source files."""
    parser = argparse.ArgumentParser(description="Audit recorder code for bare datetime.now() calls.")
    parser.add_argument("paths", nargs="*", default=["."], help="Files or directories to scan.")
    parser.add_argument("--fix", action="store_true", help="Apply fixes in-place.")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Report only (default).")
    args = parser.parse_args(argv)

    files = _discover_python_files(args.paths)
    total_hits = files_with_hits = 0

    for fpath in files:
        try:
            source = fpath.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        hits = _find_bare_now_calls(source)
        if not hits:
            continue
        files_with_hits += 1
        total_hits += len(hits)
        print(f"\n{fpath}: {len(hits)} bare datetime.now() call(s)")
        for lineno, line_text in hits:
            print(f"  L{lineno}: {line_text.strip()}")
        if args.fix:
            fixed = _fix_source(source)
            if fixed != source:
                fpath.write_text(fixed, encoding="utf-8")
                print("  -> fixed and written")

    print(textwrap.dedent(f"""
        -----------------------------------------
        Summary
          Files scanned : {len(files)}
          Files with hits: {files_with_hits}
          Total hits    : {total_hits}
          Mode          : {'FIX' if args.fix else 'DRY-RUN'}
        -----------------------------------------
    """).strip())

    # Self-test: verify own AST is valid
    ast.parse(Path(__file__).read_text(encoding="utf-8"))
    return 0 if total_hits == 0 or args.fix else 1


if __name__ == "__main__":
    sys.exit(main())
