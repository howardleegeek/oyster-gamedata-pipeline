"""
defense_systeminfo_required.py — Blue-team schema guard for G095 systeminfo payloads.

Provides:
  - REQUIRED_KEYS: authoritative list of mandatory systeminfo fields.
  - validate_systeminfo(): pydantic-lite validator (stdlib-only, no hard dep).
  - CLI entry-point via main(argv) for standalone validation.

Usage:
  python -m oyster_agent_runner.defense_systeminfo_required --file info.json
  python -m oyster_agent_runner.defense_systeminfo_required --stdin
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

# ---------------------------------------------------------------------------
# Schema definition
# ---------------------------------------------------------------------------

REQUIRED_KEYS: list[str] = [
    "hostname",
    "os_name",
    "os_version",
    "architecture",
    "cpu_count",
    "memory_bytes",
    "disk_total_bytes",
    "ip_addresses",
    "mac_addresses",
    "kernel_version",
    "uptime_seconds",
    "timezone",
]

# Expected Python types for each required key (used by the validator).
_KEY_TYPES: dict[str, type] = {
    "hostname": str,
    "os_name": str,
    "os_version": str,
    "architecture": str,
    "cpu_count": int,
    "memory_bytes": int,
    "disk_total_bytes": int,
    "ip_addresses": list,
    "mac_addresses": list,
    "kernel_version": str,
    "uptime_seconds": (int, float),
    "timezone": str,
}

# ---------------------------------------------------------------------------
# Pydantic-lite validator (stdlib only)
# ---------------------------------------------------------------------------


def validate_systeminfo(
    payload: dict[str, Any],
    *,
    strict: bool = False,
) -> tuple[bool, list[str]]:
    """Validate a systeminfo dict against REQUIRED_KEYS and type hints.

    Args:
        payload: Parsed JSON dict to validate.
        strict: When True, reject any keys beyond REQUIRED_KEYS.

    Returns:
        (is_valid, errors) — a tuple of bool and list of error strings.
    """
    errors: list[str] = []

    # 1. Missing required keys
    for key in REQUIRED_KEYS:
        if key not in payload:
            errors.append(f"missing_required_key:{key}")

    # 2. Type checks for present keys
    for key, expected_type in _KEY_TYPES.items():
        if key not in payload:
            continue
        value = payload[key]
        if not isinstance(value, expected_type):
            errors.append(
                f"type_mismatch:{key}:expected={expected_type}:got={type(value).__name__}"
            )

    # 3. Value-level sanity checks
    if (
        "cpu_count" in payload
        and isinstance(payload["cpu_count"], int)
        and payload["cpu_count"] < 1
    ):
        errors.append("value_error:cpu_count:must_be_positive")
    if (
        "memory_bytes" in payload
        and isinstance(payload["memory_bytes"], int)
        and payload["memory_bytes"] < 0
    ):
        errors.append("value_error:memory_bytes:must_be_non_negative")
    if (
        "uptime_seconds" in payload
        and isinstance(payload["uptime_seconds"], (int, float))
        and payload["uptime_seconds"] < 0
    ):
        errors.append("value_error:uptime_seconds:must_be_non_negative")

    # 4. Strict mode — reject extra keys
    if strict:
        extra = set(payload.keys()) - set(REQUIRED_KEYS)
        if extra:
            errors.append(f"extra_keys:{','.join(sorted(extra))}")

    return (len(errors) == 0, errors)


# ---------------------------------------------------------------------------
# Optional pydantic model (lazy import — only used if pydantic is available)
# ---------------------------------------------------------------------------


def _try_pydantic_model() -> Any | None:
    """Attempt to build a pydantic BaseModel for systeminfo (lazy import).

    Returns None if pydantic is not installed.
    """
    try:
        from pydantic import BaseModel, Field  # noqa: F401 — lazy import
    except ImportError:
        return None

    class SystemInfoModel(BaseModel):
        hostname: str
        os_name: str
        os_version: str
        architecture: str
        cpu_count: int = Field(ge=1)
        memory_bytes: int = Field(ge=0)
        disk_total_bytes: int = Field(ge=0)
        ip_addresses: list[str]
        mac_addresses: list[str]
        kernel_version: str
        uptime_seconds: float = Field(ge=0)
        timezone: str

    return SystemInfoModel


def validate_with_pydantic(payload: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate using pydantic if available, else fall back to stdlib validator."""
    model_cls = _try_pydantic_model()
    if model_cls is None:
        return validate_systeminfo(payload)
    try:
        model_cls(**payload)
        return (True, [])
    except Exception as exc:
        return (False, [str(exc)])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI entry-point for systeminfo validation.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).

    Returns:
        Exit code: 0 on valid, 1 on invalid, 2 on usage/IO error.
    """
    parser = argparse.ArgumentParser(
        description="Validate a systeminfo JSON payload against REQUIRED_KEYS schema.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--file", type=str, help="Path to JSON file to validate.")
    group.add_argument("--stdin", action="store_true", help="Read JSON from stdin.")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Reject keys not in REQUIRED_KEYS.",
    )
    parser.add_argument(
        "--pydantic",
        action="store_true",
        help="Use pydantic validator if available.",
    )
    args = parser.parse_args(argv)

    # Load payload
    try:
        if args.stdin:
            raw = sys.stdin.read()
        else:
            with open(args.file, encoding="utf-8") as fh:
                raw = fh.read()
        payload: dict[str, Any] = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: failed to load JSON — {exc}", file=sys.stderr)
        return 2

    if not isinstance(payload, dict):
        print("ERROR: top-level JSON must be an object/dict.", file=sys.stderr)
        return 2

    # Validate
    if args.pydantic:
        is_valid, errors = validate_with_pydantic(payload)
    else:
        is_valid, errors = validate_systeminfo(payload, strict=args.strict)

    if is_valid:
        print("OK: systeminfo payload is valid.")
        return 0
    else:
        print("FAIL: systeminfo payload has errors:")
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
