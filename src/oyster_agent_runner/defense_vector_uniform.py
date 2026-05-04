#!/usr/bin/env python3
"""defense_vector_uniform.py — Blue-team guard for G097.

Scans the first record of a vector data file to infer the expected Vector3
shape (3-element numeric vectors) and enforces that every subsequent record
conforms to the same shape.  Non-conforming rows are reported to stderr and
the process exits non-zero when violations are found.

Supported formats: CSV, JSON (array-of-arrays), NumPy ``.npy`` / ``.npz``.
Only stdlib + numpy are used at runtime.

Usage::

    python -m oyster_agent_runner.defense_vector_uniform --input data.csv
    python -m oyster_agent_runner.defense_vector_uniform --input data.json --strict
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Union

import numpy as np

__all__ = ["main", "scan_vectors", "validate_uniform_shape"]

_PathLike = Union[str, Path]


def _to_path(p: _PathLike) -> Path:
    """Coerce a string or Path to a Path object."""
    return p if isinstance(p, Path) else Path(p)


def _iter_csv(path: Path) -> Iterator[list[float]]:
    """Yield numeric rows from a CSV file (header row is skipped)."""
    with path.open(newline="") as fh:
        reader = csv.reader(fh)
        next(reader, None)  # skip header
        for row in reader:
            yield [float(v) for v in row if v.strip()]


def _iter_json(path: Path) -> Iterator[list[float]]:
    """Yield numeric rows from a JSON file (expects array-of-arrays)."""
    with path.open() as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        for key in ("vectors", "data", "records", "items"):
            if key in data:
                data = data[key]
                break
        else:
            for v in data.values():
                if isinstance(v, list):
                    data = v
                    break
    for row in data:
        if isinstance(row, (list, tuple)):
            yield [float(x) for x in row]


def _iter_npy(path: Path) -> Iterator[list[float]]:
    """Yield numeric rows from a NumPy ``.npy`` or ``.npz`` file."""
    arr = np.load(path)
    if isinstance(arr, np.lib.npyio.NpzFile):
        arr = arr[list(arr.keys())[0]]
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    for row in arr:
        yield row.tolist()


_FORMAT_MAP = {".csv": "csv", ".tsv": "csv", ".txt": "csv",
               ".json": "json", ".npy": "npy", ".npz": "npy"}
_ITERATORS = {"csv": _iter_csv, "json": _iter_json, "npy": _iter_npy}


def _reader_for(path: _PathLike) -> Iterator[list[float]]:
    """Return an iterator over numeric rows, auto-detecting format."""
    p = _to_path(path)
    fmt = _FORMAT_MAP.get(p.suffix.lower())
    if fmt is None:
        raise ValueError(f"Unsupported file extension: {p.suffix}")
    return _ITERATORS[fmt](p)


def scan_vectors(path: _PathLike) -> tuple[int, list[tuple[int, list[float]]]]:
    """Scan *path* and return ``(expected_dim, rows)``.

    The first record determines the expected dimensionality.
    """
    rows: list[tuple[int, list[float]]] = []
    expected_dim: int | None = None
    for idx, row in enumerate(_reader_for(path), start=1):
        if expected_dim is None:
            expected_dim = len(row)
        rows.append((idx, row))
    return expected_dim or 0, rows


def validate_uniform_shape(
    path: _PathLike, expected_dim: int = 3,
) -> list[tuple[int, int, list[float]]]:
    """Return ``(row_index, actual_dim, row)`` for every non-conforming row."""
    return [(idx, len(row), row)
            for idx, row in enumerate(_reader_for(path), start=1)
            if len(row) != expected_dim]


def main(argv: Sequence[str] | None = None) -> int:
    """Entry-point with argparse CLI.  Returns 0 on success, 1 on violation."""
    parser = argparse.ArgumentParser(
        description="Enforce uniform Vector3 shape across a vector data file.")
    parser.add_argument("--input", "-i", type=Path, required=True,
                        help="Path to vector data file.")
    parser.add_argument("--dim", "-d", type=int, default=3,
                        help="Expected vector dimensionality (default: 3).")
    parser.add_argument("--strict", action="store_true",
                        help="Exit non-zero on any shape mismatch.")
    args = parser.parse_args(list(argv) if argv else [])

    if not args.input.is_file():
        print(f"Error: file not found: {args.input}", file=sys.stderr)
        return 1

    violations = validate_uniform_shape(args.input, expected_dim=args.dim)
    if violations:
        for idx, actual, row in violations:
            vals = row[:6]
            suffix = "..." if len(row) > 6 else ""
            print(f"VIOLATION row {idx}: expected dim={args.dim}, got dim={actual}  "
                  f"values={vals}{suffix}", file=sys.stderr)
        print(f"\n{len(violations)} non-conforming record(s) found.", file=sys.stderr)
        return 1 if args.strict else 0

    print(f"All records conform to Vector{args.dim} shape.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
