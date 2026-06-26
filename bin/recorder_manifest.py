#!/usr/bin/env python3
"""
bin/recorder_manifest.py — Per-clip MANIFEST.json generator.

Generates a per-clip ``MANIFEST.json`` containing the SHA-256 hash of each of
the five PRD-canonical files / directories that make up a buyer-grade clip:

  1. ``video.mp4``           — H.265 RGB capture
  2. ``systeminfo.json``     — host hardware + OS metadata
  3. ``action_camera.json``  — per-frame keyboard / mouse / camera-pose log
  4. ``gameinfo.xlsx``       — scene metadata workbook
  5. ``depth/``              — directory of per-frame EXR depth maps
                                (hashed as a single rollup over all entries)

The output ``MANIFEST.json`` is written into the clip directory itself so the
vendor batch builder (``bin/generate_manifest.py``) can later roll it up into
the batch-level ``manifest.yaml`` without re-hashing.

This is a NEW FILE so ``bin/recorder_consumer_lite.py`` can call it as a
sub-process when a clip has finished post-processing.  ``recorder_consumer_lite.py``
itself is **not edited**.

Standalone CLI:

    recorder_manifest.py --clip-dir <dir>

Spec: G262 (W31 wave). PP1 priority. ~100 lines.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Allow direct execution.
_BIN_DIR = Path(__file__).resolve().parent
if str(_BIN_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR.parent))

#: Files (and one directory) that constitute a PRD-canonical clip.
CANONICAL_FILES: List[str] = [
    "video.mp4",
    "systeminfo.json",
    "action_camera.json",
    "gameinfo.xlsx",
]
CANONICAL_DIR: str = "depth"

#: Filename of the manifest written into each clip directory.
MANIFEST_NAME: str = "MANIFEST.json"


def hash_file(path: Path) -> str:
    """Return the SHA-256 hex digest of a single file.

    Delegates to :func:`bin.generate_manifest.compute_sha256` so that hash
    algorithm + chunk size match the vendor batch tooling exactly.

    Args:
        path: Existing regular file.

    Returns:
        Lower-case 64-char SHA-256 hex digest.
    """
    from bin.generate_manifest import compute_sha256  # type: ignore

    return compute_sha256(str(path))


def hash_directory(directory: Path) -> Dict[str, Any]:
    """Hash every regular file inside ``directory`` (recursively).

    Returns a rollup dict::

        {
          "file_count": N,
          "total_bytes": S,
          "sha256_of_sha256s": "<digest of sorted child digests>",
          "children": {"<rel_path>": "<sha256>", ...}
        }

    The ``sha256_of_sha256s`` is a stable single-value hash usable in
    ``MANIFEST.json`` / vendor-batch reconciliation.

    Args:
        directory: Existing directory.

    Returns:
        Rollup metadata dict.
    """
    import hashlib

    children: Dict[str, str] = {}
    total_bytes = 0
    for child in sorted(directory.rglob("*")):
        if not child.is_file():
            continue
        rel = child.relative_to(directory).as_posix()
        children[rel] = hash_file(child)
        total_bytes += child.stat().st_size

    rollup = hashlib.sha256()
    for rel in sorted(children):
        rollup.update(rel.encode("utf-8"))
        rollup.update(children[rel].encode("ascii"))
    return {
        "file_count": len(children),
        "total_bytes": total_bytes,
        "sha256_of_sha256s": rollup.hexdigest(),
        "children": children,
    }


def build_manifest(clip_dir: Path) -> Dict[str, Any]:
    """Build the per-clip MANIFEST dict.

    Missing canonical files are reported as ``None`` so downstream validators
    can flag them; we never raise solely because a file is absent (the
    recorder may legitimately produce a stop-gap MVP clip without
    ``gameinfo.xlsx``).

    Args:
        clip_dir: Existing clip directory.

    Returns:
        JSON-serialisable dict.
    """
    clip_dir = clip_dir.resolve()
    files: Dict[str, Optional[str]] = {}
    for name in CANONICAL_FILES:
        p = clip_dir / name
        files[name] = hash_file(p) if p.is_file() else None

    depth_path = clip_dir / CANONICAL_DIR
    depth_meta: Optional[Dict[str, Any]] = (
        hash_directory(depth_path) if depth_path.is_dir() else None
    )

    return {
        "schema_version": "1",
        "clip_id": clip_dir.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "files": files,
        "depth": depth_meta,
        "complete": all(v is not None for v in files.values()) and depth_meta is not None,
    }


def write_manifest(clip_dir: Path) -> Path:
    """Build and persist ``MANIFEST.json`` inside ``clip_dir``.

    Args:
        clip_dir: Existing clip directory.

    Returns:
        Path to the written ``MANIFEST.json``.

    Raises:
        NotADirectoryError: If ``clip_dir`` doesn't exist.
    """
    clip_dir = Path(clip_dir)
    if not clip_dir.is_dir():
        raise NotADirectoryError(clip_dir)
    out = clip_dir / MANIFEST_NAME
    manifest = build_manifest(clip_dir)
    out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return out


def main(argv: Optional[list] = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Generate MANIFEST.json (sha256 of 5 canonical files) for a clip"
    )
    parser.add_argument("--clip-dir", required=True, help="Directory containing clip files")
    args = parser.parse_args(argv)

    try:
        out = write_manifest(Path(args.clip_dir))
    except NotADirectoryError as exc:
        print(f"[recorder_manifest] ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"[recorder_manifest] wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
