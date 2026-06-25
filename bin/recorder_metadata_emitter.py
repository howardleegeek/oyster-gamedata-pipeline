#!/usr/bin/env python3
"""recorder_metadata_emitter.py — Emit per-clip ``metadata.json``.

Buyer-spec lint criterion 22 requires a ``metadata.json`` next to the
recording with the four privacy-conscious fields below — *no PII*, no GPS,
just enough information for buyers to dedupe clips and group by recorder
machine without learning who or where the recorder is.

Schema::

    {
        "timestamp":  "2026-05-06T00:14:49+00:00",  # UTC ISO 8601
        "location":   "anonymous",
        "device_id":  "f3a1b9c8d4e57206",            # sha256(hostname)[:16]
        "session_id": "5b7e9a40-..."                 # uuid4
    }

Usage:
    python3 bin/recorder_metadata_emitter.py --clip-dir <dir>
    python3 bin/recorder_metadata_emitter.py --clip-dir <dir> --print

Exit codes:
    0 — metadata.json written successfully
    1 — invalid args / clip dir missing
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import logging
import socket
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ANONYMOUS_LOCATION: str = "anonymous"
DEVICE_ID_LENGTH: int = 16


def utc_now_iso() -> str:
    """Return the current time in UTC ISO 8601 format with offset."""
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def device_id(hostname: Optional[str] = None) -> str:
    """Return the privacy-preserving device id for the current host.

    Computed as ``sha256(hostname)[:16]`` so two captures from the same
    machine carry the same id without revealing the hostname.

    Args:
        hostname: Override for testing.  When None, uses
            :func:`socket.gethostname`.
    """
    name = hostname if hostname is not None else socket.gethostname()
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()
    return digest[:DEVICE_ID_LENGTH]


def session_id() -> str:
    """Return a fresh ``uuid4`` string for the current recording session."""
    return str(uuid.uuid4())


def build_metadata(
    *,
    hostname: Optional[str] = None,
    timestamp: Optional[str] = None,
    session: Optional[str] = None,
) -> Dict[str, Any]:
    """Return the ``metadata.json`` payload for a recorder clip.

    Args:
        hostname: Override for the hostname (used when computing device_id).
        timestamp: Pre-computed ISO timestamp (mostly for deterministic
            tests); otherwise the current UTC clock is sampled.
        session: Pre-computed session id; otherwise a new uuid4 is drawn.
    """
    return {
        "timestamp": timestamp or utc_now_iso(),
        "location": ANONYMOUS_LOCATION,
        "device_id": device_id(hostname),
        "session_id": session or session_id(),
    }


def write_metadata(clip_dir: Path, metadata: Optional[Dict[str, Any]] = None) -> Path:
    """Write metadata.json into ``clip_dir`` and return its path."""
    payload = metadata if metadata is not None else build_metadata()
    out = clip_dir / "metadata.json"
    out.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return out


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--clip-dir", type=Path, required=True, help="Recorder clip directory to emit metadata into"
    )
    parser.add_argument(
        "--print", action="store_true", help="Print the metadata JSON to stdout after write"
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point."""
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    clip_dir: Path = args.clip_dir.resolve()
    if not clip_dir.exists() or not clip_dir.is_dir():
        logger.error("Clip dir not found: %s", clip_dir)
        return 1
    payload = build_metadata()
    out = write_metadata(clip_dir, payload)
    if args.print:
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    else:
        logger.info("Wrote %s", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
