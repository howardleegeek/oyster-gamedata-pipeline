#!/usr/bin/env python3
"""
G101 · Defense Timestamp Range Module

Blue team defense for G086: enforce timestamps within 2024-01-01 to 2030-01-01 range.
"""

import argparse
import sys
from datetime import datetime
from typing import Optional, Tuple

MIN_TIMESTAMP: datetime = datetime(2024, 1, 1, 0, 0, 0)
MAX_TIMESTAMP: datetime = datetime(2030, 1, 1, 0, 0, 0)


def validate_timestamp(ts: datetime) -> bool:
    """Check if timestamp falls within [MIN_TIMESTAMP, MAX_TIMESTAMP]."""
    return MIN_TIMESTAMP <= ts <= MAX_TIMESTAMP


def clamp_timestamp(ts: datetime) -> datetime:
    """Clamp timestamp to valid range [MIN_TIMESTAMP, MAX_TIMESTAMP]."""
    if ts < MIN_TIMESTAMP:
        return MIN_TIMESTAMP
    if ts > MAX_TIMESTAMP:
        return MAX_TIMESTAMP
    return ts


def parse_timestamp(ts_str: str) -> Optional[datetime]:
    """Parse timestamp string (YYYY-MM-DD or ISO 8601). Returns None on failure."""
    formats = ["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"]
    for fmt in formats:
        try:
            return datetime.strptime(ts_str, fmt)
        except ValueError:
            continue
    return None


def process_timestamps(timestamps: list) -> Tuple[list, int]:
    """Process timestamps, returning (clamped_list, invalid_count)."""
    processed, invalid_count = [], 0
    for ts in timestamps:
        if not validate_timestamp(ts):
            invalid_count += 1
        processed.append(clamp_timestamp(ts))
    return processed, invalid_count


def main(argv: list) -> int:
    """Main entry point with argparse CLI. Returns exit code."""
    parser = argparse.ArgumentParser(
        description="Validate/clamp timestamps to 2024-01-01 to 2030-01-01 range."
    )
    parser.add_argument("timestamps", nargs="*", help="Timestamps (YYYY-MM-DD format)")
    parser.add_argument("--clamp", action="store_true", help="Output clamped timestamps")
    args = parser.parse_args(argv)

    if not args.timestamps:
        print("Error: No timestamps provided", file=sys.stderr)
        return 1

    exit_code = 0
    for ts_str in args.timestamps:
        ts = parse_timestamp(ts_str)
        if ts is None:
            print(f"Error: Invalid timestamp format: {ts_str}", file=sys.stderr)
            exit_code = 1
            continue
        if args.clamp:
            print(f"{ts_str} -> {clamp_timestamp(ts).isoformat()}")
        else:
            valid = validate_timestamp(ts)
            print(f"{ts_str}: {'VALID' if valid else 'INVALID'}")
            if not valid:
                exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))