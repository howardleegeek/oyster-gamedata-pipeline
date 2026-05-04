#!/usr/bin/env python3
"""network_throttle_test.py — Simulate weak vendor network via tc/dummynet.

Validates that upload_s3.sh (or any target script) correctly handles
slow-link conditions by applying Linux traffic-control (tc) rules that
throttle upload bandwidth to a configurable rate (default 200 Kbps).

Usage:
    python3 bin/network_throttle_test.py --bandwidth 200kbps --duration 30 \\
        --target ./upload_s3.sh --interface eth0

Requires root (tc needs CAP_NET_ADMIN).  All tc rules are cleaned up
on exit via atexit handlers.
"""

import argparse
import atexit
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import List, Optional

log = logging.getLogger(__name__)


def _run(args: List[str], *, check: bool = True) -> subprocess.CompletedProcess:
    """Run a command without shell=True."""
    log.debug("exec: %s", " ".join(args))
    return subprocess.run(args, check=check, capture_output=True, text=True)


def _tc(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Thin wrapper around the tc binary."""
    return _run(["tc"] + list(args), check=check)


def apply_throttle(interface: str, bandwidth: str, latency: str = "50ms") -> None:
    """Apply HTB qdisc + rate-limit on *interface* upload path.

    Parameters
    ----------
    interface : str — Network interface name (e.g. ``eth0``).
    bandwidth : str — Target upload bandwidth, e.g. ``200kbps``.
    latency : str — Simulated one-way latency, e.g. ``50ms``.
    """
    log.info("Applying throttle: %s up=%s latency=%s", interface, bandwidth, latency)
    _tc("qdisc", "del", "dev", interface, "root", check=False)
    _tc("qdisc", "add", "dev", interface, "root", "handle", "1:", "htb", "default", "10")
    _tc("class", "add", "dev", interface, "parent", "1:", "classid", "1:1",
        "htb", "rate", bandwidth, "ceil", bandwidth)
    _tc("qdisc", "add", "dev", interface, "parent", "1:1", "handle", "10:",
        "netem", "delay", latency, "loss", "0.1%")
    log.info("Throttle applied successfully.")


def remove_throttle(interface: str) -> None:
    """Remove all tc rules from *interface*.  Safe to call if tc is absent."""
    log.info("Removing throttle on %s", interface)
    try:
        _tc("qdisc", "del", "dev", interface, "root", check=False)
    except FileNotFoundError:
        log.debug("tc not found; skipping cleanup (likely non-Linux host).")


def run_target(target: str, args: List[str], workdir: Optional[str] = None) -> int:
    """Execute the target script under throttled conditions.

    Returns the exit code of the target process.
    """
    cmd = [str(target)] + args
    log.info("Running target: %s", " ".join(cmd))
    return subprocess.run(cmd, cwd=workdir).returncode


def main(argv: Optional[List[str]] = None) -> int:
    """Entry-point with argparse CLI.  Returns 0 on success, non-zero on failure."""
    parser = argparse.ArgumentParser(
        description="Simulate weak vendor network and validate upload scripts.",
    )
    parser.add_argument("--interface", "-i", default="eth0",
                        help="Network interface to throttle (default: eth0).")
    parser.add_argument("--bandwidth", "-b", default="200kbps",
                        help="Upload bandwidth limit, e.g. 200kbps (default: 200kbps).")
    parser.add_argument("--latency", "-l", default="50ms",
                        help="Simulated one-way latency (default: 50ms).")
    parser.add_argument("--duration", "-d", type=int, default=0,
                        help="Max duration in seconds before auto-cleanup (0 = no limit).")
    parser.add_argument("--target", "-t", default=None,
                        help="Path to the script to validate under throttle.")
    parser.add_argument("--target-args", nargs=argparse.REMAINDER, default=[],
                        help="Arguments forwarded to --target.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print tc commands without executing them.")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Enable debug logging.")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
    )

    if args.target and not Path(args.target).exists():
        log.error("Target script not found: %s", args.target)
        return 1

    tmpdir = tempfile.mkdtemp(prefix="netthrottle_")
    log.debug("Temp workdir: %s", tmpdir)
    atexit.register(remove_throttle, args.interface)

    if args.dry_run:
        log.info("[DRY RUN] Would apply throttle on %s: %s / %s",
                 args.interface, args.bandwidth, args.latency)
        if args.target:
            log.info("[DRY RUN] Would run: %s %s", args.target, " ".join(args.target_args))
        return 0

    try:
        apply_throttle(args.interface, args.bandwidth, args.latency)
    except subprocess.CalledProcessError as exc:
        log.error("Failed to apply tc rules: %s", exc.stderr.strip())
        log.error("Ensure you have root privileges (CAP_NET_ADMIN).")
        return 2
    except FileNotFoundError:
        log.error("tc binary not found — this tool requires Linux with iproute2.")
        return 2

    exit_code = 0
    if args.target:
        exit_code = run_target(args.target, args.target_args, workdir=tmpdir)
        if exit_code == 0:
            log.info("Target completed successfully under throttled conditions.")
        else:
            log.warning("Target exited with code %d under throttled conditions.", exit_code)
    else:
        log.info("No --target specified; throttle active. Press Ctrl+C to stop.")
        try:
            if args.duration > 0:
                time.sleep(args.duration)
            else:
                signal.pause()
        except KeyboardInterrupt:
            log.info("Interrupted by user.")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
