#!/usr/bin/env python3
"""PRD p7 test: verify systeminfo required keys are present.

Validates that a system-info data file (JSON or YAML) contains all
required keys: gpu, cpu, ram_gb, os, build.  Fails closed — returns
non-zero exit code if any key is missing or the file cannot be parsed.

Usage:
    python3 bin/prd_test_systeminfo_required.py <path_to_systeminfo>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REQUIRED_KEYS: List[str] = ["gpu", "cpu", "ram_gb", "os", "build"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> Dict[str, Any]:
    """Load and return a JSON document from *path*."""
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _load_yaml(path: Path) -> Dict[str, Any]:
    """Load and return a YAML document from *path* (lazy import)."""
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        print("ERROR: PyYAML not available; cannot parse YAML input.", file=sys.stderr)
        sys.exit(2)
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        print(f"ERROR: YAML root is not a mapping in {path}.", file=sys.stderr)
        sys.exit(2)
    return data


def load_systeminfo(path: Path) -> Dict[str, Any]:
    """Load system-info data from a JSON or YAML file.

    The format is inferred from the file extension (``.json``, ``.yaml``,
    ``.yml``).  Any other extension raises ``ValueError``.
    """
    suffix = path.suffix.lower()
    if suffix == ".json":
        return _load_json(path)
    if suffix in (".yaml", ".yml"):
        return _load_yaml(path)
    raise ValueError(f"Unsupported file extension: {suffix!r}")


def validate_required_keys(data: Dict[str, Any] | None) -> List[str]:
    """Return a list of required keys that are missing from *data*.

    Returns all required keys if data is None or not a dict (fail-closed).
    """
    if data is None or not isinstance(data, dict):
        return list(REQUIRED_KEYS)
    return [key for key in REQUIRED_KEYS if key not in data]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: List[str] | None = None) -> int:
    """Entry-point: parse args, load file, validate, report.

    Returns 0 when all required keys are present, 1 otherwise.
    """
    parser = argparse.ArgumentParser(
        description="Check that a system-info file contains all required keys.",
    )
    parser.add_argument(
        "systeminfo",
        type=Path,
        help="Path to a JSON or YAML system-info file.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        default=False,
        help="Also fail if any required key has an empty / falsy value.",
    )
    args = parser.parse_args(argv)

    path: Path = args.systeminfo
    if not path.is_file():
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        return 1

    try:
        data = load_systeminfo(path)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: failed to parse {path}: {exc}", file=sys.stderr)
        return 1

    missing = validate_required_keys(data)
    if missing:
        print(f"FAIL: missing required keys in {path}: {', '.join(missing)}", file=sys.stderr)
        return 1

    if args.strict:
        empty_keys = [k for k in REQUIRED_KEYS if not data.get(k)]
        if empty_keys:
            print(
                f"FAIL: required keys with empty values in {path}: {', '.join(empty_keys)}",
                file=sys.stderr,
            )
            return 1

    print(f"OK: all required keys present in {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
