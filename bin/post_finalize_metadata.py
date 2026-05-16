#!/usr/bin/env python3
"""Generate metadata.json + MANIFEST.json per session — closes RBGA C1/C4/C5/C6.

Runs AFTER bin/finalize_session.py. Adds:
- metadata.json: timestamp (UTC), session_id (UUID4), device_id, location,
  recorder_version (read from manifest if available)
- MANIFEST.json: sha256 per file in the session dir (for buyer-side
  tamper detection)

Both required by lint v3 #22 and RECORDER_BUYER_GAP_AUDIT.md C1/C4.
Idempotent: rerunning produces the same MANIFEST given same files.

Usage:
  python3 bin/post_finalize_metadata.py <session_dir>
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import socket
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


def file_sha256(path: Path, chunk_size: int = 1 << 20) -> str:
    """Stream sha256 of one file (handles 1 GB mp4 without loading into RAM)."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def collect_files(session: Path) -> list[Path]:
    """All files in session_dir, sorted, excluding metadata.json + MANIFEST.json
    themselves (they're outputs of THIS script — including them would create
    a circular dependency)."""
    out = []
    for p in sorted(session.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(session)
        if rel.name in ("metadata.json", "MANIFEST.json", ".DS_Store"):
            continue
        if rel.name.endswith(".bak"):  # heal-tool backups don't count
            continue
        out.append(p)
    return out


def write_metadata(session: Path) -> dict:
    """Generate metadata.json per RBGA C1/C5/C6."""
    now_utc = datetime.now(timezone.utc)
    # Preserve existing session_id if metadata.json already there (idempotent)
    existing = {}
    mpath = session / "metadata.json"
    if mpath.exists():
        try:
            existing = json.loads(mpath.read_text())
        except (json.JSONDecodeError, OSError):
            existing = {}

    # Reasonable defaults; env vars override
    session_id = existing.get("session_id") or os.environ.get("OYSTER_SESSION_ID") or str(uuid.uuid4())
    device_id = os.environ.get("OYSTER_DEVICE_ID") or socket.gethostname()
    location = os.environ.get("OYSTER_LOCATION") or "unspecified"

    meta = {
        "schema_version": 1,
        "session_id": session_id,                  # M2: UUID4 unique cross-machine
        "device_id": device_id,                    # RBGA C1
        "location": location,                      # RBGA C1
        "recording_started_utc": existing.get("recording_started_utc") or now_utc.isoformat(),
        "metadata_written_utc": now_utc.isoformat(),  # M3: UTC timestamps
        "recorder_version": os.environ.get("OYSTER_RECORDER_VERSION") or existing.get("recorder_version") or "unknown",
        "platform": {
            "os": platform.system(),
            "release": platform.release(),
            "python": platform.python_version(),
        },
    }
    mpath.write_text(json.dumps(meta, indent=2, sort_keys=True))
    return meta


def write_manifest(session: Path) -> dict:
    """Generate MANIFEST.json with sha256 per file per RBGA C4 / M4 / F8."""
    files = collect_files(session)
    entries = {}
    for p in files:
        rel = str(p.relative_to(session)).replace(os.sep, "/")
        entries[rel] = {
            "sha256": file_sha256(p),
            "size_bytes": p.stat().st_size,
        }
    manifest = {
        "schema_version": 1,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "file_count": len(entries),
        "files": entries,
    }
    (session / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return manifest


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: post_finalize_metadata.py <session_dir>", file=sys.stderr)
        return 2
    session = Path(argv[1])
    if not session.is_dir():
        print(f"FATAL: not a directory: {session}", file=sys.stderr)
        return 2

    meta = write_metadata(session)
    manifest = write_manifest(session)
    print(f"  metadata.json: session_id={meta['session_id'][:8]}... device={meta['device_id']}")
    print(f"  MANIFEST.json: {manifest['file_count']} files hashed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
