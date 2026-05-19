#!/usr/bin/env python3
"""Red team: simulate ENOSPC during write — verify adapter aborts cleanly.

Creates a tiny mounted filesystem (disk image / tmpfs), fills it to capacity,
then attempts a write.  Verifies that OSError(errno=ENOSPC) is raised and
can be caught with a clear error message.

Usage:
    python3 bin/red_team_disk_full.py [--size MB] [--payload KB] [--verbose]
"""

import argparse
import errno
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from typing import List, Optional


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point — parse CLI args and run the ENOSPC simulation."""
    parser = argparse.ArgumentParser(
        description="Simulate disk-full (ENOSPC) during write to verify clean abort.",
    )
    parser.add_argument("--size", type=int, default=1,
                        help="Simulated disk size in MiB (default: 1).")
    parser.add_argument("--payload", type=int, default=256,
                        help="Payload to write after disk is full, in KiB (default: 256).")
    parser.add_argument("--verbose", action="store_true",
                        help="Print detailed progress messages.")
    args = parser.parse_args(argv)
    return _run(args.size, args.payload, args.verbose)


def _run(size_mb: int, payload_kb: int, verbose: bool) -> int:
    """Set up a tiny filesystem, fill it, then attempt an oversized write."""
    work_dir: str = tempfile.mkdtemp(prefix="redteam_enospc_")
    mount_point: str = os.path.join(work_dir, "mnt")
    os.makedirs(mount_point, exist_ok=True)
    payload_bytes: int = payload_kb * 1024
    try:
        _setup_tiny_fs(work_dir, mount_point, size_mb, verbose)
        return _simulate_enospc(mount_point, payload_bytes, verbose)
    except Exception as exc:
        print(f"[ERROR] setup failed: {exc}", file=sys.stderr)
        return 1
    finally:
        _cleanup(work_dir, mount_point, verbose)


def _setup_tiny_fs(work_dir: str, mount_point: str, size_mb: int,
                   verbose: bool) -> None:
    """Create and mount a tiny filesystem appropriate for the current OS."""
    sys_name: str = platform.system()
    if sys_name == "Darwin":
        dmg_path = os.path.join(work_dir, "tiny.dmg")
        subprocess.run(
            ["hdiutil", "create", "-size", f"{size_mb}m", "-fs", "HFS+",
             "-volname", "redteam", "-type", "SPARSE", dmg_path],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["hdiutil", "attach", "-nobrowse", "-mountpoint", mount_point, dmg_path],
            check=True, capture_output=True, text=True,
        )
    elif sys_name == "Linux":
        subprocess.run(
            ["mount", "-t", "tmpfs", "-o", f"size={size_mb}m", "tmpfs", mount_point],
            check=True, capture_output=True,
        )
    else:
        raise RuntimeError(f"Unsupported platform: {sys_name}")
    if verbose:
        print(f"[INFO] tiny fs mounted at {mount_point}", file=sys.stderr)


def _simulate_enospc(mount_point: str, payload_bytes: int, verbose: bool) -> int:
    """Fill the tiny filesystem then attempt an oversized write.

    Returns 0 on success (ENOSPC was caught cleanly), 1 on failure.
    """
    filler_path = os.path.join(mount_point, "filler.bin")
    try:
        # Fill the filesystem until ENOSPC
        _fill_disk(mount_point, filler_path, verbose)
    except OSError as exc:
        if exc.errno != errno.ENOSPC:
            print(f"[ERROR] unexpected error while filling: {exc}", file=sys.stderr)
            return 1
        if verbose:
            print("[INFO] disk filled to capacity (ENOSPC on filler)", file=sys.stderr)

    # Now attempt the actual payload write — should also hit ENOSPC
    target_path = os.path.join(mount_point, "payload.bin")
    try:
        with open(target_path, "wb") as fh:
            fh.write(b"\x00" * payload_bytes)
        print("[FAIL] write succeeded — ENOSPC was NOT triggered", file=sys.stderr)
        return 1
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            print(f"[PASS] ENOSPC caught cleanly: {exc}", file=sys.stderr)
            return 0
        print(f"[FAIL] unexpected OSError (errno={exc.errno}): {exc}", file=sys.stderr)
        return 1


def _fill_disk(mount_point: str, filler_path: str, verbose: bool) -> None:
    """Write data to *filler_path* until the filesystem is full."""
    chunk: bytes = b"\xff" * (1024 * 1024)  # 1 MiB chunks
    with open(filler_path, "wb") as fh:
        while True:
            try:
                fh.write(chunk)
                fh.flush()
            except OSError as exc:
                if exc.errno == errno.ENOSPC:
                    raise
                raise
            if verbose:
                stat = os.statvfs(mount_point)
                free_mb = (stat.f_bavail * stat.f_frsize) / (1024 * 1024)
                print(f"[INFO] filling… {free_mb:.1f} MiB free", file=sys.stderr)


def _cleanup(work_dir: str, mount_point: str, verbose: bool) -> None:
    """Unmount and remove the temporary filesystem."""
    sys_name: str = platform.system()
    try:
        if sys_name == "Darwin":
            subprocess.run(
                ["hdiutil", "detach", mount_point],
                check=False, capture_output=True,
            )
        elif sys_name == "Linux":
            subprocess.run(
                ["umount", mount_point],
                check=False, capture_output=True,
            )
    except Exception as exc:
        if verbose:
            print(f"[WARN] unmount failed (non-fatal): {exc}", file=sys.stderr)
    try:
        shutil.rmtree(work_dir, ignore_errors=True)
    except Exception as exc:
        if verbose:
            print(f"[WARN] cleanup failed (non-fatal): {exc}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
