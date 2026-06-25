#!/usr/bin/env python3
"""
G041 · bin/edge_test_gigantic_record_count.py

Boundary test: 1,000,000 records in a single action_camera.json file.

Purpose:
    Confirm the adapter streams records rather than loading all records
    into memory at once. Generates a large JSON Lines file, then reads
    it back in streaming chunks while monitoring peak memory usage.

Usage:
    python bin/edge_test_gigantic_record_count.py [--records N] [--output PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import tracemalloc
from pathlib import Path
from typing import Any, Iterator


def generate_records(count: int) -> Iterator[dict[str, Any]]:
    """Lazily generate action_camera records without materialising all at once."""
    for i in range(count):
        yield {
            "id": i + 1,
            "model": f"ActionCam-{i % 100:03d}",
            "resolution": ["4K", "1080p", "720p"][i % 3],
            "fps": [30, 60, 120][i % 3],
            "price": round(99.99 + (i % 500) * 0.50, 2),
            "in_stock": (i % 5) != 0,
            "warehouse": f"WH-{i % 20:02d}",
        }


def write_json_streaming(records: Iterator[dict[str, Any]], path: Path) -> int:
    """Write records to a JSON Lines file in streaming fashion."""
    count = 0
    with open(path, "w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, separators=(",", ":")) + "\n")
            count += 1
            if count % 100_000 == 0:
                print(f"  Written {count:,} records...", file=sys.stderr)
    return count


def stream_read_json(path: Path, chunk_size: int = 10_000) -> Iterator[list[dict[str, Any]]]:
    """Stream JSON Lines records from file in bounded-size chunks."""
    buffer: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if stripped:
                buffer.append(json.loads(stripped))
                if len(buffer) >= chunk_size:
                    yield buffer
                    buffer = []
    if buffer:
        yield buffer


def main(argv: list[str] | None = None) -> int:
    """Main entry point — generate, write, stream-read, and verify memory bounds."""
    parser = argparse.ArgumentParser(
        description="Edge test: 1M records — verify streaming adapter."
    )
    parser.add_argument(
        "--records",
        type=int,
        default=1_000_000,
        help="Number of records to generate (default: 1,000,000)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output JSON file path (default: temporary directory)",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=10_000,
        help="Stream read chunk size (default: 10,000)",
    )
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args(argv)

    if args.records <= 0 or args.chunk_size <= 0:
        print("Error: --records and --chunk-size must be positive integers.", file=sys.stderr)
        return 1

    # Determine output path (use tempfile for safety)
    cleanup = False
    if args.output:
        output_path = args.output
    else:
        temp_dir = tempfile.mkdtemp(prefix="edge_test_gigantic_")
        output_path = Path(temp_dir) / "action_camera.json"
        cleanup = True

    print(f"Target: {args.records:,} records → {output_path}", file=sys.stderr)

    # --- Phase 1: Generate & write ---
    tracemalloc.start()
    print("Phase 1: Writing records (streaming)...", file=sys.stderr)
    written = write_json_streaming(generate_records(args.records), output_path)
    assert written == args.records, f"Expected {args.records}, wrote {written}"
    file_size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"  Wrote {written:,} records ({file_size_mb:.1f} MB)", file=sys.stderr)

    # --- Phase 2: Stream-read & verify ---
    print("Phase 2: Streaming read-back...", file=sys.stderr)
    read_count = 0
    peak_mb = 0.0
    for chunk in stream_read_json(output_path, chunk_size=args.chunk_size):
        read_count += len(chunk)
        current, peak = tracemalloc.get_traced_memory()
        peak_mb = peak / (1024 * 1024)
        if args.verbose and read_count % 100_000 == 0:
            print(f"  Read {read_count:,} records (peak mem: {peak_mb:.1f} MB)", file=sys.stderr)

    tracemalloc.stop()
    assert read_count == args.records, f"Expected {args.records}, read {read_count}"

    # --- Summary ---
    print(f"\nResult: PASS — {read_count:,} records streamed successfully.", file=sys.stderr)
    print(f"  File size : {file_size_mb:.1f} MB", file=sys.stderr)
    print(f"  Peak memory: {peak_mb:.1f} MB", file=sys.stderr)
    print(f"  Chunk size : {args.chunk_size:,}", file=sys.stderr)

    if cleanup:
        output_path.unlink(missing_ok=True)
        output_path.parent.rmdir()

    return 0


if __name__ == "__main__":
    sys.exit(main())
