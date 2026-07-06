#!/usr/bin/env python3
"""bin/telemetry.py – Anonymous daily usage telemetry (opt-in only).

Collects minimal, non-PII usage statistics and uploads them once per day
to the backend stub via ``POST /api/v1/telemetry/daily``.

Consent gate
------------
The user must have opted in via ``~/.oyster/consent.json`` with
``"telemetry": true``.  If the file is missing, malformed, or telemetry
is ``false`` / absent, **zero** network calls are made.

Payload (strict schema)
-----------------------
.. code-block:: json

    {
      "anon_id": "sha256(machine_id + os_user)",
      "version": "0.5.3",
      "os": "Windows",
      "sessions_today": 3,
      "uploads_today": 3,
      "total_session_seconds": 5400,
      "crash_today": false,
      "ts": "2025-01-15T10:30:00+00:00"
    }

Design constraints
------------------
- No PII is collected or transmitted.
- No game data, file names, or paths are included.
- No real user ID or IP address is sent.
- Network failures are silently skipped.
- Upload is fire-and-forget (async) – never blocks the recorder main thread.
- Backend returns 200 OK with no payload.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import platform
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
VERSION = "0.5.3"
CONSENT_DIR = Path.home() / ".oyster"
CONSENT_FILE = CONSENT_DIR / "consent.json"
LAST_UPLOAD_MARKER = CONSENT_DIR / ".telemetry_last_upload"
BACKEND_BASE_URL = os.environ.get("OYSTER_TELEMETRY_URL", "http://localhost:8500")
TELEMETRY_ENDPOINT = f"{BACKEND_BASE_URL}/api/v1/telemetry/daily"

# ---------------------------------------------------------------------------
# Consent helpers
# ---------------------------------------------------------------------------


def _read_consent(path: Optional[Path] = None) -> Dict[str, Any]:
    """Read and parse the consent JSON file.

    Returns an empty dict on any error (missing file, bad JSON, etc.).
    """
    target = path or CONSENT_FILE
    try:
        text = target.read_text(encoding="utf-8")
        return json.loads(text)
    except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        logger.debug("consent read failed for %s: %s", target, exc)
        return {}


def is_telemetry_opted_in(path: Optional[Path] = None) -> bool:
    """Return ``True`` only if the user has explicitly opted in to telemetry.

    The consent file must exist, be valid JSON, and contain
    ``"telemetry": true``.
    """
    data = _read_consent(path)
    return data.get("telemetry") is True


# ---------------------------------------------------------------------------
# Anonymous ID
# ---------------------------------------------------------------------------


def compute_anon_id(
    machine_id: Optional[str] = None, os_user: Optional[str] = None
) -> str:
    """Compute a deterministic anonymous identifier.

    ``anon_id = sha256(machine_id + os_user)``

    The raw inputs are **never** stored or transmitted – only the hash
    leaves the machine.
    """
    if machine_id is None:
        machine_id = platform.node() or "unknown"
    if os_user is None:
        os_user = os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"
    raw = f"{machine_id}{os_user}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Metrics collection (local, no PII)
# ---------------------------------------------------------------------------


def _read_counter_file(path: Path) -> int:
    """Read an integer counter from a single-line file. Returns 0 on error."""
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, OSError, ValueError) as exc:
        logger.debug("counter read failed for %s: %s", path, exc)
        return 0


def _write_counter_file(path: Path, value: int) -> None:
    """Write an integer counter to a single-line file."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(value), encoding="utf-8")
    except OSError as exc:
        logger.debug("counter write failed for %s: %s", path, exc)
        pass  # best-effort


def _today_marker_dir() -> Path:
    """Directory for today's local counters."""
    return CONSENT_DIR / "telemetry" / datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _session_counter_path() -> Path:
    return _today_marker_dir() / "sessions"


def _upload_counter_path() -> Path:
    return _today_marker_dir() / "uploads"


def _crash_flag_path() -> Path:
    return _today_marker_dir() / "crash"


def _total_seconds_path() -> Path:
    return _today_marker_dir() / "total_seconds"


def record_session(duration_seconds: int = 0) -> None:
    """Increment the session counter and accumulate session duration.

    Safe to call from the recorder main thread – only does local file I/O.
    """
    _write_counter_file(
        _session_counter_path(),
        _read_counter_file(_session_counter_path()) + 1,
    )
    if duration_seconds > 0:
        _write_counter_file(
            _total_seconds_path(),
            _read_counter_file(_total_seconds_path()) + duration_seconds,
        )


def record_upload() -> None:
    """Increment the upload counter."""
    _write_counter_file(
        _upload_counter_path(),
        _read_counter_file(_upload_counter_path()) + 1,
    )


def record_crash() -> None:
    """Mark that a crash occurred today."""
    _write_counter_file(_crash_flag_path(), 1)


def gather_daily_metrics() -> Dict[str, Any]:
    """Gather today's metrics into the telemetry payload dict.

    Returns a dict matching the strict schema.  No PII is included.
    """
    now = datetime.now(timezone.utc)
    return {
        "anon_id": compute_anon_id(),
        "version": VERSION,
        "os": platform.system(),
        "sessions_today": _read_counter_file(_session_counter_path()),
        "uploads_today": _read_counter_file(_upload_counter_path()),
        "total_session_seconds": _read_counter_file(_total_seconds_path()),
        "crash_today": _read_counter_file(_crash_flag_path()) > 0,
        "ts": now.isoformat(),
    }


# ---------------------------------------------------------------------------
# Upload logic
# ---------------------------------------------------------------------------


def _has_uploaded_today() -> bool:
    """Check if we already uploaded today (prevents duplicate daily uploads)."""
    try:
        marker = LAST_UPLOAD_MARKER.read_text(encoding="utf-8").strip()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return marker == today
    except (FileNotFoundError, OSError) as exc:
        logger.debug("last-upload marker read failed: %s", exc)
        return False


def _mark_uploaded_today() -> None:
    """Write today's date as the last-upload marker."""
    try:
        CONSENT_DIR.mkdir(parents=True, exist_ok=True)
        LAST_UPLOAD_MARKER.write_text(
            datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.debug("last-upload marker write failed: %s", exc)
        pass


async def _async_upload(payload: Dict[str, Any], timeout: float = 5.0) -> bool:
    """Fire-and-forget HTTP POST to the telemetry endpoint.

    Returns ``True`` on success, ``False`` on any failure (network error,
    non-200 status, timeout, etc.).  All failures are logged at DEBUG level
    and silently swallowed.
    """
    if httpx is None:
        logger.debug("httpx not installed – telemetry upload skipped")
        return False
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(TELEMETRY_ENDPOINT, json=payload)
            if resp.status_code == 200:
                logger.debug("Telemetry upload succeeded")
                return True
            else:
                logger.debug("Telemetry upload returned %d", resp.status_code)
                return False
    except Exception as exc:
        logger.debug("Telemetry upload failed (silent skip): %s", exc)
        return False


def _dispatch_upload(payload: Dict[str, Any]) -> None:
    """Run the async upload in a dedicated thread with its own event loop.

    This is the fire-and-forget mechanism that keeps the recorder main
    thread unblocked.
    """

    def _run_in_loop() -> None:
        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)
        try:
            new_loop.run_until_complete(_async_upload_and_mark(payload))
        finally:
            new_loop.close()

    t = threading.Thread(target=_run_in_loop, daemon=True)
    t.start()


def send_telemetry(
    consent_path: Optional[Path] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> bool:
    """Main entry point – check consent, gather metrics, upload async.

    This function is **non-blocking**: it schedules the HTTP POST on a
    background thread and returns immediately.

    Returns ``True`` if the upload was dispatched, ``False`` if skipped
    (no consent, already uploaded today).
    """
    # 1. Consent gate
    if not is_telemetry_opted_in(consent_path):
        logger.debug("Telemetry skipped – not opted in")
        return False

    # 2. Once-per-day gate
    if _has_uploaded_today():
        logger.debug("Telemetry skipped – already uploaded today")
        return False

    # 3. Build payload
    data = payload or gather_daily_metrics()

    # 4. Fire-and-forget async upload
    _dispatch_upload(data)
    return True


async def _async_upload_and_mark(payload: Dict[str, Any]) -> None:
    """Upload and, on success, mark today as uploaded."""
    ok = await _async_upload(payload)
    if ok:
        _mark_uploaded_today()


# ---------------------------------------------------------------------------
# CLI entry point (for manual testing / cron)
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI: force-send telemetry now (useful for testing)."""
    import argparse

    parser = argparse.ArgumentParser(description="Send daily telemetry")
    parser.add_argument(
        "--consent-file",
        type=Path,
        default=None,
        help="Override consent.json path",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore the once-per-day gate",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG)

    if args.force:
        # Temporarily clear the marker
        try:
            LAST_UPLOAD_MARKER.unlink()
        except FileNotFoundError:
            pass

    if send_telemetry(consent_path=args.consent_file):
        print("Telemetry dispatched.")
    else:
        print("Telemetry skipped (not opted in or already sent today).")


if __name__ == "__main__":
    main()
