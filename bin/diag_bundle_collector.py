#!/usr/bin/env python3
"""
G138 - Diagnostic Bundle Collector

Collects logs, system information, and recent manifests into a tarball
for customer support tickets.

Author: G138 Team
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

MODULE_VERSION = "1.0.0"
DEFAULT_LOG_DIRS = ["/var/log", "./logs"]
MANIFEST_PATTERNS = ["manifest*.json", "manifest*.yaml", "manifest*.yml"]


def get_system_info() -> dict:
    """Gather system information for diagnostic purposes."""
    info = {
        "timestamp": datetime.datetime.now().isoformat(),
        "collector_version": MODULE_VERSION,
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "python": {"version": platform.python_version(), "executable": sys.executable},
        "environment": {"cwd": os.getcwd(), "user": os.environ.get("USER", "unknown")},
    }
    # Get memory info on Linux
    try:
        if platform.system() == "Linux":
            with open("/proc/meminfo") as f:
                info["memory"] = [line.strip() for line in f.readlines()[:5]]
    except OSError as exc:
        logger.debug("diag_bundle_collector: cannot read /proc/meminfo: %s", exc)
    return info


def find_log_files(log_dirs: List[str], max_mb: int = 10) -> List[Path]:
    """Find log files in specified directories, limited by size."""
    files, max_bytes = [], max_mb * 1024 * 1024
    for d in log_dirs:
        p = Path(d)
        if not p.exists():
            continue
        for pat in ["*.log", "*.txt", "syslog*"]:
            for m in p.glob(pat):
                if m.is_file() and m.stat().st_size <= max_bytes:
                    files.append(m)
    return sorted(set(files))


def find_manifests(manifest_dir: str, limit: int = 3) -> List[Path]:
    """Find the most recent manifest files."""
    files = []
    p = Path(manifest_dir)
    if not p.exists():
        return files
    for pat in MANIFEST_PATTERNS:
        files.extend(m for m in p.glob(pat) if m.is_file())
    files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    return files[:limit]


def run_cmd_safe(cmd: List[str], timeout: int = 30) -> Optional[str]:
    """Execute a command safely, returning output or None."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.debug("diag_bundle_collector: run_cmd_safe %s failed: %s", cmd, exc)
        return None


def collect_bundle(log_dirs: List[str], manifest_dir: str, output: str) -> Path:
    """Collect all diagnostics into a tarball."""
    with tempfile.TemporaryDirectory() as tmpdir:
        col_dir = Path(tmpdir) / "diagnostics"
        col_dir.mkdir()
        # System info
        with open(col_dir / "system_info.json", "w") as f:
            json.dump(get_system_info(), f, indent=2)
        # Logs
        logs_dir = col_dir / "logs"
        logs_dir.mkdir()
        for lf in find_log_files(log_dirs):
            try:
                shutil.copy2(lf, logs_dir / lf.name)
            except OSError as exc:
                logger.debug("diag_bundle_collector: cannot copy log %s: %s", lf, exc)
        # Manifests
        man_dir = col_dir / "manifests"
        man_dir.mkdir()
        for mf in find_manifests(manifest_dir):
            try:
                shutil.copy2(mf, man_dir / mf.name)
            except OSError as exc:
                logger.debug("diag_bundle_collector: cannot copy manifest %s: %s", mf, exc)
        # Diagnostic commands output
        with open(col_dir / "diagnostics.txt", "w") as f:
            f.write(f"Collected: {datetime.datetime.now().isoformat()}\n{'='*50}\n")
            for name, cmd in [("Uname", ["uname", "-a"]), ("Date", ["date"]), ("Uptime", ["uptime"])]:
                f.write(f"\n--- {name} ---\n{run_cmd_safe(cmd) or 'N/A'}\n")
        # Create tarball
        out = Path(output)
        with tarfile.open(out, "w:gz") as tar:
            tar.add(col_dir, arcname="diagnostics")
        return out


def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point for the diagnostic bundle collector."""
    parser = argparse.ArgumentParser(
        description="Collect logs, system info, and manifests into a diagnostic tarball."
    )
    parser.add_argument("-o", "--output", help="Output tarball path (default: auto-generated)")
    parser.add_argument("-l", "--log-dir", action="append", dest="log_dirs", default=[],
                        help="Additional log directory (can repeat)")
    parser.add_argument("-m", "--manifest-dir", default="./manifests",
                        help="Manifest directory (default: ./manifests)")
    parser.add_argument("-v", "--version", action="version", version=f"%(prog)s {MODULE_VERSION}")
    args = parser.parse_args(argv)

    output = args.output or f"diag_bundle_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.tar.gz"
    log_dirs = DEFAULT_LOG_DIRS + args.log_dirs

    print(f"Collecting diagnostics to: {output}")
    try:
        result = collect_bundle(log_dirs, args.manifest_dir, output)
        print(f"Bundle created: {result} ({result.stat().st_size / 1024:.1f} KB)")
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
