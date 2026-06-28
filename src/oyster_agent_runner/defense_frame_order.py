#!/usr/bin/env python3
"""
Defense Frame Order Validator (Blue Team for G092)

Streaming validator asserting frame_id strictly increases by 1.
Detects frame order anomalies in video/audio streaming pipelines.

Usage:
    python defense_frame_order.py --input frames.jsonl
    cat frames.jsonl | python defense_frame_order.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator


class FrameOrderValidator:
    """Validates that frame_ids strictly increase by 1 in a stream."""

    def __init__(self, allow_reset: bool = False, verbose: bool = False) -> None:
        self.allow_reset = allow_reset
        self.verbose = verbose
        self._expected: int | None = None
        self._count = 0
        self._errors = 0

    def validate(self, frame_id: int) -> bool:
        """Validate a single frame's ordering.

        Args:
            frame_id: The frame identifier to validate.

        Returns:
            True if frame_id follows expected ordering, False otherwise.
        """
        self._count += 1

        if self._expected is None:
            self._expected = frame_id + 1
            return True

        if frame_id == self._expected:
            self._expected = frame_id + 1
            return True

        if self.allow_reset and frame_id == 0:
            self._expected = 1
            return True

        self._errors += 1
        if self.verbose:
            print(
                f"ERROR: expected frame_id={self._expected}, got {frame_id}",
                file=sys.stderr,
            )
        return False

    @property
    def stats(self) -> dict:
        """Return validation statistics."""
        return {"total": self._count, "errors": self._errors}


def parse_stream(stream) -> Iterator[dict]:
    """Parse JSON lines from an input stream.

    Skips blank lines and comment lines starting with '#'.

    Args:
        stream: Iterable of text lines.

    Yields:
        Parsed JSON objects as dicts.
    """
    for raw_line in stream:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        yield json.loads(line)


def validate_stream(
    stream,
    allow_reset: bool = False,
    verbose: bool = False,
    stop_on_error: bool = False,
) -> int:
    """Validate frame ordering from an input stream.

    Args:
        stream: Iterable of text lines (JSONL format).
        allow_reset: If True, allow frame_id to reset to 0.
        verbose: If True, print details about each error.
        stop_on_error: If True, stop processing on first error.

    Returns:
        0 if all frames are valid, 1 if any errors occurred.
    """
    validator = FrameOrderValidator(allow_reset=allow_reset, verbose=verbose)

    for frame in parse_stream(stream):
        frame_id = frame.get("frame_id")
        if frame_id is None:
            continue

        valid = validator.validate(int(frame_id))
        if not valid and stop_on_error:
            break

    stats = validator.stats
    print(
        f"Validation: {stats['total'] - stats['errors']}/{stats['total']} valid, "
        f"{stats['errors']} errors",
        file=sys.stderr,
    )
    return 1 if stats["errors"] > 0 else 0


def main(argv: list[str]) -> int:
    """CLI entry point for the defense frame order validator.

    Args:
        argv: Command-line arguments (excluding program name).

    Returns:
        Exit code: 0 on success, 1 on validation failure.
    """
    parser = argparse.ArgumentParser(description="Validate frame_id ordering (Blue Team for G092)")
    parser.add_argument(
        "--input",
        "-i",
        type=argparse.FileType("r"),
        default=sys.stdin,
        help="Input file (JSON lines with frame_id field)",
    )
    parser.add_argument(
        "--allow-reset",
        "-r",
        action="store_true",
        help="Allow frame_id to reset to 0 (new stream)",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose error output")
    parser.add_argument(
        "--stop-on-error",
        "-s",
        action="store_true",
        help="Stop processing on first error",
    )
    args = parser.parse_args(argv)

    return validate_stream(
        args.input,
        allow_reset=args.allow_reset,
        verbose=args.verbose,
        stop_on_error=args.stop_on_error,
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
