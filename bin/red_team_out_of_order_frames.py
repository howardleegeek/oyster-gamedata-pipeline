#!/usr/bin/env python3
"""
Red team: shuffle frame ordering in JSON — lint rejects non-monotonic frame_id sequence.

This tool reads a JSON file containing frame data, shuffles the frame ordering
so that frame_id values are no longer monotonically increasing, and writes the
result to an output file. Useful for red-team testing of lint pipelines that
enforce monotonic frame_id sequences.

Usage:
    python bin/red_team_out_of_order_frames.py input.json -o output.json
    python bin/red_team_out_of_order_frames.py input.json --seed 42
"""

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any, List, Optional


def find_frames(data: Any) -> Optional[List[Any]]:
    """Recursively search JSON data for a list of frame dicts with 'frame_id'.

    Args:
        data: Parsed JSON structure (dict or list).

    Returns:
        The first list found where elements are dicts containing 'frame_id',
        or None if no such list exists.
    """
    if isinstance(data, dict):
        if "frames" in data and isinstance(data["frames"], list):
            return data["frames"]
        for value in data.values():
            result = find_frames(value)
            if result is not None:
                return result
    elif isinstance(data, list):
        if data and isinstance(data[0], dict) and "frame_id" in data[0]:
            return data
        for item in data:
            result = find_frames(item)
            if result is not None:
                return result
    return None


def replace_frames(data: Any, new_frames: List[Any]) -> bool:
    """Replace the first matching frames list in-place with a new list.

    Args:
        data: Parsed JSON structure to mutate.
        new_frames: Replacement list of frame dicts.

    Returns:
        True if a replacement was made, False otherwise.
    """
    if isinstance(data, dict):
        if "frames" in data and isinstance(data["frames"], list):
            data["frames"] = new_frames
            return True
        for value in data.values():
            if replace_frames(value, new_frames):
                return True
    elif isinstance(data, list):
        if data and isinstance(data[0], dict) and "frame_id" in data[0]:
            data.clear()
            data.extend(new_frames)
            return True
        for item in data:
            if replace_frames(item, new_frames):
                return True
    return False


def shuffle_frames(
    input_file: Path,
    output_file: Optional[Path] = None,
    seed: Optional[int] = None,
) -> bool:
    """Load JSON, shuffle frames out of monotonic order, write result.

    Args:
        input_file: Path to the source JSON file.
        output_file: Path for the shuffled output. Defaults to
            ``<stem>_shuffled<suffix>`` next to the input.
        seed: Optional RNG seed for reproducible shuffling.

    Returns:
        True on success, False on any error.
    """
    try:
        with open(input_file, "r", encoding="utf-8") as fh:
            data: Any = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"Error reading {input_file}: {exc}", file=sys.stderr)
        return False

    frames = find_frames(data)
    if frames is None:
        print(f"No frames array found in {input_file}", file=sys.stderr)
        return False

    if len(frames) < 2:
        print(f"Only {len(frames)} frame in {input_file}; nothing to shuffle",
              file=sys.stderr)
        return False

    if seed is not None:
        random.seed(seed)

    shuffled: List[Any] = list(frames)
    random.shuffle(shuffled)

    if not replace_frames(data, shuffled):
        print(f"Failed to replace frames in {input_file}", file=sys.stderr)
        return False

    out_path = output_file or input_file.with_name(
        f"{input_file.stem}_shuffled{input_file.suffix}"
    )

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    orig_ids = [f.get("frame_id", idx) for idx, f in enumerate(frames)]
    new_ids = [f.get("frame_id", idx) for idx, f in enumerate(shuffled)]
    print(f"Shuffled {input_file}: {orig_ids} -> {new_ids}")
    print(f"Output written to {out_path}")
    return True


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).

    Returns:
        Exit code: 0 on success, 1 on failure.
    """
    parser = argparse.ArgumentParser(
        description="Shuffle frame ordering in JSON to break monotonic frame_id."
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Path to the input JSON file containing frames.",
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=None,
        help="Output file path (default: <input_stem>_shuffled.json).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for reproducible shuffling.",
    )
    args = parser.parse_args(argv)

    if not args.input.is_file():
        print(f"Input file not found: {args.input}", file=sys.stderr)
        return 1

    success = shuffle_frames(args.input, args.output, args.seed)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
