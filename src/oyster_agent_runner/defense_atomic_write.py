#!/usr/bin/env python3
"""Atomic file write helper using tempfile + os.replace."""

import argparse
import contextlib
import os
import sys
import tempfile
from pathlib import Path


def write_atomic(
    path: str | Path,
    data: bytes | str,
    *,
    mode: str = "w",
    encoding: str | None = None,
    preserve_permissions: bool = True,
) -> None:
    """Atomically write data to file using tempfile + os.replace."""
    path = Path(path)

    # Validate arguments
    if mode not in ("w", "wb"):
        raise ValueError(f"Invalid mode: {mode}")
    if "b" in mode and not isinstance(data, bytes):
        raise TypeError("Bytes required for binary mode")
    if "b" not in mode and not isinstance(data, str):
        raise TypeError("String required for text mode")

    # Ensure parent directory exists
    path.parent.mkdir(parents=True, exist_ok=True)

    # Create temp file in same directory
    fd = None
    temp_path = None
    try:
        fd, temp_path = tempfile.mkstemp(
            prefix=".tmp.",
            dir=str(path.parent),
            text="b" not in mode,
        )

        # Write data
        with os.fdopen(fd, mode, encoding=encoding) as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())

        # Preserve permissions if requested
        if preserve_permissions and path.exists():
            with contextlib.suppress(OSError):  # Ignore permission errors
                os.chmod(temp_path, path.stat().st_mode)

        # Atomic replace
        os.replace(temp_path, path)

    except Exception:
        # Clean up temp file on any error
        if temp_path and os.path.exists(temp_path):
            with contextlib.suppress(OSError):
                os.unlink(temp_path)
        raise


def main(argv: list[str] | None = None) -> int:
    """Command line interface."""
    parser = argparse.ArgumentParser(
        prog="defense_atomic_write", description="Atomically write data to a file"
    )
    parser.add_argument("file", help="Target file path")
    parser.add_argument("data", nargs="?", help="Data (stdin if omitted)")
    parser.add_argument("-b", "--binary", action="store_true", help="Binary mode")
    parser.add_argument("-e", "--encoding", default="utf-8", help="Text encoding")
    parser.add_argument("--no-preserve-permissions", action="store_true")

    args = parser.parse_args(argv)

    try:
        # Read data
        data = args.data if args.data is not None else sys.stdin.read()

        # Convert if needed
        if args.binary and isinstance(data, str):
            data = data.encode(args.encoding)

        # Write atomically
        write_atomic(
            args.file,
            data,
            mode="wb" if args.binary else "w",
            encoding=None if args.binary else args.encoding,
            preserve_permissions=not args.no_preserve_permissions,
        )

        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
