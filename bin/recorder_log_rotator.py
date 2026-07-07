#!/usr/bin/env python3
"""G277: Recorder log rotator.

Rotate ``~/OysterRecorder.log`` when it grows past 10 MB. Keeps the last
five rotations on disk as ``.1`` through ``.5``; older copies are dropped.

Solves recorder gap F4 (unbounded log fills tester disk over multi-day
recording campaigns).

The rotator is intentionally side-effect-only and dependency-free so the
recorder can call ``rotate_if_needed()`` from a background tick or before
opening its log handle for append.

CLI usage::

    python bin/recorder_log_rotator.py                 # default path / 10MB
    python bin/recorder_log_rotator.py --path /tmp/r.log --max-mb 1
    python bin/recorder_log_rotator.py --force         # rotate regardless

Programmatic usage::

    from bin.recorder_log_rotator import rotate_if_needed
    rotated = rotate_if_needed()  # returns True if a rotation happened
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_LOG_PATH = Path.home() / "OysterRecorder.log"
DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
KEEP_ROTATIONS = 5


def _size_bytes(path: Path) -> int:
    """Return size in bytes, or 0 if the file does not exist."""
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0
    except OSError as exc:
        logger.debug("recorder_log_rotator: stat %s failed: %s", path, exc)
        return 0


def rotate(log_path: Path, keep: int = KEEP_ROTATIONS) -> bool:
    """Perform the rotation cascade ``log -> .1 -> .2 -> ... -> .keep``.

    Returns ``True`` if the active log was rotated, ``False`` if there was
    nothing to rotate (no active log file).
    """
    if not log_path.exists():
        return False

    # Drop the oldest rotation if it exists.
    oldest = log_path.with_suffix(log_path.suffix + f".{keep}")
    if oldest.exists():
        try:
            oldest.unlink()
        except OSError as exc:
            logger.debug("recorder_log_rotator: unlink %s failed: %s", oldest, exc)

    # Cascade .N -> .N+1 from the back so we never overwrite a survivor.
    for idx in range(keep - 1, 0, -1):
        src = log_path.with_suffix(log_path.suffix + f".{idx}")
        dst = log_path.with_suffix(log_path.suffix + f".{idx + 1}")
        if src.exists():
            try:
                os.replace(src, dst)
            except OSError as exc:
                logger.debug(
                    "recorder_log_rotator: replace %s -> %s failed: %s", src, dst, exc
                )

    # Active log -> .1
    first = log_path.with_suffix(log_path.suffix + ".1")
    try:
        os.replace(log_path, first)
        return True
    except OSError as exc:
        logger.debug(
            "recorder_log_rotator: active rotate %s -> %s failed: %s",
            log_path,
            first,
            exc,
        )
        return False


def rotate_if_needed(
    log_path: Path = DEFAULT_LOG_PATH,
    max_bytes: int = DEFAULT_MAX_BYTES,
    keep: int = KEEP_ROTATIONS,
) -> bool:
    """Rotate the log file only if it exceeds ``max_bytes``."""
    if _size_bytes(log_path) <= max_bytes:
        return False
    return rotate(log_path, keep=keep)


def main(argv: Optional[list] = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Rotate recorder log file")
    parser.add_argument("--path", type=Path, default=DEFAULT_LOG_PATH)
    parser.add_argument("--max-mb", type=float, default=10.0)
    parser.add_argument("--keep", type=int, default=KEEP_ROTATIONS)
    parser.add_argument("--force", action="store_true",
                        help="Rotate regardless of current size")
    args = parser.parse_args(argv)

    max_bytes = int(args.max_mb * 1024 * 1024)
    if args.force:
        rotated = rotate(args.path, keep=args.keep)
    else:
        rotated = rotate_if_needed(args.path, max_bytes, keep=args.keep)
    print(f"rotated={rotated} path={args.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
