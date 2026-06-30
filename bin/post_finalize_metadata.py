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


def _detect_recorder_version(session: Path) -> str | None:
    """MECE M5 — detect recorder version from Cargo.toml.

    Tries (in priority):
      1. ``<session>/recorder_version.txt`` — recorder may drop this at session start
      2. ``vendor/recorder/Cargo.toml`` (pipeline vendored copy)
      3. Walk up from ``__file__`` to find any Cargo.toml with a [package] version
    Returns None on total failure (caller falls back to env or "unknown").
    """
    # 1. recorder-dropped sentinel
    rv_file = session / "recorder_version.txt"
    if rv_file.is_file():
        try:
            v = rv_file.read_text(encoding="utf-8").strip()
            if v:
                return v
        except OSError:
            pass
    # 2. vendor/recorder/Cargo.toml — typical pipeline layout
    candidates = [
        Path(__file__).resolve().parent.parent / "vendor" / "recorder" / "Cargo.toml",
        Path(__file__).resolve().parent.parent.parent / "gamedata-recorder" / "Cargo.toml",
    ]
    # Scan top-level [package] and [workspace.package] sections for a literal
    # ``version = "X.Y.Z"`` line. We pin to those exact section headers so we
    # don't accidentally pick up a dependency's version (deps live under
    # [dependencies] and [target.*.dependencies], NOT under *.package).
    target_sections = {"[package]", "[workspace.package]"}
    for cargo in candidates:
        if not cargo.is_file():
            continue
        try:
            in_target = False
            for line in cargo.read_text(encoding="utf-8").splitlines():
                s = line.strip()
                if s.startswith("[") and s.endswith("]"):
                    in_target = s in target_sections
                    continue
                if not in_target:
                    continue
                if s.startswith("version") and "=" in s:
                    val = s.split("=", 1)[1].strip().strip('"').strip("'")
                    # Skip ``version.workspace = true`` and ``version = { ... }``
                    if val and not val.startswith("{") and val != "true":
                        return val
        except OSError:
            continue
    return None


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
    a circular dependency).

    Bug-fix 2026-05-15: compare against POSIX relpath (not basename) so a nested
    ``depth/metadata.json`` is NOT silently excluded — only the top-level outputs
    of this script are. .DS_Store and .bak suffix matches stay basename-based
    (those should be excluded anywhere in the tree).
    """
    self_outputs = {"metadata.json", "MANIFEST.json"}
    out = []
    for p in sorted(session.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(session)
        rel_posix = rel.as_posix()
        if rel_posix in self_outputs:  # only TOP-LEVEL metadata/MANIFEST excluded
            continue
        if rel.name == ".DS_Store":  # anywhere in tree
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

    # Reasonable defaults; env vars override.
    # Bug-fix 2026-05-15: use ``_first_nonempty`` instead of ``a or b or c`` so
    # an *empty string* in existing metadata doesn't silently fall through to
    # a fresh UUID — that breaks the idempotency contract (re-running this
    # script must produce the same session_id when the prior write succeeded).
    def _first_nonempty(*vals: object) -> object:
        for v in vals:
            if v is not None and v != "":
                return v
        return None

    session_id = _first_nonempty(
        existing.get("session_id"),
        os.environ.get("OYSTER_SESSION_ID"),
    ) or str(uuid.uuid4())
    device_id = _first_nonempty(
        os.environ.get("OYSTER_DEVICE_ID"),
        socket.gethostname(),
    ) or "unknown"
    location = _first_nonempty(
        os.environ.get("OYSTER_LOCATION"),
    ) or "unspecified"
    recording_started_utc = _first_nonempty(
        existing.get("recording_started_utc"),
    ) or now_utc.isoformat()
    recorder_version = _first_nonempty(
        os.environ.get("OYSTER_RECORDER_VERSION"),
        existing.get("recorder_version"),
        _detect_recorder_version(session),  # MECE M5 — Cargo.toml auto-detect
    ) or "unknown"

    meta = {
        "schema_version": 1,
        "session_id": session_id,                  # M2: UUID4 unique cross-machine
        "device_id": device_id,                    # RBGA C1
        "location": location,                      # RBGA C1
        "recording_started_utc": recording_started_utc,
        "metadata_written_utc": now_utc.isoformat(),  # M3: UTC timestamps
        "recorder_version": recorder_version,
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
    """Generate metadata.json and MANIFEST.json for a session directory.

    Args:
        argv: Command-line arguments. Expects argv[1] to be a session directory path.

    Returns:
        0 on success, 2 on usage error or invalid directory.

    Raises:
        SystemExit: Does not raise; errors are logged and returned as exit codes.
    """
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
