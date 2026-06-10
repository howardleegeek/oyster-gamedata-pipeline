#!/usr/bin/env python3
"""
Cluster Cost Tracker

Scans all cluster dispatch logs /tmp/cluster-*/SXX-dispatch.log (or dispatch.log),
extracts turn counts, retry counts, model info, and wall-clock duration,
then outputs dashboard/cluster_cost.json with per-spec and total cost estimates.

Usage:
    python3 daemon/cluster_cost_tracker.py --once
    python3 daemon/cluster_cost_tracker.py --dry-run
"""

import argparse
import glob
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

# Constants
TOKENS_PER_TURN = 800
USD_PER_M_TOKEN = 3.0  # qwen3.6-plus rate
CLUSTER_GLOB = "/tmp/cluster-*/dispatch.log"
OUTPUT_PATH = "dashboard/cluster_cost.json"

# Regex patterns
RE_MODEL = re.compile(r"Starting MiniMax agent \(model=([^,\)]+)")
RE_TURNS = re.compile(r"TASK RESULT: completed after (\d+) turns")
RE_429 = re.compile(r"HTTP 429")


def parse_dispatch_log(log_path: str) -> dict | None:
    """Parse a single dispatch.log and extract cost metrics.

    Returns None if the log cannot be parsed (e.g. no header line).
    """
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except (OSError, IOError):
        return None

    if not lines:
        return None

    model = None
    turns = None
    retries = 0
    spec_id = None

    for line in lines:
        # Extract model from header
        if model is None:
            m = RE_MODEL.search(line)
            if m:
                model = m.group(1)

        # Extract spec_id from header line
        if spec_id is None:
            # Header looks like: [spec_id] Starting MiniMax agent ...
            m = re.match(r"\[([^\]]+)\]\s+Starting MiniMax agent", line)
            if m:
                spec_id = m.group(1)

        # Extract turns from TASK RESULT
        if turns is None:
            m = RE_TURNS.search(line)
            if m:
                turns = int(m.group(1))

        # Count 429 retries
        if RE_429.search(line):
            retries += 1

    # Must have at least a model to be valid
    if model is None:
        return None

    # Wall clock: mtime of the file (first write → last write)
    try:
        stat = os.stat(log_path)
        wall_s = int(stat.st_mtime - stat.st_ctime)
        if wall_s < 0:
            wall_s = 0
    except OSError:
        wall_s = 0

    # If no TASK RESULT found, turns is unknown — still report with 0
    if turns is None:
        turns = 0

    estimated_tokens = turns * TOKENS_PER_TURN
    estimated_usd = round(estimated_tokens * USD_PER_M_TOKEN / 1_000_000, 4)

    return {
        "spec": spec_id or os.path.basename(os.path.dirname(log_path)),
        "model": model,
        "turns": turns,
        "retries": retries,
        "wall_s": wall_s,
        "estimated_tokens": estimated_tokens,
        "estimated_usd": estimated_usd,
    }


def scan_clusters(glob_pattern: str = CLUSTER_GLOB) -> list[dict]:
    """Scan all cluster dispatch logs and return per-spec metrics."""
    results = []
    log_files = sorted(glob.glob(glob_pattern))

    for log_path in log_files:
        entry = parse_dispatch_log(log_path)
        if entry is not None:
            results.append(entry)

    # Sort by spec name for deterministic output
    results.sort(key=lambda x: x["spec"])
    return results


def build_report(per_spec: list[dict]) -> dict:
    """Build the full JSON report with per-spec and totals."""
    totals = {
        "specs": len(per_spec),
        "turns": sum(e["turns"] for e in per_spec),
        "retries": sum(e["retries"] for e in per_spec),
        "wall_s": sum(e["wall_s"] for e in per_spec),
        "estimated_usd": round(sum(e["estimated_usd"] for e in per_spec), 4),
    }

    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "per_spec": per_spec,
        "totals": totals,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Cluster Cost Tracker")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run once and exit (no daemon loop)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print JSON to stdout instead of writing to file",
    )
    parser.add_argument(
        "--glob",
        default=CLUSTER_GLOB,
        help="Glob pattern for dispatch logs (default: %(default)s)",
    )
    parser.add_argument(
        "--output",
        default=OUTPUT_PATH,
        help="Output JSON path (default: %(default)s)",
    )
    args = parser.parse_args()

    per_spec = scan_clusters(args.glob)
    report = build_report(per_spec)

    if args.dry_run:
        print(json.dumps(report, indent=2))
    else:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
            f.write("\n")
        print(
            f"Wrote {output_path} — {report['totals']['specs']} specs, "
            f"${report['totals']['estimated_usd']} estimated"
        )

    if not args.once:
        # Daemon mode would loop here; for now just exit
        print("No --once flag: running once and exiting (daemon loop not implemented)")


if __name__ == "__main__":
    main()
