#!/usr/bin/env python3
"""recorder_health_telemetry.py — Vendor recorder health telemetry daemon.

Posts heartbeat payloads (uptime, FPS, encoder backpressure, last-clip-at)
to a configurable /v1/health endpoint on a fixed interval (default 60 s).
Designed to run as a sidecar alongside the recorder process so that a
backend dashboard can surface stragglers and degraded units.

Usage
-----
    python3 -m bin.recorder_health_telemetry --endpoint http://localhost:8080
    python3 -m bin.recorder_health_telemetry --endpoint http://api.internal/v1/health \
        --interval 30 --recorder-pid 12345

Exit codes
----------
    0  — clean shutdown (SIGTERM / SIGINT)
    1  — fatal error (missing endpoint, unrecoverable I/O)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

__version__ = "1.0.0"

logger = logging.getLogger("recorder_health_telemetry")

# ---------------------------------------------------------------------------
# Signal handling — graceful shutdown
# ---------------------------------------------------------------------------

_shutdown_event = signal.Event() if hasattr(signal, "Event") else None  # type: ignore[attr-defined]


def _install_signal_handlers() -> None:
    """Register SIGTERM / SIGINT handlers that set the shutdown flag."""
    if _shutdown_event is not None:
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_args: _shutdown_event.set())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Metrics collection
# ---------------------------------------------------------------------------

def _read_proc_uptime(pid: Optional[int] = None) -> float:
    """Return process uptime in seconds.

    Falls back to wall-clock boot time when *pid* is not supplied or the
    target process cannot be inspected.
    """
    target_pid = pid if pid is not None else os.getpid()
    try:
        stat_path = f"/proc/{target_pid}/stat"
        with open(stat_path, "r", encoding="utf-8") as fh:
            parts = fh.read().split()
        # Field 22 (0-indexed 21) is starttime in clock ticks.
        clk_tck = os.sysconf(os.sysconf_names.get("SC_CLK_TCK", 2))
        starttime_ticks = int(parts[21])
        # Approximate: use process start time from /proc/uptime
        with open("/proc/uptime", "r", encoding="utf-8") as fh:
            system_uptime = float(fh.read().split()[0])
        start_seconds = system_uptime - (starttime_ticks / clk_tck)
        return max(start_seconds, 0.0)
    except (OSError, IndexError, ValueError):
        # Fallback: return 0.0 (unknown)
        return 0.0


def _collect_recorder_metrics(
    pid: Optional[int] = None,
) -> Dict[str, Any]:
    """Gather recorder health metrics from the running process.

    In a real deployment this would query the recorder's internal stats
    (e.g. via a local Unix socket, shared memory, or /proc fs).  Here we
    provide a structured skeleton that vendors can extend.

    Returns a dict with keys:
        - uptime_s: float
        - fps: float
        - encoder_backpressure: int  (0 = healthy, >0 = frames queued)
        - last_clip_at: str | None   (ISO-8601 timestamp)
    """
    uptime = _read_proc_uptime(pid)

    # --- FPS ---------------------------------------------------------------
    # Vendors typically expose this via a stats file or IPC.  Default to 0.
    fps = 0.0
    fps_path = os.environ.get("RECORDER_FPS_FILE")
    if fps_path and os.path.isfile(fps_path):
        try:
            with open(fps_path, "r", encoding="utf-8") as fh:
                fps = float(fh.read().strip())
        except (ValueError, OSError):
            logger.warning("Failed to read FPS from %s", fps_path)

    # --- Encoder backpressure ----------------------------------------------
    backpressure = 0
    bp_path = os.environ.get("RECORDER_BACKPRESSURE_FILE")
    if bp_path and os.path.isfile(bp_path):
        try:
            with open(bp_path, "r", encoding="utf-8") as fh:
                backpressure = int(fh.read().strip())
        except (ValueError, OSError):
            logger.warning("Failed to read backpressure from %s", bp_path)

    # --- Last clip timestamp -----------------------------------------------
    last_clip_at: Optional[str] = None
    clip_path = os.environ.get("RECORDER_LAST_CLIP_FILE")
    if clip_path and os.path.isfile(clip_path):
        try:
            with open(clip_path, "r", encoding="utf-8") as fh:
                last_clip_at = fh.read().strip() or None
        except OSError:
            logger.warning("Failed to read last-clip from %s", clip_path)

    return {
        "uptime_s": round(uptime, 2),
        "fps": round(fps, 2),
        "encoder_backpressure": backpressure,
        "last_clip_at": last_clip_at,
    }


# ---------------------------------------------------------------------------
# HTTP posting
# ---------------------------------------------------------------------------

def _post_health(endpoint: str, payload: Dict[str, Any], timeout: float = 10.0) -> bool:
    """POST *payload* as JSON to *endpoint*.

    Returns True on HTTP 2xx, False otherwise.  Logs errors but never raises.
    """
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if 200 <= resp.status < 300:
                logger.debug("Health POST succeeded (HTTP %d)", resp.status)
                return True
            logger.warning("Health POST returned HTTP %d", resp.status)
            return False
    except (urllib.error.URLError, OSError) as exc:
        logger.error("Health POST failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def _run_loop(
    endpoint: str,
    interval: float,
    pid: Optional[int],
) -> None:
    """Block until shutdown signal; post heartbeat every *interval* seconds."""
    logger.info(
        "Starting telemetry loop — endpoint=%s interval=%.1fs pid=%s",
        endpoint,
        interval,
        pid,
    )
    while not (_shutdown_event is not None and _shutdown_event.is_set()):
        metrics = _collect_recorder_metrics(pid)
        payload = {
            "source": os.environ.get("RECORDER_ID", "unknown"),
            "version": __version__,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "metrics": metrics,
        }
        _post_health(endpoint, payload)
        # Interruptible sleep
        if _shutdown_event is not None:
            _shutdown_event.wait(timeout=interval)
        else:
            time.sleep(interval)
    logger.info("Telemetry loop exiting on shutdown signal.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    """Entry-point with argparse CLI.

    Parameters
    ----------
    argv : list[str] | None
        Command-line arguments (defaults to sys.argv[1:]).

    Returns
    -------
    int
        Exit code (0 = success, 1 = error).
    """
    parser = argparse.ArgumentParser(
        description="Vendor recorder health telemetry daemon",
    )
    parser.add_argument(
        "--endpoint",
        required=True,
        help="Full URL of the /v1/health endpoint (e.g. http://api:8080/v1/health)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=60.0,
        help="Seconds between heartbeat posts (default: 60)",
    )
    parser.add_argument(
        "--recorder-pid",
        type=int,
        default=None,
        help="PID of the recorder process to monitor (default: this process)",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging verbosity (default: INFO)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    args = parser.parse_args(argv)

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.interval <= 0:
        logger.error("--interval must be > 0")
        return 1

    _install_signal_handlers()

    try:
        _run_loop(
            endpoint=args.endpoint,
            interval=args.interval,
            pid=args.recorder_pid,
        )
    except KeyboardInterrupt:
        # Operator-driven shutdown (Ctrl+C / SIGINT surfaced as KeyboardInterrupt
        # in the main thread). Log it so operators can distinguish a clean stop
        # from a hung process, then return 0 (clean exit).
        logger.info("Telemetry loop interrupted by operator (KeyboardInterrupt); exiting cleanly")
    except Exception as exc:
        logger.exception("Fatal error in telemetry loop: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
