#!/usr/bin/env python3
"""
sentry_init.py — Optional Sentry DSN integration for vendor-side error reporting.

Provides a lazy-import, zero-dependency wrapper around ``sentry_sdk`` so that
vendor pipelines can opt-in to error telemetry without forcing a hard dependency.

Usage
-----
    # Programmatic
    from bin.sentry_init import init_sentry
    init_sentry(dsn="https://key@o0.ingest.sentry.io/0", environment="prod")

    # CLI
    python -m bin.sentry_init --dsn "$SENTRY_DSN" --env prod --release v1.2.3
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Optional

logger = logging.getLogger(__name__)

_SENTRY_AVAILABLE: bool = False
_sentry_sdk: Optional[object] = None


def _load_sentry() -> bool:
    """Attempt to import ``sentry_sdk``; return ``True`` on success."""
    global _SENTRY_AVAILABLE, _sentry_sdk
    if _SENTRY_AVAILABLE:
        return True
    try:
        import sentry_sdk  # noqa: F401

        _sentry_sdk = sentry_sdk
        _SENTRY_AVAILABLE = True
        return True
    except ImportError:
        logger.warning("sentry_sdk is not installed; error reporting disabled.")
        return False


def init_sentry(
    dsn: Optional[str] = None,
    environment: Optional[str] = None,
    release: Optional[str] = None,
    traces_sample_rate: float = 0.0,
) -> bool:
    """
    Initialise Sentry error reporting.

    Parameters
    ----------
    dsn : str or None
        Sentry DSN string; falls back to ``SENTRY_DSN`` environment variable.
    environment : str or None
        Deployment environment label (e.g. ``"prod"``).
    release : str or None
        Application release identifier (e.g. ``"v1.2.3"``).
    traces_sample_rate : float
        Transaction sampling rate in ``[0.0, 1.0]``.

    Returns
    -------
    bool
        ``True`` if Sentry was initialised, ``False`` otherwise.
    """
    if not _load_sentry():
        return False

    effective_dsn = dsn or os.environ.get("SENTRY_DSN")
    if not effective_dsn:
        logger.warning("No Sentry DSN provided; skipping initialisation.")
        return False

    assert _sentry_sdk is not None
    _sentry_sdk.init(
        dsn=effective_dsn,
        environment=environment or os.environ.get("SENTRY_ENVIRONMENT"),
        release=release or os.environ.get("SENTRY_RELEASE"),
        traces_sample_rate=traces_sample_rate,
    )
    masked = effective_dsn[:12] + "…" if len(effective_dsn) > 12 else effective_dsn
    logger.info("Sentry initialised (dsn=%s, env=%s)", masked, environment)
    return True


def main(argv: Optional[list[str]] = None) -> int:
    """
    CLI entry-point.

    Parameters
    ----------
    argv : list[str] or None
        Command-line arguments (defaults to ``sys.argv[1:]``).

    Returns
    -------
    int
        Exit code: ``0`` on success, ``1`` on failure.
    """
    parser = argparse.ArgumentParser(
        description="Optional Sentry DSN integration for vendor-side error reporting.",
    )
    parser.add_argument("--dsn", default=None, help="Sentry DSN string.")
    parser.add_argument("--env", default=None, dest="environment", help="Environment label.")
    parser.add_argument("--release", default=None, help="Release identifier.")
    parser.add_argument(
        "--traces-sample-rate",
        type=float,
        default=0.0,
        help="Sampling rate in [0, 1].",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check SDK availability and exit.",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")

    args = parser.parse_args(argv)

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    if args.check:
        ok = _load_sentry()
        print(f"sentry_sdk available: {ok}")
        return 0 if ok else 1

    success = init_sentry(
        dsn=args.dsn,
        environment=args.environment,
        release=args.release,
        traces_sample_rate=args.traces_sample_rate,
    )
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
