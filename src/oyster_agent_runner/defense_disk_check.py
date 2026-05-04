#!/usr/bin/env python3
"""
Defense Disk Check Module.

Blue team pre-flight check for G091: validates sufficient disk space
is available before capture operations. Requires 5 GB free minimum.

This module provides a CLI tool to verify disk availability and can be
integrated into capture workflows as a gatekeeper.
"""

import argparse
import os
import shutil
import sys

# Minimum required free space in bytes (5 GB)
MIN_FREE_BYTES = 5 * 1024 * 1024 * 1024


def get_disk_usage(path: str) -> tuple[int, int, int]:
    """
    Get disk usage statistics for the given path.

    Args:
        path: Filesystem path to check (must exist).

    Returns:
        Tuple of (total, used, free) bytes.

    Raises:
        FileNotFoundError: If path does not exist.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Path does not exist: {path}")
    return shutil.disk_usage(path)


def check_disk_space(path: str, min_bytes: int = MIN_FREE_BYTES) -> tuple[bool, int]:
    """
    Check if sufficient disk space is available at the given path.

    Args:
        path: Filesystem path to check.
        min_bytes: Minimum required free space in bytes.

    Returns:
        Tuple of (is_sufficient, free_bytes).
        - is_sufficient: True if free space >= min_bytes.
        - free_bytes: Actual free space in bytes.
    """
    try:
        _, _, free_bytes = get_disk_usage(path)
    except FileNotFoundError:
        return False, 0
    return free_bytes >= min_bytes, free_bytes


def format_bytes(num_bytes: int) -> str:
    """Format bytes as human-readable string (GB)."""
    gb = num_bytes / (1024 * 1024 * 1024)
    return f"{gb:.2f} GB"


def main(argv: list[str]) -> int:
    """
    CLI entry point for disk space check.

    Args:
        argv: Command-line arguments (excluding script name).

    Returns:
        Exit code: 0 if sufficient space, 1 if insufficient, 2 for errors.
    """
    parser = argparse.ArgumentParser(
        description="Check available disk space before capture operations."
    )
    parser.add_argument(
        "-p",
        "--path",
        default=".",
        help="Path to check disk space for (default: current directory)",
    )
    parser.add_argument(
        "-m",
        "--min-gb",
        type=float,
        default=5.0,
        help="Minimum required free space in GB (default: 5.0)",
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true", help="Suppress output, only return exit code"
    )

    args = parser.parse_args(argv)

    min_bytes = int(args.min_gb * 1024 * 1024 * 1024)

    try:
        is_sufficient, free_bytes = check_disk_space(args.path, min_bytes)
    except Exception as e:
        if not args.quiet:
            print(f"Error checking disk space: {e}", file=sys.stderr)
        return 2

    if not args.quiet:
        if is_sufficient:
            print(f"OK: {format_bytes(free_bytes)} free (required: {format_bytes(min_bytes)})")
        else:
            print(
                f"INSUFFICIENT: {format_bytes(free_bytes)} free "
                f"(required: {format_bytes(min_bytes)})",
                file=sys.stderr,
            )

    return 0 if is_sufficient else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
