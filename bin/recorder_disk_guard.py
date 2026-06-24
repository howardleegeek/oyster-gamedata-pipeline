#!/usr/bin/env python3
"""
bin/recorder_disk_guard.py — Pre-flight free-space check (G272, W31).

Purpose
-------
A 5-minute 1080p H.265 clip plus depth EXR sidecars is ~250–400 MB. If the
tester's ``Documents`` folder is too full, ffmpeg silently truncates and
the clip fails QA. This guard runs *before* ``ffmpeg`` is spawned and
refuses to start when free space is below ``MIN_FREE_BYTES`` (default
500 MB), surfacing a Chinese-language banner that matches the rest of the
recorder UI.

Standalone — stdlib only. Imported by ``recorder_consumer_lite.py``::

    from recorder_disk_guard import ensure_disk_space, DiskGuardError
    try:
        ensure_disk_space()
    except DiskGuardError as exc:
        show_banner(str(exc))
        sys.exit(1)
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Optional

MIN_FREE_BYTES = 500 * 1024 * 1024  # 500 MB
DEFAULT_TARGET_NAMES = ("OysterClips",)


class DiskGuardError(RuntimeError):
    """Raised when free space is below the configured floor."""


def documents_dir() -> Path:
    """Resolve the user's Documents folder cross-platform."""
    home = Path.home()
    candidates = [home / "Documents", home / "documents", home]
    for c in candidates:
        if c.is_dir():
            return c
    return home


def free_bytes(target: Optional[Path] = None) -> int:
    """Return free bytes on the volume that hosts ``target``."""
    path = target if target is not None else documents_dir()
    while not path.exists():
        if path.parent == path:
            break
        path = path.parent
    return shutil.disk_usage(str(path)).free


def ensure_disk_space(
    target: Optional[Path] = None,
    min_free_bytes: int = MIN_FREE_BYTES,
) -> int:
    """Raise :class:`DiskGuardError` if free space is below the floor."""
    free = free_bytes(target)
    if free < min_free_bytes:
        free_mb = free // (1024 * 1024)
        floor_mb = min_free_bytes // (1024 * 1024)
        raise DiskGuardError(f"磁盘剩余 {free_mb} MB 太少 — 至少需要 {floor_mb}MB")
    return free


def _main(argv: list[str]) -> int:
    target = Path(argv[0]) if argv else documents_dir()
    try:
        free = ensure_disk_space(target)
    except DiskGuardError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    free_mb = free // (1024 * 1024)
    print(f"OK — {free_mb} MB free at {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
