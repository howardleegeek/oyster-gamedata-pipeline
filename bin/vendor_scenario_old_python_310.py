#!/usr/bin/env python3
"""
G065 · Vendor Scenario: Old Python 3.10 Compatibility Walkthrough

This module demonstrates handling datetime operations on Ubuntu 22.04
with the default Python 3.10, where datetime.UTC is not available
(it was introduced in Python 3.11).

Usage:
    python bin/vendor_scenario_old_python_310.py --input sample.csv --output output.json
    python bin/vendor_scenario_old_python_310.py --check-compat
"""

from __future__ import annotations

import argparse
import ast
import datetime
import json
import sys
import tempfile
from pathlib import Path
from typing import Any


def get_utc_now() -> datetime.datetime:
    """Get current UTC datetime without using datetime.UTC (Python 3.11+)."""
    return datetime.datetime.now(datetime.timezone.utc)


def parse_datetime_legacy(dt_string: str) -> datetime.datetime:
    """Parse ISO datetime string to timezone-aware datetime (Python 3.10 compat)."""
    normalized = dt_string.replace("Z", "+00:00")
    dt = datetime.datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def format_datetime_legacy(dt: datetime.datetime) -> str:
    """Format datetime to ISO string without using datetime.UTC."""
    if dt.tzinfo is not None:
        utc_dt = dt.astimezone(datetime.timezone.utc)
        return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return dt.isoformat()


def check_python_version() -> dict[str, Any]:
    """Check Python version and datetime.UTC availability."""
    version_info = sys.version_info
    has_datetime_utc = hasattr(datetime, "UTC")
    return {
        "major": version_info.major,
        "minor": version_info.minor,
        "has_datetime_utc": has_datetime_utc,
        "compatible": version_info >= (3, 10),
    }


def simulate_adapter_processing(data: dict[str, Any]) -> dict[str, Any]:
    """Simulate adapter processing with datetime handling."""
    result = data.copy()
    result["processed_at"] = format_datetime_legacy(get_utc_now())
    result["python_version"] = f"{sys.version_info.major}.{sys.version_info.minor}"
    return result


def validate_syntax(filepath: Path) -> bool:
    """Validate Python file syntax using AST."""
    content = filepath.read_text(encoding="utf-8")
    ast.parse(content, filename=str(filepath))
    return True


def main(argv: list[str] | None = None) -> int:
    """Main entry point for the vendor scenario script."""
    parser = argparse.ArgumentParser(
        description="G065: Vendor scenario for Python 3.10 datetime compatibility"
    )
    parser.add_argument(
        "--check-compat",
        action="store_true",
        help="Check Python version and datetime.UTC availability",
    )
    parser.add_argument("--input", type=Path, help="Input JSON file to process")
    parser.add_argument("--output", type=Path, help="Output JSON file for results")
    parser.add_argument("--validate", type=Path, help="Validate Python file syntax")

    args = parser.parse_args(argv)

    if args.check_compat:
        info = check_python_version()
        print(f"Python {info['major']}.{info['minor']}")
        print(f"Has datetime.UTC: {info['has_datetime_utc']}")
        print(f"Compatible: {info['compatible']}")
        return 0

    if args.validate:
        try:
            validate_syntax(args.validate)
            print(f"Syntax OK: {args.validate}")
            return 0
        except SyntaxError as e:
            print(f"Syntax error in {args.validate}: {e}", file=sys.stderr)
            return 1

    if args.input and args.output:
        try:
            data = json.loads(args.input.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, FileNotFoundError) as e:
            print(f"Error reading input: {e}", file=sys.stderr)
            return 1

        result = simulate_adapter_processing(data)

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as tmp:
            json.dump(result, tmp, indent=2)
            tmp_path = Path(tmp.name)

        tmp_path.replace(args.output)
        print(f"Processed: {args.input} -> {args.output}")
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
