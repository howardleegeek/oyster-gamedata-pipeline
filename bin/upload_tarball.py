#!/usr/bin/env python3
"""
upload_tarball.py — CLI wrapper around storage_backend for shell scripts.

Replaces the inline `gh release upload` calls in auto_upload_real_sample.sh
with a backend-agnostic invocation. Backend selection by --backend or env
$STORAGE_BACKEND. Defaults to "github" to preserve the legacy behaviour.

USAGE:
    bin/upload_tarball.py /tmp/swarm_real_X.tar.gz \
        --tester-id tester-001 \
        --d5-verdict REAL \
        [--sha256 ABC...]            # auto-computed if omitted
        [--backend s3|github|local]  # default $STORAGE_BACKEND or "github"
        [--notes "..."]
        [--ttl-seconds 86400]

Output (stdout, JSON, one line — easy to consume from shell):
    {"storage_url": "...", "signed_url": "...", "asset_name": "...",
     "backend": "...", "idempotent_skip": false,
     "metadata": {...}}

Exit codes:
    0 = success (incl. idempotent skip)
    1 = bad input / missing tarball
    2 = D5 verdict invalid
    3 = backend error (S3 5xx, gh failure, etc.)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Allow `python bin/upload_tarball.py` from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bin.storage_backend import (  # noqa: E402  (sys.path tweak above)
    DEFAULT_SIGNED_URL_TTL_SECONDS,
    TarballMetadata,
    compute_sha256,
    get_backend,
)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("tarball", type=Path, help="Path to the .tar.gz tarball.")
    p.add_argument("--tester-id", required=True, help="Stable tester identifier.")
    p.add_argument(
        "--d5-verdict",
        required=True,
        choices=["REAL", "MIXED", "PLACEHOLDER"],
        help="D5 authenticity classifier output.",
    )
    p.add_argument(
        "--sha256",
        default=None,
        help="Pre-computed SHA256 (skips re-hash). 64 lowercase hex chars.",
    )
    p.add_argument(
        "--backend",
        default=None,
        choices=["local", "s3", "github"],
        help="Storage backend (default: $STORAGE_BACKEND or github).",
    )
    p.add_argument("--notes", default="", help="Free-form notes recorded in metadata.")
    p.add_argument(
        "--ttl-seconds",
        type=int,
        default=DEFAULT_SIGNED_URL_TTL_SECONDS,
        help="Signed-URL TTL in seconds (default 86400 = 24h).",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Enable INFO logging on stderr.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="[%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )

    if not args.tarball.is_file():
        print(f"ERROR: tarball not found: {args.tarball}", file=sys.stderr)
        return 1

    sha256 = args.sha256 or compute_sha256(args.tarball)
    size_bytes = args.tarball.stat().st_size
    try:
        metadata = TarballMetadata(
            tester_id=args.tester_id,
            sha256=sha256,
            d5_verdict=args.d5_verdict,
            size_bytes=size_bytes,
            notes=args.notes,
        )
    except ValueError as e:
        print(f"ERROR: invalid metadata: {e}", file=sys.stderr)
        return 2

    try:
        backend = get_backend(args.backend)
    except Exception as e:
        print(f"ERROR: backend init failed: {e}", file=sys.stderr)
        return 3

    try:
        result = backend.upload(args.tarball, metadata)
    except Exception as e:
        print(f"ERROR: upload failed: {e}", file=sys.stderr)
        return 3

    # If a non-default TTL was requested, regenerate the signed URL with it.
    if args.ttl_seconds != DEFAULT_SIGNED_URL_TTL_SECONDS:
        signed = backend.get_signed_url(result.asset_name, ttl_seconds=args.ttl_seconds)
        out = result.to_dict()
        out["signed_url"] = signed
    else:
        out = result.to_dict()

    print(json.dumps(out, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
