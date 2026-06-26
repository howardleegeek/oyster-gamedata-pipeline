#!/usr/bin/env python3
"""
bin/first_run_consent.py

First-run consent flow for OysterRecorder.
Checks for existing consent, runs CLI dialog if needed, writes consent.json.

Usage:
    python -m bin.first_run_consent          # run consent flow
    python -m bin.first_run_consent --check  # check if consent exists (exit 0/1)
    python -m bin.first_run_consent --reset  # remove consent.json for re-testing
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OYSTER_DIR_NAME = ".oyster"
CONSENT_FILE_NAME = "consent.json"
CONSENT_VERSION = "v0.5.0"

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ConsentRecord:
    """Represents a user's consent choices."""

    version: str
    timestamp: str  # ISO-8601
    screen_record: bool
    upload: bool
    oauth: bool
    auto_update: bool
    telemetry: bool
    user_sig: str  # sha256(timestamp + version)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConsentRecord":
        return cls(**data)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _oyster_dir() -> Path:
    """Return the ~/.oyster directory path."""
    return Path.home() / OYSTER_DIR_NAME


def _consent_path() -> Path:
    """Return the full path to consent.json."""
    return _oyster_dir() / CONSENT_FILE_NAME


def _compute_sig(timestamp: str, version: str) -> str:
    """Compute sha256(timestamp + version) as hex digest."""
    payload = f"{timestamp}{version}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    """Return current UTC time in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------


def consent_exists(path: Optional[Path] = None) -> bool:
    """Return True if a valid consent.json already exists."""
    p = path or _consent_path()
    if not p.exists():
        return False
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Basic validation: must have required keys
        required = {
            "version",
            "timestamp",
            "screen_record",
            "upload",
            "oauth",
            "auto_update",
            "telemetry",
            "user_sig",
        }
        return required.issubset(data.keys())
    except (json.JSONDecodeError, OSError):
        return False


def load_consent(path: Optional[Path] = None) -> Optional[ConsentRecord]:
    """Load and return a ConsentRecord, or None if not found / invalid."""
    p = path or _consent_path()
    if not consent_exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return ConsentRecord.from_dict(data)
    except (json.JSONDecodeError, OSError, TypeError, KeyError):
        return None


def save_consent(record: ConsentRecord, path: Optional[Path] = None) -> Path:
    """Persist a ConsentRecord to consent.json. Returns the written path."""
    p = path or _consent_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(record.to_dict(), f, indent=2)
    return p


def build_consent(
    screen_record: bool,
    upload: bool,
    oauth: bool,
    auto_update: bool,
    telemetry: bool = False,
    version: str = CONSENT_VERSION,
    timestamp: Optional[str] = None,
) -> ConsentRecord:
    """Build a ConsentRecord with computed signature."""
    ts = timestamp or _now_iso()
    sig = _compute_sig(ts, version)
    return ConsentRecord(
        version=version,
        timestamp=ts,
        screen_record=screen_record,
        upload=upload,
        oauth=oauth,
        auto_update=auto_update,
        telemetry=telemetry,
        user_sig=sig,
    )


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------


def run_consent_flow(
    dialog_fn=None,
    consent_path: Optional[Path] = None,
) -> int:
    """
    Main entry point. Returns 0 on success, 1 if user rejects screen_record.

    Parameters
    ----------
    dialog_fn : callable | None
        Function that presents the CLI dialog and returns a dict of choices.
        If None, defaults to ``consent_dialog_cli.run_dialog``.
    consent_path : Path | None
        Override the consent.json location (useful for testing).
    """
    # Fast path: consent already given
    if consent_exists(consent_path):
        return 0

    # Import here to avoid circular deps when dialog_fn is provided
    if dialog_fn is None:
        from bin.consent_dialog_cli import run_dialog as dialog_fn

    choices = dialog_fn()

    # screen_record is mandatory — exit if rejected
    if not choices.get("screen_record", False):
        print("\n❌  Screen recording consent is required to use OysterRecorder.")
        print("   Exiting. You can re-run this tool later if you change your mind.")
        return 1

    record = build_consent(
        screen_record=choices["screen_record"],
        upload=choices.get("upload", False),
        oauth=choices.get("oauth", False),
        auto_update=choices.get("auto_update", False),
        telemetry=choices.get("telemetry", False),
    )

    save_consent(record, consent_path)
    print(f"\n✅  Consent saved to {consent_path or _consent_path()}")
    return 0


def main(argv: Optional[list] = None) -> int:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="OysterRecorder first-run consent flow")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check if consent exists and exit (0=yes, 1=no)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Remove existing consent.json (for testing)",
    )
    args = parser.parse_args(argv)

    if args.reset:
        p = _consent_path()
        if p.exists():
            p.unlink()
            print(f"Removed {p}")
        else:
            print(f"No consent file at {p}")
        return 0

    if args.check:
        return 0 if consent_exists() else 1

    return run_consent_flow()


if __name__ == "__main__":
    sys.exit(main())
