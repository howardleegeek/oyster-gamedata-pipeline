#!/usr/bin/env python3
"""G102 · defense_path_sanitize.py — Blue-team tarball path sanitizer for G087.

Rejects tar members with absolute paths or traversal components (``..``, ``~``).
Provides a programmatic API and a CLI with ``extract`` / ``check`` sub-commands.

Usage:
    python -m src.oyster_agent_runner.defense_path_sanitize extract archive.tar /dest
    python -m src.oyster_agent_runner.defense_path_sanitize check archive.tar
"""

from __future__ import annotations

import argparse
import os
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


def is_safe_path(name: str, base_dir: str | None = None) -> tuple[bool, str]:
    """Return ``(True, "ok")`` if *name* is free of traversal / absolute attacks."""
    name = name.strip()
    if not name:
        return False, "empty member name"
    if name.startswith("/") or name.startswith("\\"):
        return False, f"absolute path: {name!r}"
    if len(name) >= 2 and name[1] == ":" and name[0].isalpha():
        return False, f"Windows absolute path: {name!r}"
    for part in PurePosixPath(name).parts:
        if part == "..":
            return False, f"traversal '..' in: {name!r}"
        if part.startswith("~"):
            return False, f"home-dir component in: {name!r}"
    if base_dir is not None:
        resolved = (Path(base_dir) / name).resolve()
        try:
            resolved.relative_to(Path(base_dir).resolve())
        except ValueError:
            return False, f"escapes base directory: {name!r}"
    return True, "ok"


def is_safe_link(member: tarfile.TarInfo, base_dir: str) -> tuple[bool, str]:
    """Validate that a symlink / hardlink target stays inside *base_dir*."""
    if member.issym() or member.islnk():
        resolved = (Path(base_dir) / member.name).parent / member.linkname
        try:
            resolved.resolve().relative_to(Path(base_dir).resolve())
        except ValueError:
            return False, f"link escapes base: {member.name!r} -> {member.linkname!r}"
    return True, "ok"


def filter_safe_members(tar_path: str, extract_dir: str) -> list[tarfile.TarInfo]:
    """Return ``TarInfo`` objects from *tar_path* that pass all safety checks."""
    safe: list[tarfile.TarInfo] = []
    with tarfile.open(tar_path, "r:*") as tf:
        for member in tf.getmembers():
            ok, reason = is_safe_path(member.name, extract_dir)
            if not ok:
                print(f"[REJECT] {member.name}: {reason}", file=sys.stderr)
                continue
            ok, reason = is_safe_link(member, extract_dir)
            if not ok:
                print(f"[REJECT] {member.name}: {reason}", file=sys.stderr)
                continue
            safe.append(member)
    return safe


def extract_safe(tar_path: str, extract_dir: str) -> int:
    """Extract only safe members from *tar_path* into *extract_dir*.
    Returns the number of members extracted."""
    os.makedirs(extract_dir, exist_ok=True)
    safe_members = filter_safe_members(tar_path, extract_dir)
    if not safe_members:
        print("[WARN] no safe members to extract", file=sys.stderr)
        return 0
    with tarfile.open(tar_path, "r:*") as tf:
        for member in safe_members:
            tf.extract(member, extract_dir, set_attrs=True)
    return len(safe_members)


def main(argv: list[str] | None = None) -> int:
    """CLI entry-point with ``extract`` and ``check`` sub-commands."""
    parser = argparse.ArgumentParser(
        prog="defense_path_sanitize",
        description="Blue-team tarball path sanitizer (G102 / G087)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_ext = sub.add_parser("extract", help="extract safe members only")
    p_ext.add_argument("tarball", help="path to archive")
    p_ext.add_argument("dest", nargs="?", default=None, help="destination (default: temp dir)")
    p_chk = sub.add_parser("check", help="audit tarball without extracting")
    p_chk.add_argument("tarball", help="path to archive")
    args = parser.parse_args(argv)

    try:
        if args.command == "extract":
            dest = args.dest or tempfile.mkdtemp(prefix="oyster_extract_")
            n = extract_safe(args.tarball, dest)
            print(f"[OK] extracted {n} member(s) to {dest}")
            return 0
        elif args.command == "check":
            safe = filter_safe_members(args.tarball, "/")
            with tarfile.open(args.tarball, "r:*") as tf:
                total = len(tf.getmembers())
            print(f"[OK] {len(safe)}/{total} members are safe")
            return 0 if len(safe) == total else 1
    except FileNotFoundError:
        print(f"[ERR] tarball not found: {args.tarball}", file=sys.stderr)
        return 1
    except tarfile.TarError as exc:
        print(f"[ERR] tar error: {exc}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
