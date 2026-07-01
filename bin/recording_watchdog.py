#!/usr/bin/env python3
"""recording_watchdog — Monitor a live recording session for stalls.

Watches the active session directory while OysterPlay is recording. Polls
recording.mp4 size and game_state.jsonl line count every WATCH_INTERVAL_SEC.
If either stops growing for more than STALL_THRESHOLD_SEC, writes a
.stall_warning marker the user can see immediately + logs a structured event
to recording_watchdog.log.

Why: empirical sessions on minipc1 showed both mp4 + game_state usually work
together, but failures (truncated mp4, mod silent failure) are silent — tester
finishes a 30-min play session, finds output is unusable. This watchdog
surfaces stalls in real-time so the tester can stop + restart instead of
wasting 30 minutes.

Designed to run as a sidecar subprocess of OysterPlay.exe. Pure stdlib.

CLI:
    python recording_watchdog.py <session_dir> [--interval 5] [--stall 15]

Exit codes:
    0 — clean exit (session finished successfully, no stalls)
    1 — stall detected, .stall_warning written
    2 — session dir didn't exist OR became unreadable
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_INTERVAL_SEC = 5
DEFAULT_STALL_THRESHOLD_SEC = 15
WATCHED_FILES = ("recording.mp4", "game_state.jsonl", "inputs.jsonl")


# ---------------------------------------------------------------------------
# State tracking
# ---------------------------------------------------------------------------


@dataclass
class FileHealth:
    name: str
    last_size: int = 0
    last_change_ts: float = field(default_factory=time.time)
    last_lines: int = 0  # only meaningful for .jsonl files
    stall_warned: bool = False

    def update(self, current_size: int, current_lines: int = 0) -> bool:
        """Return True if this file is healthy (growing or just unchanged), False if stalled."""
        now = time.time()
        size_changed = current_size > self.last_size
        lines_changed = current_lines > self.last_lines
        if size_changed or lines_changed:
            self.last_size = current_size
            self.last_lines = current_lines
            self.last_change_ts = now
            return True
        # No change — check whether we're past the stall threshold
        return True  # leave the caller to compute stall duration

    def stall_duration_sec(self) -> float:
        return time.time() - self.last_change_ts


def count_lines_fast(path: Path) -> int:
    """Quick line count via byte chunks. Doesn't load whole file."""
    if not path.exists():
        return 0
    try:
        count = 0
        with path.open("rb") as fp:
            while True:
                chunk = fp.read(65536)
                if not chunk:
                    break
                count += chunk.count(b"\n")
        return count
    except OSError:
        return 0


# ---------------------------------------------------------------------------
# Watchdog loop
# ---------------------------------------------------------------------------


def watch_session(
    session_dir: Path,
    interval_sec: float,
    stall_threshold_sec: float,
) -> int:
    """Run the watchdog loop. Returns exit code."""
    log_path = session_dir / "recording_watchdog.log"
    warning_path = session_dir / ".stall_warning"

    if not session_dir.exists():
        print(f"FATAL: session dir does not exist: {session_dir}", file=sys.stderr)
        return 2

    files = {name: FileHealth(name=name) for name in WATCHED_FILES}
    start_ts = time.time()
    iteration = 0
    saw_any_stall = False
    stop_signaled = False

    def _on_signal(_sig, _frame):
        nonlocal stop_signaled
        stop_signaled = True

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    print(f"[watchdog] start: {session_dir}")
    print(f"[watchdog] interval={interval_sec}s stall_threshold={stall_threshold_sec}s")
    print(f"[watchdog] watching: {', '.join(WATCHED_FILES)}")
    print()

    with log_path.open("a", encoding="utf-8") as logf:
        logf.write(
            json.dumps({
                "ts": start_ts, "event": "watchdog_start",
                "interval_sec": interval_sec, "stall_threshold_sec": stall_threshold_sec,
            }) + "\n",
        )
        logf.flush()

        while not stop_signaled:
            iteration += 1
            tick_stalls: list[str] = []

            for name, health in files.items():
                path = session_dir / name
                size = path.stat().st_size if path.exists() else 0
                lines = count_lines_fast(path) if name.endswith(".jsonl") else 0

                # Update healh tracker
                changed = (size > health.last_size) or (lines > health.last_lines)
                if changed:
                    health.last_size = size
                    health.last_lines = lines
                    health.last_change_ts = time.time()
                    if health.stall_warned:
                        # Recovered from stall
                        msg = f"recovered: {name} resumed growing after {health.stall_duration_sec():.1f}s"
                        print(f"  ✓ {msg}")
                        logf.write(json.dumps({
                            "ts": time.time(), "event": "stall_recovered", "file": name,
                        }) + "\n")
                        health.stall_warned = False

                dur = health.stall_duration_sec()
                if dur > stall_threshold_sec and not health.stall_warned:
                    health.stall_warned = True
                    saw_any_stall = True
                    msg = f"STALLED: {name} no growth for {dur:.1f}s (size={size} lines={lines})"
                    tick_stalls.append(msg)
                    print(f"  ⚠ {msg}")
                    logf.write(json.dumps({
                        "ts": time.time(), "event": "stall_detected", "file": name,
                        "stall_sec": dur, "size": size, "lines": lines,
                    }) + "\n")

            # Status print every 12 iterations (~1 min @ 5s)
            if iteration % 12 == 0:
                ages = {n: int(time.time() - h.last_change_ts) for n, h in files.items()}
                print(f"  [iter {iteration}] elapsed={int(time.time()-start_ts)}s ages={ages}")

            # Write the .stall_warning marker on first stall
            if tick_stalls and not warning_path.exists():
                warning_path.write_text("\n".join(tick_stalls) + "\n", encoding="utf-8")
                print(f"  ⚠ wrote {warning_path}")

            logf.flush()
            time.sleep(interval_sec)

        logf.write(json.dumps({
            "ts": time.time(), "event": "watchdog_stop",
            "elapsed_sec": time.time() - start_ts, "saw_stall": saw_any_stall,
        }) + "\n")

    print()
    print(f"[watchdog] stopped after {time.time() - start_ts:.1f}s")
    print(f"[watchdog] saw_stall={saw_any_stall}")
    return 1 if saw_any_stall else 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("session_dir", type=Path,
                    help="Session directory to monitor")
    ap.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SEC,
                    help=f"Polling interval seconds (default: {DEFAULT_INTERVAL_SEC})")
    ap.add_argument("--stall", type=float, default=DEFAULT_STALL_THRESHOLD_SEC,
                    help=f"Stall threshold seconds (default: {DEFAULT_STALL_THRESHOLD_SEC})")
    args = ap.parse_args(argv)

    return watch_session(args.session_dir, args.interval, args.stall)


if __name__ == "__main__":
    sys.exit(main())
