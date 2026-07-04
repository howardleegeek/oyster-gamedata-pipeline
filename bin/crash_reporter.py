#!/usr/bin/env python3
"""OysterRecorder crash-reporter daemon.

Watches ``%LOCALAPPDATA%\\OysterRecorder\\logs\\`` (or a configurable
directory) for new ``crash-*.log`` files, parses the Rust panic stack
trace, OS info, and recorder version, then:

1. Writes a summary to ``~/.oyster/crashes/``.
2. If the user has opted-in (persisted in ``~/.oyster/telemetry.json``),
   uploads the anonymized report to the backend stub via
   ``POST /api/v1/crash/dump``.

Usage
-----
    python bin/crash_reporter.py --daemon          # run as daemon
    python bin/crash_reporter.py --once            # single scan + exit
    python bin/crash_reporter.py --daemon --watch-dir /tmp/test-logs
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [crash-reporter] %(levelname)s %(message)s",
)
log = logging.getLogger("crash-reporter")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_WATCH_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", str(Path.home())),
    "OysterRecorder",
    "logs",
)
OYSTER_DIR = Path.home() / ".oyster"
CRASHES_DIR = OYSTER_DIR / "crashes"
TELEMETRY_FILE = OYSTER_DIR / "telemetry.json"
CRASH_FILE_PATTERN = re.compile(r"^crash(-.*)?\.log$")

# Rust panic patterns
PANIC_RE = re.compile(
    r"thread\s+'[^']*'\s+panicked\s+at\s+'(?P<message>[^']*)',\s+"
    r"(?P<location>\S+:\d+:\d+)"
)
VERSION_RE = re.compile(r"recorder[_-]?version[:\s=]+(?P<version>\S+)", re.IGNORECASE)
OS_RE = re.compile(r"os[:\s=]+(?P<os>[^\n]+)", re.IGNORECASE)


def _get_backend_url() -> str:
    """Get the backend URL from recorder_config (with env-var override)."""
    from bin.recorder_config import load as load_config

    cfg = load_config()
    return cfg["backend_url"]


# ---------------------------------------------------------------------------
# Telemetry consent
# ---------------------------------------------------------------------------


def _read_telemetry() -> dict[str, Any]:
    """Read the telemetry consent file."""
    if TELEMETRY_FILE.exists():
        try:
            with open(TELEMETRY_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            log.debug("Failed to read telemetry file %s: %s", TELEMETRY_FILE, exc)
    return {}


def _write_telemetry(data: dict[str, Any]) -> None:
    """Persist telemetry consent."""
    OYSTER_DIR.mkdir(parents=True, exist_ok=True)
    with open(TELEMETRY_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_consent() -> bool | None:
    """Return True if user opted-in, False if opted-out, None if undecided."""
    data = _read_telemetry()
    if "crash_upload_consent" in data:
        return bool(data["crash_upload_consent"])
    return None


def prompt_consent() -> bool:
    """Ask the user for crash-upload consent and persist the answer."""
    try:
        answer = (
            input("Send anonymized crash report to help fix? [Y/n]: ").strip().lower()
        )
    except (EOFError, OSError):
        # Non-interactive: default to no
        answer = "n"

    consent = answer not in ("n", "no")
    _write_telemetry({"crash_upload_consent": consent})
    log.info("Crash-upload consent: %s", consent)
    return consent


def ensure_consent() -> bool:
    """Return True if we have consent (prompting if needed)."""
    consent = get_consent()
    if consent is None:
        consent = prompt_consent()
    return consent


# ---------------------------------------------------------------------------
# Crash parsing
# ---------------------------------------------------------------------------


def parse_crash_file(filepath: Path) -> dict[str, str]:
    """Parse a crash log file and extract anonymized fields.

    Returns a dict with keys: panic_message, stack_trace, os_info,
    recorder_version.
    """
    text = filepath.read_text(errors="replace")

    panic_match = PANIC_RE.search(text)
    panic_message = panic_match.group("message") if panic_match else ""

    # Stack trace: everything after the panic line
    stack_trace = ""
    if panic_match:
        idx = panic_match.end()
        stack_trace = text[idx:].strip()

    version_match = VERSION_RE.search(text)
    recorder_version = version_match.group("version") if version_match else ""

    os_match = OS_RE.search(text)
    os_info = os_match.group("os") if os_match else ""

    return {
        "panic_message": panic_message,
        "stack_trace": stack_trace,
        "os_info": os_info,
        "recorder_version": recorder_version,
    }


# ---------------------------------------------------------------------------
# Crash summary writing
# ---------------------------------------------------------------------------


def write_crash_summary(parsed: dict[str, str], filename: str) -> Path:
    """Write a human-readable crash summary to ~/.oyster/crashes/."""
    CRASHES_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    summary_path = CRASHES_DIR / f"summary-{ts}-{filename}.txt"

    lines = [
        "Crash Summary",
        "=============",
        f"File: {filename}",
        f"Timestamp: {ts}",
        "",
        f"Panic: {parsed['panic_message']}",
        f"OS: {parsed['os_info']}",
        f"Recorder Version: {parsed['recorder_version']}",
        "",
        "Stack Trace:",
        "---",
        parsed["stack_trace"],
        "---",
    ]
    summary_path.write_text("\n".join(lines) + "\n")
    log.info("Wrote crash summary: %s", summary_path)
    return summary_path


# ---------------------------------------------------------------------------
# Upload to backend
# ---------------------------------------------------------------------------


def upload_crash(parsed: dict[str, str], filename: str, backend_url: str) -> bool:
    """Upload the crash report to the backend."""
    payload = {
        "panic_message": parsed["panic_message"],
        "stack_trace": parsed["stack_trace"],
        "os_info": parsed["os_info"],
        "recorder_version": parsed["recorder_version"],
        "raw_file": filename,
    }

    url = f"{backend_url}/api/v1/crash/dump"
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            result = resp.json()
            log.info("Crash uploaded successfully (id=%s)", result.get("id", "?"))
            return True
    except Exception as exc:
        log.warning("Failed to upload crash report: %s", exc)
        return False


# ---------------------------------------------------------------------------
# File processing
# ---------------------------------------------------------------------------

_processed_files: set[str] = set()


def process_crash_file(filepath: Path, backend_url: str) -> None:
    """Process a single crash file: parse, summarize, optionally upload."""
    filename = filepath.name

    if filename in _processed_files:
        return
    _processed_files.add(filename)

    log.info("Processing crash file: %s", filename)

    try:
        parsed = parse_crash_file(filepath)
    except Exception as exc:
        log.error("Failed to parse %s: %s", filename, exc)
        return

    write_crash_summary(parsed, filename)

    if ensure_consent():
        upload_crash(parsed, filename, backend_url)
    else:
        log.info("Upload skipped (user declined)")


# ---------------------------------------------------------------------------
# Watchdog integration
# ---------------------------------------------------------------------------


def _watch_directory(watch_dir: str, backend_url: str) -> None:
    """Watch a directory for new crash-*.log files using watchdog."""
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    class CrashFileHandler(FileSystemEventHandler):
        def on_created(self, event):  # type: ignore[override]
            if event.is_directory:
                return
            path = Path(event.src_path)
            if CRASH_FILE_PATTERN.match(path.name):
                # Small delay to let the file finish writing
                time.sleep(0.5)
                process_crash_file(path, backend_url)

        def on_modified(self, event):  # type: ignore[override]
            if event.is_directory:
                return
            path = Path(event.src_path)
            if (
                CRASH_FILE_PATTERN.match(path.name)
                and path.name not in _processed_files
            ):
                time.sleep(0.5)
                process_crash_file(path, backend_url)

    watch_path = Path(watch_dir)
    watch_path.mkdir(parents=True, exist_ok=True)

    handler = CrashFileHandler()
    observer = Observer()
    observer.schedule(handler, str(watch_path), recursive=False)
    observer.start()
    log.info("Watching %s for crash files...", watch_dir)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Shutting down crash-reporter daemon...")
        observer.stop()
    observer.join()


# ---------------------------------------------------------------------------
# One-shot mode
# ---------------------------------------------------------------------------


def _scan_once(watch_dir: str, backend_url: str) -> None:
    """Scan the watch directory once and exit."""
    watch_path = Path(watch_dir)
    if not watch_path.exists():
        log.warning("Watch directory does not exist: %s", watch_dir)
        return

    for f in sorted(watch_path.iterdir()):
        if f.is_file() and CRASH_FILE_PATTERN.match(f.name):
            process_crash_file(f, backend_url)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="crash_reporter.py",
        description="OysterRecorder crash-reporter daemon",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run as a background daemon (watch mode).",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Scan once and exit.",
    )
    parser.add_argument(
        "--watch-dir",
        default=DEFAULT_WATCH_DIR,
        help=f"Directory to watch for crash files (default: {DEFAULT_WATCH_DIR}).",
    )
    parser.add_argument(
        "--backend-url",
        default=None,
        help="Backend URL. Defaults to value from ~/.oyster/config.json.",
    )
    parser.add_argument(
        "--consent",
        choices=["yes", "no"],
        help="Set crash-upload consent without prompting.",
    )
    args = parser.parse_args(argv)

    # Resolve backend URL: CLI arg > recorder_config (env var + config file)
    if args.backend_url is None:
        args.backend_url = _get_backend_url()

    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    # Handle explicit consent flag
    if args.consent is not None:
        _write_telemetry({"crash_upload_consent": args.consent == "yes"})
        log.info("Consent set to: %s", args.consent == "yes")
        if not args.daemon and not args.once:
            return 0

    # Ensure directories exist
    OYSTER_DIR.mkdir(parents=True, exist_ok=True)
    CRASHES_DIR.mkdir(parents=True, exist_ok=True)

    if args.daemon:
        _watch_directory(args.watch_dir, args.backend_url)
    elif args.once:
        _scan_once(args.watch_dir, args.backend_url)
    else:
        # Default: daemon mode
        _watch_directory(args.watch_dir, args.backend_url)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
