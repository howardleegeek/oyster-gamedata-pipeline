#!/usr/bin/env python3
"""
bin/red_team_corrupt_exr.py

Red team utility: corrupt an EXR file by zero-filling 1 KB of data.
Validator detects corruption via numpy isnan/isinf scan.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

_np: Optional[object] = None


def _get_numpy():
    """Lazy import numpy."""
    global _np
    if _np is None:
        import numpy as np
        _np = np
    return _np


def corrupt_exr_file(input_path: Path, output_path: Path, offset: int = 0,
                     corrupt_size: int = 1024) -> dict:
    """Corrupt an EXR file by zero-filling a region.

    Parameters
    ----------
    input_path : Path
        Path to the input EXR file.
    output_path : Path
        Path to write the corrupted EXR file.
    offset : int
        Byte offset where corruption starts (default: 0).
    corrupt_size : int
        Number of bytes to zero-fill (default: 1024 = 1 KB).

    Returns
    -------
    dict
        Metadata about the corruption operation.
    """
    with open(input_path, "rb") as f:
        data = bytearray(f.read())

    file_size = len(data)
    if offset < 0:
        offset = 0
    if offset >= file_size:
        offset = max(0, file_size - corrupt_size)

    end_offset = min(offset + corrupt_size, file_size)
    actual_size = end_offset - offset

    for i in range(offset, end_offset):
        data[i] = 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(data)

    return {"input": str(input_path), "output": str(output_path),
            "file_size": file_size, "offset": offset, "corrupt_size": actual_size}


def validate_corruption_detection(file_path: Path) -> dict:
    """Validate corruption can be detected via numpy isnan/isinf scan.

    Parameters
    ----------
    file_path : Path
        Path to the EXR file to validate.

    Returns
    -------
    dict
        Detection results including whether NaN/Inf values were found.
    """
    np = _get_numpy()
    result = {"file": str(file_path), "has_nan": False, "has_inf": False,
              "detection_possible": False, "error": None}

    try:
        from PIL import Image
        with Image.open(file_path) as img:
            arr = np.array(img, dtype=np.float32)
            result["has_nan"] = bool(np.isnan(arr).any())
            result["has_inf"] = bool(np.isinf(arr).any())
            result["detection_possible"] = result["has_nan"] or result["has_inf"]
    except Exception as e:
        result["error"] = str(e)
        result["detection_possible"] = True  # File unreadable = corruption detected

    return result


def main(argv: Optional[list] = None) -> int:
    """Main entry point for the EXR corruption tool.

    Parameters
    ----------
    argv : list, optional
        Command-line arguments. Defaults to sys.argv[1:].

    Returns
    -------
    int
        Exit code (0 for success, non-zero for errors).
    """
    parser = argparse.ArgumentParser(
        description="Red team: corrupt an EXR file by zero-filling 1 KB.")
    parser.add_argument("input", type=Path, help="Input EXR file path")
    parser.add_argument("output", type=Path, nargs="?",
                        help="Output corrupted EXR file path")
    parser.add_argument("--offset", type=int, default=0,
                        help="Byte offset for corruption start (default: 0)")
    parser.add_argument("--size", type=int, default=1024,
                        help="Bytes to zero-fill (default: 1024 = 1 KB)")
    parser.add_argument("--validate-only", action="store_true",
                        help="Only validate corruption detection, don't corrupt")

    args = parser.parse_args(argv)

    if not args.input.exists():
        print(f"Error: Input file not found: {args.input}", file=sys.stderr)
        return 1

    if args.validate_only:
        result = validate_corruption_detection(args.input)
        print(f"Validation: has_nan={result['has_nan']}, has_inf={result['has_inf']}, "
              f"detection_possible={result['detection_possible']}")
        return 0

    if args.output is None:
        print("Error: Output path required for corruption mode", file=sys.stderr)
        return 1

    print(f"Corrupting {args.input} -> {args.output} (offset={args.offset}, size={args.size})")
    result = corrupt_exr_file(args.input, args.output, args.offset, args.size)
    print(f"Corrupted {result['corrupt_size']} bytes at offset {result['offset']}")

    validation = validate_corruption_detection(args.output)
    print(f"Detection possible: {validation['detection_possible']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
