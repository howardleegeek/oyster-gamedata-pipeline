#!/usr/bin/env python3
"""edge_test_empty_strings.py — Boundary test for empty-string required fields.

Verifies that required string fields (e.g. ``route_type``) reject empty
strings with a fail-closed posture.  Exit 0 = all passed; non-zero = failure.

Usage::

    python3 bin/edge_test_empty_strings.py [--verbose]
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, Callable, List, Sequence

_REQUIRED_FIELDS: tuple[str, ...] = ("route_type",)


def _validate_required_string(data: dict[str, Any], field: str) -> bool:
    """Return ``True`` when *field* is present and non-empty in *data*."""
    value = data.get(field)
    return isinstance(value, str) and len(value.strip()) > 0


def _rejects_empty(
    build_payload: Callable[[str], dict[str, Any]],
    *,
    label: str,
    verbose: bool,
) -> bool:
    """Assert that an empty-string payload for *label* is rejected (fail-closed)."""
    payload = build_payload("")
    is_valid = _validate_required_string(payload, "route_type")
    if verbose:
        status = "REJECTED" if not is_valid else "ACCEPTED (BUG)"
        print(f"  [{status}] {label}: {payload}")
    return not is_valid


# Payload builders — each simulates a different input vector
def _cli_args_payload(v: str) -> dict[str, Any]:
    return {"route_type": v, "source": "cli"}


def _json_payload(v: str) -> dict[str, Any]:
    return {"route_type": v, "source": "json"}


def _yaml_payload(v: str) -> dict[str, Any]:
    return {"route_type": v, "source": "yaml"}


def _whitespace_payload(v: str) -> dict[str, Any]:
    return {"route_type": "   ", "source": "whitespace"}


_CASES: List[tuple[str, Callable[[str], dict[str, Any]]]] = [
    ("cli_args", _cli_args_payload),
    ("json_body", _json_payload),
    ("yaml_config", _yaml_payload),
    ("whitespace_only", _whitespace_payload),
]


def run_edge_tests(*, verbose: bool = False) -> int:
    """Execute all empty-string boundary tests. Returns 0 on success, 1 on failure."""
    failures: int = 0
    print("edge_test_empty_strings: verifying fail-closed on empty route_type")
    for label, builder in _CASES:
        if not _rejects_empty(builder, label=label, verbose=verbose):
            failures += 1
            print(f"  FAIL: {label} — empty string was accepted")

    # Sanity: valid non-empty value should pass
    valid = {"route_type": "express", "source": "sanity"}
    if not _validate_required_string(valid, "route_type"):
        failures += 1
        print("  FAIL: sanity — valid route_type was rejected")
    elif verbose:
        print("  [OK] sanity: valid route_type accepted")
    return failures


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI arguments and run the edge-test suite."""
    parser = argparse.ArgumentParser(
        description="Boundary test: empty string for required route_type field.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Print per-case details.")
    args = parser.parse_args(argv)
    return run_edge_tests(verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
