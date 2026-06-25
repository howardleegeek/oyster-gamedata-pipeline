#!/usr/bin/env python3
"""cluster_output_autoformat.py – Pre-commit auto-formatter for cluster-shipped code.

Runs ``black`` and ``ruff --fix`` on staged Python files before harness commit,
catching the unformatted-output failure mode that commonly breaks CI.

Usage
-----
    python bin/cluster_output_autoformat.py [--dry-run] [--staged] [file ...]

Exit codes
----------
    0  – all files formatted successfully (or already clean)
    1  – formatting failed or a tool is missing
    2  – CLI usage error
"""

from __future__ import annotations

import argparse
import ast
import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_staged_python_files() -> list[str]:
    """Return list of staged ``*.py`` paths from ``git diff --cached``."""
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.warning("git not available or no staged files; falling back to cwd")
        return [str(p) for p in Path(".").rglob("*.py")]
    return [line.strip() for line in result.stdout.splitlines() if line.strip().endswith(".py")]


def _validate_syntax(path: str) -> bool:
    """Return ``True`` if *path* parses as valid Python AST."""
    try:
        ast.parse(Path(path).read_text(encoding="utf-8"))
        return True
    except SyntaxError as exc:
        logger.error("Syntax error in %s: %s", path, exc)
        return False


def _run_formatter(cmd: list[str], paths: list[str]) -> int:
    """Run a formatter command (black / ruff) on *paths*. Return exit code."""
    if not shutil.which(cmd[0]):
        logger.error("%s not found on PATH – install it or skip this step", cmd[0])
        return 1
    try:
        result = subprocess.run(
            cmd + paths,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            logger.warning("%s exited %d: %s", cmd[0], result.returncode, result.stderr.strip())
        return result.returncode
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to run %s: %s", cmd[0], exc)
        return 1


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def format_files(paths: list[str], *, dry_run: bool = False) -> int:
    """Run black + ruff --fix on *paths*.

    Parameters
    ----------
    paths : list[str]
        File paths to format.
    dry_run : bool
        When ``True``, only check formatting without modifying files.

    Returns
    -------
    int
        0 on success, 1 on any failure.
    """
    if not paths:
        logger.info("No Python files to format.")
        return 0

    # Pre-flight: validate syntax so we don't corrupt broken files
    bad = [p for p in paths if not _validate_syntax(p)]
    if bad:
        logger.error("Skipping %d file(s) with syntax errors: %s", len(bad), bad)
        return 1

    if dry_run:
        black_cmd = ["black", "--check", "--diff"]
        ruff_cmd = ["ruff", "check", "--diff"]
    else:
        black_cmd = ["black", "--quiet"]
        ruff_cmd = ["ruff", "check", "--fix", "--quiet"]

    rc = 0
    rc |= _run_formatter(black_cmd, paths)
    rc |= _run_formatter(ruff_cmd, paths)

    if rc == 0:
        logger.info("All %d file(s) formatted successfully.", len(paths))
    return rc


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Entry-point with argparse CLI.

    Parameters
    ----------
    argv : sequence of str, optional
        Command-line arguments (defaults to ``sys.argv[1:]``).

    Returns
    -------
    int
        Process exit code.
    """
    parser = argparse.ArgumentParser(
        description="Pre-commit auto-formatter: runs black + ruff --fix on staged files.",
    )
    parser.add_argument(
        "files",
        nargs="*",
        default=None,
        help="Python files to format (default: staged *.py files).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Check formatting only; do not modify files.",
    )
    parser.add_argument(
        "--staged",
        action="store_true",
        default=True,
        help="Auto-detect staged Python files via git (default: True).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    paths: list[str] = args.files if args.files else _find_staged_python_files()
    return format_files(paths, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
