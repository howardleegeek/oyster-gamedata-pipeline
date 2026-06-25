#!/usr/bin/env python3
"""recorder_motw_unblock.py — Strip Windows Mark-of-the-Web from a binary.

Windows attaches a ``Zone.Identifier`` Alternate Data Stream (ADS) to files
downloaded from the internet or copied across SMB shares.  When SmartScreen
sees this stream on a freshly-deployed ``.exe`` it shows a full-screen
"Windows protected your PC" prompt the very first time the file is launched,
which causes flaky test runs when engineering pushes recorder builds to
tester machines via SSH.

This standalone utility wraps PowerShell's ``Unblock-File`` cmdlet to remove
the ADS, falling back to a pure-Python ``os.remove`` of the
``<path>:Zone.Identifier`` stream on systems where PowerShell is unavailable.

Usage:
    python3 bin/recorder_motw_unblock.py <path_to_exe>
    python3 bin/recorder_motw_unblock.py <path_to_exe> --quiet

Exit codes:
    0 — file unblocked (or had no MOTW to begin with).
    1 — target path missing or unreadable.
    2 — unblock attempt failed (PowerShell error / permission denied).
    3 — invoked on a non-Windows host (no-op, MOTW is Windows-only).
"""

from __future__ import annotations

import argparse
import logging
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

ZONE_IDENTIFIER_SUFFIX: str = ":Zone.Identifier"


def is_windows() -> bool:
    """Return True if the current host is Windows.

    MOTW only exists on NTFS volumes mounted by Windows, so this guard lets
    the same script be invoked from cross-platform CI without spurious
    failures on Linux / macOS engineers' laptops.
    """
    return platform.system() == "Windows"


def has_motw(exe_path: Path) -> bool:
    """Return True if ``exe_path`` carries a ``Zone.Identifier`` ADS.

    Args:
        exe_path: Absolute path to the binary to inspect.

    Returns:
        True when the ADS exists, False otherwise (including when the host
        is not Windows — non-NTFS filesystems cannot store ADS).
    """
    if not is_windows():
        return False
    ads_path = f"{exe_path}{ZONE_IDENTIFIER_SUFFIX}"
    try:
        with open(ads_path, "rb"):
            return True
    except OSError:
        return False


def unblock_via_powershell(exe_path: Path) -> bool:
    """Invoke PowerShell ``Unblock-File`` on ``exe_path``.

    Returns True on success, False on any PowerShell / subprocess error.
    PowerShell's ``Unblock-File`` is a no-op when no MOTW is present, so
    callers may invoke this defensively.
    """
    cmd: List[str] = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        f"Unblock-File -LiteralPath '{exe_path}'",
    ]
    try:
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=True,
            timeout=15,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.warning("PowerShell Unblock-File failed: %s", exc)
        return False


def unblock_via_ads_delete(exe_path: Path) -> bool:
    """Delete the ``Zone.Identifier`` ADS directly with ``os.remove``.

    Used as a fallback when PowerShell is unavailable.  On NTFS, removing
    the ADS path strips MOTW exactly the same way as ``Unblock-File``.
    """
    ads_path = f"{exe_path}{ZONE_IDENTIFIER_SUFFIX}"
    try:
        os.remove(ads_path)
        return True
    except OSError as exc:
        logger.warning("ADS delete failed: %s", exc)
        return False


def unblock(exe_path: Path) -> bool:
    """Strip MOTW from ``exe_path`` using whichever method works first.

    Tries PowerShell first (the documented Microsoft path) and falls back
    to direct ADS deletion if PowerShell is missing or non-zero exits.
    """
    if unblock_via_powershell(exe_path):
        return True
    return unblock_via_ads_delete(exe_path)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("exe_path", type=Path, help="Path to .exe to unblock")
    parser.add_argument("--quiet", action="store_true", help="Suppress informational output")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point.  See module docstring for exit codes."""
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    if not is_windows():
        logger.info("Not running on Windows — MOTW is a no-op here.")
        return 3

    exe_path: Path = args.exe_path.resolve()
    if not exe_path.exists():
        logger.error("Target not found: %s", exe_path)
        return 1

    if not has_motw(exe_path):
        logger.info("No MOTW present on %s — nothing to do.", exe_path)
        return 0

    if unblock(exe_path):
        logger.info("Unblocked %s", exe_path)
        return 0
    logger.error("Failed to unblock %s", exe_path)
    return 2


if __name__ == "__main__":
    sys.exit(main())
