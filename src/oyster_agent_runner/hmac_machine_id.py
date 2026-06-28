"""
hmac_machine_id.py — Cluster B: HMAC-rotated machine fingerprint.

Replaces raw MAC/disk-serial hashing with an HMAC-based approach to avoid
storing GDPR-regulated personal data (EDPB 2024 guidance).  The machine's
hardware identifiers are never persisted; only the HMAC digest is emitted.

Usage:
    python -m src.oyster_agent_runner.hmac_machine_id [--rotate] [--key KEY]
"""

from __future__ import annotations

import argparse
import hashlib
import hmac as _hmac
import os
import sys
import uuid
from contextlib import suppress
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_DEFAULT_KEY_ENV = "OYSTER_HMAC_KEY"
_KEY_FILE = Path(os.environ.get("OYSTER_KEY_PATH", "/etc/oyster/hmac.key"))
_ROTATION_MARKER = Path(os.environ.get("OYSTER_ROTATION_PATH", "/var/lib/oyster/rotation.seq"))
_DIGEST_ALGO = "sha256"
_HEX_LEN = 64  # sha256 hex length


# ---------------------------------------------------------------------------
# Machine-identifier collectors (stdlib only)
# ---------------------------------------------------------------------------
def _collect_raw_identifiers() -> bytes:
    """Gather stable, non-personal machine identifiers into a single byte blob.

    Sources (best-effort, any missing field is silently skipped):
      - Node UUID  (``/sys/class/dmi/id/product_uuid`` on Linux)
      - Machine-id (``/etc/machine-id`` on Linux)
      - Hostname
      - CPU count
      - A stable node identifier derived from ``uuid.getnode()`` (MAC-derived
        but immediately hashed — never stored raw).

    Returns
    -------
    bytes
        Concatenated identifier fields, each null-terminated.
    """
    parts: list[bytes] = []

    # DMI product UUID (Linux)
    for candidate in (
        Path("/sys/class/dmi/id/product_uuid"),
        Path("/etc/machine-id"),
    ):
        with suppress(OSError, PermissionError):
            parts.append(candidate.read_bytes().strip())

    parts.append(os.uname().nodename.encode())
    parts.append(str(os.cpu_count() or 0).encode())

    # uuid.getnode() may return the MAC; hash it immediately so the raw
    # value never leaves this function.
    raw_node = uuid.getnode()
    parts.append(hashlib.sha256(str(raw_node).encode()).digest())

    return b"\x00".join(parts)


# ---------------------------------------------------------------------------
# Key management
# ---------------------------------------------------------------------------
def _load_key(explicit_key: str | None = None) -> bytes:
    """Return the current HMAC signing key.

    Priority: explicit CLI arg > env var > key file > generated fallback.
    """
    if explicit_key:
        return explicit_key.encode()

    env_key = os.environ.get(_DEFAULT_KEY_ENV)
    if env_key:
        return env_key.encode()

    if _KEY_FILE.is_file():
        return _KEY_FILE.read_bytes().strip()

    # Fallback: derive a deterministic key from the machine itself.
    # In production this path should never be taken — operators must
    # provision a key via env or file.
    return hashlib.sha256(b"oyster-fallback-key-v1").digest()


def _rotation_sequence() -> int:
    """Return the current key-rotation counter (monotonically increasing)."""
    try:
        return int(_ROTATION_MARKER.read_text().strip())
    except (OSError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Core: HMAC fingerprint
# ---------------------------------------------------------------------------
def compute_machine_id(key: str | None = None) -> str:
    """Compute the HMAC-rotated machine fingerprint.

    Parameters
    ----------
    key : str, optional
        Override the HMAC key.  If *None*, resolved via ``_load_key``.

    Returns
    -------
    str
        Hex-encoded HMAC-SHA256 digest of the machine identifiers, keyed
        with the current rotation-aware secret.
    """
    signing_key = _load_key(key)
    rotation_seq = _rotation_sequence()

    # Incorporate rotation counter into the key material so that every
    # rotation produces a different fingerprint for the same hardware.
    rotated_key = hashlib.sha256(signing_key + str(rotation_seq).encode()).digest()

    message = _collect_raw_identifiers()
    digest = _hmac.new(rotated_key, message, _DIGEST_ALGO).hexdigest()
    return digest


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    """Entry-point for the HMAC machine-ID tool.

    Parameters
    ----------
    argv : list[str] | None
        Command-line arguments (defaults to ``sys.argv[1:]``).

    Returns
    -------
    int
        Exit code (0 = success).
    """
    parser = argparse.ArgumentParser(
        description="Generate / rotate HMAC-based machine fingerprints (GDPR-safe).",
    )
    parser.add_argument(
        "--key",
        default=None,
        help="Explicit HMAC key (overrides env/file).",
    )
    parser.add_argument(
        "--rotate",
        action="store_true",
        help="Increment the rotation counter and re-generate.",
    )
    parser.add_argument(
        "--show-raw",
        action="store_true",
        help="Show the raw identifier blob (debug only — do NOT log in prod).",
    )
    args = parser.parse_args(argv)

    if args.rotate:
        seq = _rotation_sequence() + 1
        try:
            _ROTATION_MARKER.parent.mkdir(parents=True, exist_ok=True)
            _ROTATION_MARKER.write_text(f"{seq}\n")
        except OSError as exc:
            print(f"[ERROR] cannot write rotation marker: {exc}", file=sys.stderr)
            return 1
        print(f"[INFO] rotation counter → {seq}")

    fingerprint = compute_machine_id(args.key)
    print(fingerprint)

    if args.show_raw:
        raw = _collect_raw_identifiers()
        print(f"[DEBUG] raw blob ({len(raw)} bytes): {raw.hex()}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
