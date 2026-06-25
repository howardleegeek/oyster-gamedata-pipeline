#!/usr/bin/env python3
"""
clip_uuid.py — Per-clip UUID4 generator + injection helpers.

Closes audit gap G280 / C6: every recorded clip needs a globally unique
identifier so the ingest pipeline can deduplicate / cross-reference clips
captured on different machines without collision.

This module is intentionally tiny and stdlib-only so it can be imported by
``recorder_consumer_lite`` and the buyer pipeline without dragging in
heavyweight dependencies.

Public API
----------
* :func:`new_clip_uuid` — return a fresh hex UUID4.
* :func:`inject_uuid` — add ``clip_uuid`` to a systeminfo dict and drop a
  marker file inside the clip directory.

Usage::

    from clip_uuid import new_clip_uuid, inject_uuid

    sysinfo = {"hostname": "alice-pc", ...}
    clip_dir = Path("clips/2026-05-05T12-00-00")
    clip_uuid = inject_uuid(sysinfo, clip_dir)
    # sysinfo["clip_uuid"] is now set; clip_dir has a `.clip_uuid_<uuid>` file
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, MutableMapping

# Marker filename prefix written into the clip directory as a side-channel
# proof of identity (used by the verifier to detect tampering / mismatch).
MARKER_PREFIX = ".clip_uuid_"

# Key inserted into the systeminfo dict.
SYSTEMINFO_KEY = "clip_uuid"


def new_clip_uuid() -> str:
    """Return a freshly minted UUID4 hex string (32 chars, no dashes).

    UUID4 is used because it is collision-safe across machines without a
    central registry (122 bits of entropy from ``os.urandom`` via
    :func:`uuid.uuid4`).
    """
    return uuid.uuid4().hex


def _write_marker(clip_dir: Path, clip_uuid: str) -> Path:
    """Create the dotfile marker inside ``clip_dir``. Returns the marker path."""
    marker = clip_dir / f"{MARKER_PREFIX}{clip_uuid}"
    # Touch with empty content; the filename itself carries the data.
    marker.write_bytes(b"")
    return marker


def inject_uuid(
    systeminfo_dict: MutableMapping[str, Any],
    clip_dir: Path | str,
    clip_uuid: str | None = None,
) -> str:
    """Stamp ``clip_uuid`` onto a systeminfo dict and a side-channel marker file.

    Args:
        systeminfo_dict: A mutable mapping (typically the dict that will be
            serialised to ``systeminfo.json``). The key
            :data:`SYSTEMINFO_KEY` is added in-place. If the key already
            exists, the existing value is preserved (idempotent).
        clip_dir: Path to the clip directory. Must already exist; the marker
            file is written inside it.
        clip_uuid: Optional pre-generated UUID hex. If ``None``, a new one is
            generated. Useful for tests / re-injection.

    Returns:
        The UUID string that was actually written to the systeminfo dict.

    Raises:
        FileNotFoundError: If ``clip_dir`` does not exist.
        NotADirectoryError: If ``clip_dir`` is not a directory.
    """
    clip_dir_path = Path(clip_dir)
    if not clip_dir_path.exists():
        raise FileNotFoundError(f"clip_dir does not exist: {clip_dir_path}")
    if not clip_dir_path.is_dir():
        raise NotADirectoryError(f"clip_dir is not a directory: {clip_dir_path}")

    # Honour an existing systeminfo value (idempotent re-injection).
    existing = systeminfo_dict.get(SYSTEMINFO_KEY)
    if isinstance(existing, str) and existing:
        chosen = existing
    else:
        chosen = clip_uuid or new_clip_uuid()
        systeminfo_dict[SYSTEMINFO_KEY] = chosen

    _write_marker(clip_dir_path, chosen)
    return chosen


def _cli(argv: list[str] | None = None) -> int:
    """Tiny CLI for shell-driven usage during recorder bootstrap."""
    p = argparse.ArgumentParser(description="Generate / inject a per-clip UUID.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("new", help="Print a fresh UUID4 hex.")

    inj = sub.add_parser("inject", help="Inject UUID into systeminfo.json + marker.")
    inj.add_argument("--clip-dir", required=True, type=Path)
    inj.add_argument(
        "--systeminfo",
        required=True,
        type=Path,
        help="Path to systeminfo.json (read+rewritten in place).",
    )
    inj.add_argument("--uuid", default=None, help="Use this UUID instead of generating.")

    args = p.parse_args(argv)

    if args.cmd == "new":
        print(new_clip_uuid())
        return 0

    if args.cmd == "inject":
        if not args.systeminfo.exists():
            print(f"systeminfo file missing: {args.systeminfo}", file=sys.stderr)
            return 2
        sysinfo: Dict[str, Any] = json.loads(args.systeminfo.read_text(encoding="utf-8"))
        if not isinstance(sysinfo, dict):
            print("systeminfo must be a JSON object", file=sys.stderr)
            return 2
        chosen = inject_uuid(sysinfo, args.clip_dir, args.uuid)
        args.systeminfo.write_text(
            json.dumps(sysinfo, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(chosen)
        return 0

    return 1  # pragma: no cover


if __name__ == "__main__":
    sys.exit(_cli())
