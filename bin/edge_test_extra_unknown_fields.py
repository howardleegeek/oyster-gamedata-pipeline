#!/usr/bin/env python3
"""edge_test_extra_unknown_fields.py

Boundary test: vendor adds extra keys to an action_camera record.
The lint should warn about unknown fields but still accept the record.

Usage:
    python3 bin/edge_test_extra_unknown_fields.py [--verbose] [--strict]
"""

import argparse
import json
import sys
from typing import Any, Dict, List, Tuple

# Canonical schema for action_camera records (known fields only)
ACTION_CAMERA_SCHEMA: Dict[str, type] = {
    "brand": str,
    "model": str,
    "resolution": str,
    "fps": int,
    "waterproof_depth_m": float,
    "weight_g": int,
    "connectivity": list,
    "battery_mah": int,
}


def lint_action_camera(
    record: Dict[str, Any],
    strict: bool = False,
) -> Tuple[bool, List[str]]:
    """Validate an action_camera record against the known schema.

    Args:
        record: Dictionary representing the action_camera entry.
        strict: If True, unknown fields cause rejection instead of a warning.

    Returns:
        Tuple of (accepted: bool, messages: list of str).
    """
    messages: List[str] = []
    accepted = True

    # Check for missing required fields
    for field in ACTION_CAMERA_SCHEMA:
        if field not in record:
            messages.append(f"WARNING: missing field '{field}'")

    # Check for extra unknown fields
    unknown_keys = set(record.keys()) - set(ACTION_CAMERA_SCHEMA.keys())
    if unknown_keys:
        for key in sorted(unknown_keys):
            msg = f"WARNING: unknown field '{key}' (vendor extension)"
            messages.append(msg)
        if strict:
            accepted = False
            messages.append("REJECTED: unknown fields present in strict mode")

    # Type-check known fields that are present
    for field, expected_type in ACTION_CAMERA_SCHEMA.items():
        if field in record and not isinstance(record[field], expected_type):
            messages.append(
                f"WARNING: field '{field}' expected {expected_type.__name__}, "
                f"got {type(record[field]).__name__}"
            )

    return accepted, messages


def build_sample_record() -> Dict[str, Any]:
    """Construct a sample action_camera record with vendor-added extra keys."""
    return {
        "brand": "GoPro",
        "model": "HERO12 Black",
        "resolution": "5.3K",
        "fps": 60,
        "waterproof_depth_m": 10.0,
        "weight_g": 154,
        "connectivity": ["wifi", "bluetooth", "usb-c"],
        "battery_mah": 1720,
        # Vendor-specific extensions (unknown to schema)
        "vendor_sku": "GP-H12B-2024",
        "firmware_version": "2.1.0",
        "custom_lut_support": True,
        "gps_enabled": True,
    }


def main(argv: List[str] | None = None) -> int:
    """Entry point: parse args, lint sample record, report results.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).

    Returns:
        Exit code: 0 on success, 1 on rejection.
    """
    parser = argparse.ArgumentParser(
        description="Boundary test: extra unknown fields in action_camera record"
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Reject records with unknown fields instead of warning",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print the full record JSON before linting",
    )
    args = parser.parse_args(argv)

    record = build_sample_record()

    if args.verbose:
        print(json.dumps(record, indent=2))
        print("---")

    accepted, messages = lint_action_camera(record, strict=args.strict)

    for msg in messages:
        print(msg)

    if accepted:
        print("RESULT: ACCEPTED (with warnings)")
        return 0
    else:
        print("RESULT: REJECTED")
        return 1


if __name__ == "__main__":
    sys.exit(main())
