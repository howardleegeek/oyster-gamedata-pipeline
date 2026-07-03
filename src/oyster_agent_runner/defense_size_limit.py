#!/usr/bin/env python3
"""
defense_size_limit.py - Per-file size cap defender for G084

Blue team defense module enforcing size limits:
- action_camera: 10 MB limit
- video: 500 MB limit

Provides CLI and programmatic API for file size validation.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import NamedTuple

_logger = logging.getLogger(__name__)

# Size limits in bytes
SIZE_LIMITS: dict[str, int] = {
    "action_camera": 10 * 1024 * 1024,  # 10 MB
    "video": 500 * 1024 * 1024,  # 500 MB
}


class SizeCheckResult(NamedTuple):
    """Result of a size limit check."""

    file_path: str
    file_size: int
    limit_name: str
    limit_bytes: int
    is_within_limit: bool


def get_file_size(file_path: str | Path) -> int:
    """Get file size in bytes."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    return path.stat().st_size


def check_file_size(file_path: str | Path, limit_name: str = "action_camera") -> SizeCheckResult:
    """Check if file is within configured size limit."""
    if limit_name not in SIZE_LIMITS:
        raise ValueError(f"Unknown limit '{limit_name}'. Available: {list(SIZE_LIMITS.keys())}")

    file_size = get_file_size(file_path)
    limit_bytes = SIZE_LIMITS[limit_name]

    return SizeCheckResult(
        file_path=str(file_path),
        file_size=file_size,
        limit_name=limit_name,
        limit_bytes=limit_bytes,
        is_within_limit=file_size <= limit_bytes,
    )


def format_size(size_bytes: int) -> str:
    """Format bytes as human-readable string."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def scan_directory(
    directory: str | Path, limit_name: str, recursive: bool = True
) -> list[SizeCheckResult]:
    """Scan directory for files exceeding size limit."""
    dir_path = Path(directory)
    results = []
    pattern = "**/*" if recursive else "*"

    for path in dir_path.glob(pattern):
        if path.is_file():
            try:
                results.append(check_file_size(path, limit_name))
            except FileNotFoundError as exc:
                _logger.debug(
                    "defense_size_limit: skipping vanished file %s: %s",
                    path,
                    exc,
                )
                continue
            except PermissionError as exc:
                _logger.warning(
                    "defense_size_limit: skipping unreadable file %s: %s",
                    path,
                    exc,
                )
                continue
    return results


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for size limit defender."""
    parser = argparse.ArgumentParser(description="Per-file size cap defender - Blue Team G084")
    parser.add_argument("path", nargs="?", help="File or directory to check")
    parser.add_argument("--limit", "-l", choices=list(SIZE_LIMITS.keys()), default="action_camera")
    parser.add_argument("-r", "--recursive", action="store_true", help="Scan subdirectories")
    parser.add_argument("--report", action="store_true", help="Show detailed report")

    args = parser.parse_args(argv)

    if args.path is None:
        parser.print_help()
        return 0

    path = Path(args.path)

    try:
        if path.is_file():
            result = check_file_size(path, args.limit)
            if args.report:
                status = "OK" if result.is_within_limit else "EXCEEDS"
                print(
                    f"{status}: {result.file_path} ({format_size(result.file_size)} / {format_size(result.limit_bytes)})"
                )
            if not result.is_within_limit:
                print(f"ERROR: {path} exceeds {args.limit} limit", file=sys.stderr)
                return 1
            return 0

        elif path.is_dir():
            results = scan_directory(path, args.limit, args.recursive)
            violations = [r for r in results if not r.is_within_limit]

            if args.report:
                for r in results:
                    status = "OK" if r.is_within_limit else "EXCEEDS"
                    print(f"{status}: {r.file_path} ({format_size(r.file_size)})")
                print(f"Total: {len(results)}, Violations: {len(violations)}")

            if violations:
                print(
                    f"ERROR: {len(violations)} file(s) exceed {args.limit} limit", file=sys.stderr
                )
                return 1
            return 0

        else:
            print(f"ERROR: Path not found: {path}", file=sys.stderr)
            return 2

    except (FileNotFoundError, ValueError, PermissionError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
