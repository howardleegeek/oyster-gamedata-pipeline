#!/usr/bin/env python3
"""
parquet_manifest_writer.py

Cluster D: Generate manifest for gameinfo.parquet and action_camera.parquet shards
keyed by (clip_id, frame_idx), replacing xlsx (AV2 / OXE / DROID convention).

This module reads parquet files and produces a manifest JSON mapping composite
keys (clip_id, frame_idx) to shard file paths.
"""

import argparse
import json
import logging
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Lazy imports for optional dependencies
pandas = None
pyarrow = None


def _lazy_import_pandas():
    """Lazily import pandas; raise ImportError if unavailable."""
    global pandas
    if pandas is None:
        try:
            import pandas as pd
            pandas = pd
        except ImportError:
            raise ImportError("pandas is required for parquet operations")
    return pandas


def _lazy_import_pyarrow():
    """Lazily import pyarrow; raise ImportError if unavailable."""
    global pyarrow
    if pyarrow is None:
        try:
            import pyarrow as pa
            pyarrow = pa
        except ImportError:
            raise ImportError("pyarrow is required for parquet operations")
    return pyarrow


def discover_parquet_files(
    input_dir: Path,
    patterns: Optional[List[str]] = None
) -> Dict[str, Path]:
    """
    Discover parquet files in the input directory matching given patterns.

    Args:
        input_dir: Directory to search for parquet files.
        patterns: Optional list of filename patterns (e.g., ["gameinfo", "action_camera"]).

    Returns:
        Dictionary mapping base name to full Path for discovered parquet files.
    """
    if patterns is None:
        patterns = ["gameinfo", "action_camera"]

    discovered: Dict[str, Path] = {}
    for pattern in patterns:
        for pf in input_dir.glob(f"{pattern}*.parquet"):
            base = pf.stem
            # Remove numeric suffixes if present (e.g., gameinfo_0 -> gameinfo)
            base = base.rsplit("_", 1)[0] if base.split("_")[-1].isdigit() else base
            if base not in discovered:
                discovered[base] = pf
            elif pf.stat().st_mtime > discovered[base].stat().st_mtime:
                discovered[base] = pf  # Prefer newer file
    return discovered


def read_parquet_keys(
    parquet_path: Path,
    key_columns: Tuple[str, ...] = ("clip_id", "frame_idx")
) -> List[Dict[str, any]]:
    """
    Read key columns from a parquet file.

    Args:
        parquet_path: Path to the parquet file.
        key_columns: Tuple of column names to extract as keys.

    Returns:
        List of dictionaries containing key column values.
    """
    pd = _lazy_import_pandas()
    try:
        df = pd.read_parquet(parquet_path, columns=list(key_columns))
    except Exception as e:
        print(f"Warning: Could not read {parquet_path}: {e}", file=sys.stderr)
        return []

    records = df.to_dict(orient="records")
    return records


def generate_manifest(
    input_dir: Path,
    output_path: Optional[Path] = None,
    key_columns: Tuple[str, ...] = ("clip_id", "frame_idx")
) -> Dict[str, any]:
    """
    Generate a manifest mapping (clip_id, frame_idx) to shard metadata.

    Args:
        input_dir: Directory containing parquet shards.
        output_path: Optional path to write manifest JSON. If None, manifest
                     is returned but not written.
        key_columns: Column names to use as composite key.

    Returns:
        Manifest dictionary with metadata and entries.
    """
    parquet_files = discover_parquet_files(input_dir)

    manifest: Dict[str, any] = {
        "version": "1.0",
        "generated_by": "parquet_manifest_writer.py",
        "key_columns": list(key_columns),
        "shards": {},
        "entries": []
    }

    for name, path in parquet_files.items():
        shard_id = f"{name}_{path.stat().st_size}"
        manifest["shards"][shard_id] = {
            "name": name,
            "path": str(path),
            "size_bytes": path.stat().st_size
        }

        records = read_parquet_keys(path, key_columns)
        for rec in records:
            composite_key = "|".join(str(rec.get(col, "")) for col in key_columns)
            manifest["entries"].append({
                "key": composite_key,
                "shard": shard_id,
                "clip_id": rec.get("clip_id"),
                "frame_idx": rec.get("frame_idx")
            })

    # Sort entries by key for deterministic output
    manifest["entries"].sort(key=lambda x: x["key"])
    manifest["total_entries"] = len(manifest["entries"])

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
        print(f"Manifest written to {output_path}")

    return manifest


def validate_manifest(manifest: Dict[str, any]) -> bool:
    """
    Validate manifest structure and content.

    Args:
        manifest: Manifest dictionary to validate.

    Returns:
        True if valid, False otherwise.
    """
    required_fields = {"version", "generated_by", "key_columns", "shards", "entries"}
    if not required_fields.issubset(manifest.keys()):
        print("Error: Manifest missing required fields", file=sys.stderr)
        return False

    if not isinstance(manifest["entries"], list):
        print("Error: 'entries' must be a list", file=sys.stderr)
        return False

    return True


def main(argv: Optional[List[str]] = None) -> int:
    """
    CLI entry point for parquet_manifest_writer.

    Args:
        argv: Command-line arguments (defaults to sys.argv).

    Returns:
        Exit code (0 for success, non-zero for error).
    """
    parser = argparse.ArgumentParser(
        description="Generate manifest for parquet shards keyed by (clip_id, frame_idx)."
    )
    parser.add_argument(
        "input_dir",
        type=Path,
        help="Directory containing parquet files"
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=None,
        help="Output path for manifest JSON (default: stdout)"
    )
    parser.add_argument(
        "--key-columns",
        nargs=2,
        default=["clip_id", "frame_idx"],
        metavar=("COL1", "COL2"),
        help="Column names to use as composite key (default: clip_id frame_idx)"
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate manifest after generation"
    )

    args = parser.parse_args(argv)

    if not args.input_dir.is_dir():
        print(f"Error: Input directory does not exist: {args.input_dir}", file=sys.stderr)
        return 1

    # Use tempfile for output if not specified (avoid hardcoded /tmp/)
    output_path = args.output
    if output_path is None:
        temp_dir = Path(tempfile.mkdtemp())
        output_path = temp_dir / "manifest.json"

    try:
        manifest = generate_manifest(
            input_dir=args.input_dir,
            output_path=output_path,
            key_columns=tuple(args.key_columns)
        )

        if args.validate:
            if not validate_manifest(manifest):
                print("Error: Manifest validation failed", file=sys.stderr)
                return 1
            print("Manifest validation passed")

        return 0

    except ImportError as e:
        print(f"Error: Missing dependency - {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    finally:
        # Cleanup temp file if we created one
        if args.output is None and output_path and output_path.exists():
            try:
                output_path.unlink()
                output_path.parent.rmdir()
            except OSError as exc:
                # Best effort cleanup: surface transient filesystem errors
                # (e.g. ENOENT from a parallel unlink, EBUSY on Windows AV
                # scanners, permission errors on read-only mounts) at DEBUG
                # so they are observable when the log level is raised,
                # without promoting a routine cleanup race to a user-visible
                # warning. The function has already produced (or failed to
                # produce) its result; this only affects temp teardown.
                logger.debug(
                    "best-effort cleanup of %s (and parent %s) failed: %s",
                    output_path,
                    output_path.parent,
                    exc,
                )


if __name__ == "__main__":
    sys.exit(main())
