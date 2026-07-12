#!/usr/bin/env python3
"""synthetic_disclosure_metadata.py — Cluster B synthetic-content disclosure.

Ensures ``is_synthetic``, ``engine``, ``engine_version``, and ``capture_date``
are present in every JSON / manifest / video-sidecar file.

Usage:
    python bin/synthetic_disclosure_metadata.py scan  <dir>
    python bin/synthetic_disclosure_metadata.py patch <dir> \
        [--engine NAME] [--version VER] [--date DATE]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
REQUIRED_KEYS = ("is_synthetic", "engine", "engine_version", "capture_date")
SUPPORTED_EXTS = {".json", ".yaml", ".yml", ".mp4", ".mov", ".avi", ".mkv"}
SIDECAR_EXTS = {".json", ".yaml", ".yml"}


def _load_yaml(path: Path) -> Dict[str, Any] | None:
    """Load a YAML file; returns None when PyYAML is unavailable."""
    try:
        import yaml  # lazy import
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except ImportError as exc:
        # PyYAML missing — log so operators know YAML sidecars are not being
        # validated (a sidecar with synthetic-disclosure metadata would be
        # silently skipped, leaving an apparent-PASS that is actually a
        # NO-OP). Caller still receives None to preserve control flow.
        logger.debug("synthetic_disclosure_metadata: PyYAML unavailable for %s: %s", path, exc)
        return None


def _save_yaml(path: Path, data: Dict[str, Any]) -> None:
    """Persist data as YAML."""
    import yaml  # lazy import
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, default_flow_style=False, allow_unicode=True)


def _load_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _save_json(path: Path, data: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def _sidecar_path(video: Path) -> Path:
    """Return the JSON sidecar path for a video file."""
    return video.with_suffix(video.suffix + ".meta.json")


def _collect_files(root: Path) -> List[Tuple[Path, str]]:
    """Walk *root* and return (path, loader_kind) tuples."""
    results: List[Tuple[Path, str]] = []
    for dirpath, _, filenames in os.walk(root):
        for fn in sorted(filenames):
            fp = Path(dirpath) / fn
            ext = fp.suffix.lower()
            if ext in {".json"}:
                results.append((fp, "json"))
            elif ext in {".yaml", ".yml"}:
                results.append((fp, "yaml"))
            elif ext in {".mp4", ".mov", ".avi", ".mkv"}:
                results.append((fp, "video"))
    return results


def _missing_keys(data: Dict[str, Any]) -> List[str]:
    return [k for k in REQUIRED_KEYS if k not in data]


# ---------------------------------------------------------------------------
# CLI sub-commands
# ---------------------------------------------------------------------------

def cmd_scan(args: argparse.Namespace) -> int:
    """Audit a directory and report files missing disclosure metadata."""
    root = Path(args.directory)
    if not root.is_dir():
        print(f"ERROR: {root} is not a directory", file=sys.stderr)
        return 1

    files = _collect_files(root)
    violations: List[str] = []

    for fp, kind in files:
        if kind == "json":
            data = _load_json(fp)
        elif kind == "yaml":
            data = _load_yaml(fp)
            if data is None:
                print(f"WARN: skipping {fp} (PyYAML not installed)", file=sys.stderr)
                continue
        else:  # video
            sc = _sidecar_path(fp)
            if not sc.exists():
                violations.append(f"{fp}: missing sidecar {sc.name}")
                continue
            data = _load_json(sc)

        missing = _missing_keys(data)
        if missing:
            violations.append(f"{fp}: missing {', '.join(missing)}")

    if violations:
        print(f"Found {len(violations)} file(s) with missing disclosure fields:")
        for v in violations:
            print(f"  - {v}")
        return 1
    print("All files contain required synthetic-disclosure metadata.")
    return 0


def cmd_patch(args: argparse.Namespace) -> int:
    """Add missing disclosure fields to every applicable file."""
    root = Path(args.directory)
    if not root.is_dir():
        print(f"ERROR: {root} is not a directory", file=sys.stderr)
        return 1

    defaults: Dict[str, Any] = {
        "is_synthetic": True,
        "engine": args.engine or "unknown",
        "engine_version": args.version or "0.0.0",
        "capture_date": args.date or "1970-01-01",
    }

    files = _collect_files(root)
    patched = 0

    for fp, kind in files:
        if kind == "json":
            data = _load_json(fp)
            missing = _missing_keys(data)
            if not missing:
                continue
            data.update({k: defaults[k] for k in missing})
            _save_json(fp, data)
            patched += 1
        elif kind == "yaml":
            data = _load_yaml(fp)
            if data is None:
                continue
            missing = _missing_keys(data)
            if not missing:
                continue
            data.update({k: defaults[k] for k in missing})
            _save_yaml(fp, data)
            patched += 1
        else:  # video
            sc = _sidecar_path(fp)
            data = _load_json(sc) if sc.exists() else {}
            missing = _missing_keys(data)
            if not missing:
                continue
            data.update({k: defaults[k] for k in missing})
            _save_json(sc, data)
            patched += 1

    print(f"Patched {patched} file(s).")
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: List[str] | None = None) -> int:
    """CLI entry-point returning an exit code."""
    parser = argparse.ArgumentParser(
        description="Synthetic-content disclosure metadata tool (Cluster B)."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="Audit directory for missing fields")
    p_scan.add_argument("directory", help="Root directory to scan")
    p_scan.set_defaults(func=cmd_scan)

    p_patch = sub.add_parser("patch", help="Insert missing disclosure fields")
    p_patch.add_argument("directory", help="Root directory to patch")
    p_patch.add_argument("--engine", default=None, help="Engine name (default: unknown)")
    p_patch.add_argument("--version", default=None, help="Engine version (default: 0.0.0)")
    p_patch.add_argument("--date", default=None, help="Capture date ISO-8601 (default: 1970-01-01)")
    p_patch.set_defaults(func=cmd_patch)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
