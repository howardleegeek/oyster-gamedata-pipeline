#!/usr/bin/env python3
"""
Red Team Tool: Inject NaN into camera_position coordinates.

This module intentionally corrupts camera_position data with NaN values
to test that lint/validation pipelines properly reject invalid data
rather than silently propagating NaN through the system.
"""

import argparse
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

import yaml


def inject_nan(data: dict, field_path: str = "camera_position",
               coords: Optional[List[str]] = None) -> dict:
    """Inject NaN into coordinate fields at the given path."""
    if coords is None:
        coords = ["x", "y", "z"]

    parts = field_path.split(".")
    current = data
    for part in parts[:-1]:
        if part not in current:
            raise ValueError(f"Field path '{field_path}' not found")
        current = current[part]

    final_key = parts[-1]
    if final_key not in current:
        raise ValueError(f"Field '{final_key}' not found at '{field_path}'")

    pos = current[final_key]
    if isinstance(pos, dict):
        for c in coords:
            if c in pos:
                pos[c] = float("nan")
    return data


def load_yaml(path: Path) -> dict:
    """Load and validate YAML file."""
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML: {e}")
    if data is None:
        raise ValueError(f"Empty YAML: {path}")
    return data


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point for NaN injection tool."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", "-i", type=Path, required=True,
                        help="Input YAML with camera_position")
    parser.add_argument("--output", "-o", type=Path, required=True,
                        help="Output file for corrupted data")
    parser.add_argument("--field", "-f", default="camera_position",
                        help="Field path to corrupt")
    parser.add_argument("--coordinates", "-c", nargs="+",
                        default=["x", "y", "z"], help="Coords to corrupt")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate only, don't write output")

    args = parser.parse_args(argv)

    if not args.input.exists():
        print(f"Error: Input not found: {args.input}", file=sys.stderr)
        return 1

    try:
        data = load_yaml(args.input)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    try:
        corrupted = inject_nan(data, args.field, args.coordinates)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.dry_run:
        print("Dry run complete")
        return 0

    # Atomic write via temp file
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml",
                                          delete=False, encoding="utf-8") as tmp:
            tmp_path = Path(tmp.name)
            yaml.safe_dump(corrupted, tmp, default_flow_style=False)
        tmp_path.replace(args.output)
    except OSError as e:
        print(f"Error writing output: {e}", file=sys.stderr)
        return 1

    print(f"Corrupted data written to: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
