#!/usr/bin/env python3
"""
Red Team: Duplicate Frame ID Detection

Purpose: Detect duplicate frame_id values in JSON records.
This script is used for red team testing to ensure lint detects
duplicate frame_id values and rejects them appropriately.

Exit codes: 0=no duplicates, 1=duplicates found, 2=error.
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple


def parse_args(argv: List[str]) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Detect duplicate frame_id values in JSON records"
    )
    parser.add_argument("input", help="Input JSON file path")
    parser.add_argument("--field", "-f", default="frame_id",
                       help="Field name to check (default: frame_id)")
    parser.add_argument("--verbose", "-v", action="store_true",
                       help="Show detailed output with record indices")
    return parser.parse_args(argv)


def load_json(filepath: str) -> List[Dict[str, Any]]:
    """Load records from a JSON file.

    Args:
        filepath: Path to the JSON file.

    Returns:
        List of record dictionaries.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If JSON structure is invalid.
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {filepath}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Normalize to list of records
    if isinstance(data, dict):
        records = data.get("records", [data])
    elif isinstance(data, list):
        records = data
    else:
        raise ValueError(f"Expected list or dict, got {type(data).__name__}")

    for i, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(f"Record at index {i} is not a dict")

    return records


def find_duplicates(records: List[Dict[str, Any]], field: str
                   ) -> Tuple[Dict[Any, List[int]], Set[Any]]:
    """Find duplicate values for a given field.

    Args:
        records: List of record dictionaries.
        field: Field name to check for duplicates.

    Returns:
        Tuple of (value_to_indices mapping, set of duplicate values).
    """
    value_to_indices: Dict[Any, List[int]] = defaultdict(list)

    for idx, record in enumerate(records):
        if field in record:
            value_to_indices[record[field]].append(idx)

    duplicates = {val for val, idxs in value_to_indices.items() if len(idxs) > 1}
    return dict(value_to_indices), duplicates


def main(argv: List[str]) -> int:
    """Main entry point for duplicate frame_id detection.

    Args:
        argv: Command line arguments.

    Returns:
        Exit code: 0=no duplicates, 1=duplicates found, 2=error.
    """
    args = parse_args(argv)

    try:
        records = load_json(args.input)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as e:
        print(f"JSON parse error in {args.input}: {e}", file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"Invalid data structure: {e}", file=sys.stderr)
        return 2

    if not records:
        print(f"Warning: No records found in {args.input}", file=sys.stderr)
        return 2

    value_to_indices, duplicates = find_duplicates(records, args.field)

    if not duplicates:
        print(f"PASS: No duplicate {args.field} values in {len(records)} records")
        return 0

    print(f"FAIL: Found {len(duplicates)} duplicate {args.field} value(s)")
    for val in sorted(duplicates, key=str):
        indices = value_to_indices[val]
        if args.verbose:
            print(f"  {args.field}={val!r} at indices: {indices}")
        else:
            print(f"  {args.field}={val!r} appears {len(indices)} times")

    print("Lint rejects: duplicate frame_id values detected", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
