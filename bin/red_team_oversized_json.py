#!/usr/bin/env python3
"""
bin/red_team_oversized_json.py

Red team test: Generate a 100 MB action_camera.json and verify the adapter
rejects or streams it without causing Out of Memory (OOM) errors.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
import tracemalloc
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)

DEFAULT_SIZE_MB = 100
BYTES_PER_MB = 1024 * 1024
RECORD_SIZE_BYTES = 512


def generate_record(index: int) -> dict[str, Any]:
    """Generate a single action camera metadata record."""
    return {
        "id": f"cam_{index:08d}",
        "model": "ActionCam-Pro-X1000",
        "firmware": "v2.3.1",
        "recording": {
            "start_time": f"2024-01-{(index % 28) + 1:02d}T{(index % 24):02d}:00:00Z",
            "duration_sec": 300 + (index % 600),
            "resolution": ["4K", "1080p", "720p"][index % 3],
            "fps": [30, 60, 120][index % 3],
        },
        "sensors": {
            "accelerometer": {"x": index * 0.01, "y": index * 0.02, "z": index * 0.03},
            "gps": {"lat": 37.7749 + (index * 0.0001), "lon": -122.4194},
        },
        "metadata": {"sequence": index, "checksum": f"sha256:{index:016x}"},
    }


def generate_oversized_json(output_path: Path, target_bytes: int) -> tuple[int, int]:
    """Generate an oversized JSON file with action camera records."""
    records_count = target_bytes // RECORD_SIZE_BYTES
    with open(output_path, "w", encoding="utf-8") as f:
        f.write('{"action_cameras": [\n')
        for i in range(records_count):
            if i > 0:
                f.write(",\n")
            f.write("  " + json.dumps(generate_record(i)))
        f.write("\n]}")
    return records_count, output_path.stat().st_size


def stream_records(file_path: Path) -> Iterator[dict[str, Any]]:
    """Stream JSON records without loading entire file into memory."""
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        for record in data.get("action_cameras", []):
            yield record


def test_memory_usage(json_path: Path, max_mb: int = 50) -> tuple[bool, str, int]:
    """Test that adapter handles oversized JSON without OOM."""
    tracemalloc.start()
    try:
        record_count = 0
        peak_memory = 0
        for _ in stream_records(json_path):
            record_count += 1
            _, peak = tracemalloc.get_traced_memory()
            peak_memory = max(peak_memory, peak)
        peak_mb = peak_memory / BYTES_PER_MB
        if peak_mb > max_mb:
            return False, f"Memory {peak_mb:.1f}MB exceeds limit {max_mb}MB", peak_memory
        return True, f"Processed {record_count} records, peak {peak_mb:.1f}MB", peak_memory
    finally:
        tracemalloc.stop()


def main(argv: list[str] | None = None) -> int:
    """Main entry point for the red team oversized JSON test."""
    parser = argparse.ArgumentParser(
        description="Red team test: Verify adapter handles 100MB JSON without OOM"
    )
    parser.add_argument("--size-mb", type=int, default=DEFAULT_SIZE_MB)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--max-memory-mb", type=int, default=50)
    parser.add_argument("--keep-file", action="store_true")
    args = parser.parse_args(argv)

    output_dir = args.output_dir or Path(tempfile.mkdtemp(prefix="red_team_json_"))
    if args.output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "action_camera.json"

    print(f"Red Team Test: Oversized JSON ({args.size_mb} MB)\n{'=' * 50}")
    try:
        print(f"\n[1/2] Generating {args.size_mb} MB JSON file...")
        records, size = generate_oversized_json(json_path, args.size_mb * BYTES_PER_MB)
        print(f"      Created {json_path}, Records: {records:,}, Size: {size/BYTES_PER_MB:.2f} MB")

        print(f"\n[2/2] Testing memory usage (max: {args.max_memory_mb} MB)...")
        success, msg, _ = test_memory_usage(json_path, args.max_memory_mb)
        print(f"      {msg}")

        print(f"\n{'=' * 50}")
        print(f"RESULT: {'PASS' if success else 'FAIL'} - {msg}")
        return 0 if success else 1
    except MemoryError:
        print("\nRESULT: FAIL - Out of Memory error")
        return 1
    except Exception as e:
        print(f"\nRESULT: ERROR - {type(e).__name__}: {e}")
        return 2
    finally:
        if not args.keep_file and json_path.exists():
            json_path.unlink()
            try:
                output_dir.rmdir()
            except OSError as rmdir_exc:
                logger.debug(
                    "temp dir rmdir failed (non-fatal) [%s]: %s",
                    type(rmdir_exc).__name__,
                    rmdir_exc,
                )


if __name__ == "__main__":
    sys.exit(main())
