#!/usr/bin/env python3
"""
bin/ci_health_dashboard.py

CLI that polls `gh run list`, computes pass/fail rate over last N runs,
and surfaces top-3 failure patterns. Provides observability for the
harness operator.

Usage:
    ci_health_dashboard.py [--runs N] [--repo REPO] [--json]

Dependencies:
    - stdlib only (uses subprocess to call `gh` CLI)
    - Requires GitHub CLI (`gh`) to be installed and authenticated.
"""

import argparse
import json
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class RunInfo:
    """Represents a single CI run from `gh run list`."""
    id: int
    name: str
    status: str
    conclusion: Optional[str]
    created_at: str
    head_branch: str
    event: str


def fetch_runs(repo: Optional[str], limit: int) -> List[RunInfo]:
    """
    Fetch the last N CI runs using `gh run list`.

    Args:
        repo: Repository in 'owner/repo' format. If None, uses current repo.
        limit: Number of runs to fetch.

    Returns:
        List of RunInfo objects.

    Raises:
        RuntimeError: If `gh` command fails.
    """
    cmd = ["gh", "run", "list", "--limit", str(limit), "--json",
           "id,name,status,conclusion,createdAt,headBranch,event"]
    if repo:
        cmd.extend(["--repo", repo])

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"gh run list failed: {result.stderr}")

    runs_data = json.loads(result.stdout)
    runs = []
    for item in runs_data:
        runs.append(RunInfo(
            id=item.get("id", 0),
            name=item.get("name", "unknown"),
            status=item.get("status", "unknown"),
            conclusion=item.get("conclusion"),
            created_at=item.get("createdAt", ""),
            head_branch=item.get("headBranch", ""),
            event=item.get("event", "")
        ))
    return runs


def compute_pass_fail_rate(runs: List[RunInfo]) -> Tuple[int, int, float]:
    """
    Compute pass/fail statistics from a list of runs.

    Args:
        runs: List of RunInfo objects.

    Returns:
        Tuple of (pass_count, fail_count, pass_rate).
    """
    passed = 0
    failed = 0

    for run in runs:
        if run.conclusion == "success":
            passed += 1
        elif run.conclusion in ("failure", "timed_out", "cancelled"):
            failed += 1

    total = passed + failed
    pass_rate = (passed / total * 100) if total > 0 else 0.0

    return passed, failed, pass_rate


def extract_failure_patterns(runs: List[RunInfo]) -> List[Tuple[str, int]]:
    """
    Extract top failure patterns from failed runs.

    Patterns are derived from run name + event type combinations.

    Args:
        runs: List of RunInfo objects.

    Returns:
        List of (pattern, count) tuples, sorted by count descending.
    """
    patterns = Counter()

    for run in runs:
        if run.conclusion in ("failure", "timed_out"):
            # Pattern: name + event (e.g., "CI / push")
            pattern = f"{run.name} ({run.event})"
            patterns[pattern] += 1

    return patterns.most_common(3)


def format_output(
    runs: List[RunInfo],
    passed: int,
    failed: int,
    pass_rate: float,
    patterns: List[Tuple[str, int]],
    output_json: bool
) -> str:
    """
    Format the dashboard output as either JSON or human-readable text.

    Args:
        runs: List of RunInfo objects.
        passed: Number of passed runs.
        failed: Number of failed runs.
        pass_rate: Pass rate percentage.
        patterns: Top failure patterns.
        output_json: If True, output JSON format.

    Returns:
        Formatted output string.
    """
    if output_json:
        data = {
            "total_runs": len(runs),
            "passed": passed,
            "failed": failed,
            "pass_rate": round(pass_rate, 2),
            "top_failure_patterns": [
                {"pattern": p, "count": c} for p, c in patterns
            ]
        }
        return json.dumps(data, indent=2)

    lines = [
        "=" * 50,
        "CI Health Dashboard",
        "=" * 50,
        f"Total Runs Analyzed: {len(runs)}",
        f"Passed: {passed}",
        f"Failed: {failed}",
        f"Pass Rate: {pass_rate:.1f}%",
        "",
        "Top Failure Patterns:",
    ]

    if patterns:
        for i, (pattern, count) in enumerate(patterns, 1):
            lines.append(f"  {i}. {pattern} - {count} failures")
    else:
        lines.append("  No failures detected.")

    lines.append("=" * 50)
    return "\n".join(lines)


def main(argv: List[str]) -> int:
    """
    Main entry point for the CI health dashboard CLI.

    Args:
        argv: Command-line arguments (excluding program name).

    Returns:
        Exit code (0 for success, non-zero for errors).
    """
    parser = argparse.ArgumentParser(
        description="CI Health Dashboard - Analyze GitHub Actions run history."
    )
    parser.add_argument(
        "--runs", "-n",
        type=int,
        default=20,
        help="Number of recent runs to analyze (default: 20)"
    )
    parser.add_argument(
        "--repo", "-r",
        type=str,
        default=None,
        help="Repository in 'owner/repo' format (default: current repo)"
    )
    parser.add_argument(
        "--json", "-j",
        action="store_true",
        help="Output results in JSON format"
    )

    args = parser.parse_args(argv)

    try:
        # Fetch runs
        runs = fetch_runs(args.repo, args.runs)

        if not runs:
            print("No runs found.", file=sys.stderr)
            return 1

        # Compute statistics
        passed, failed, pass_rate = compute_pass_fail_rate(runs)

        # Extract failure patterns
        patterns = extract_failure_patterns(runs)

        # Output results
        output = format_output(runs, passed, failed, pass_rate,
                               patterns, args.json)
        print(output)

        return 0

    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        print(f"Error parsing gh output: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))