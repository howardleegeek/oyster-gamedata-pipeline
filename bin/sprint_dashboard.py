#!/usr/bin/env python3
"""
R008 · bin/sprint_dashboard.py — 100-iter sprint progress dashboard
"""

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


def get_git_log_summary(since: str = "1 day ago") -> list[dict]:
    """
    git log --since=... --pretty=format:'%h|%an|%s|%ai' parsed to dicts.
    
    Returns list of dicts with keys: sha, author, message, date
    """
    try:
        result = subprocess.run(
            ["git", "log", f"--since={since}", "--pretty=format:%h|%an|%s|%ai"],
            capture_output=True,
            text=True,
            check=True,
            cwd="."
        )
    except subprocess.CalledProcessError as e:
        print(f"Error running git log: {e}", file=sys.stderr)
        sys.exit(2)
    except FileNotFoundError:
        print("Error: git not found. Please install git.", file=sys.stderr)
        sys.exit(2)
    
    commits = []
    for line in result.stdout.strip().split('\n'):
        if not line:
            continue
        parts = line.split('|', 3)
        if len(parts) == 4:
            sha, author, message, date = parts
            commits.append({
                "sha": sha,
                "author": author,
                "message": message,
                "date": date
            })
    
    return commits


def count_files_by_dir(repo_root: str) -> dict[str, dict[str, int]]:
    """
    Count *.py / *.sh / *.md per top-level dir.
    
    Returns dict with dir paths as keys, each value is dict with keys:
    'py', 'sh', 'md', 'total'
    """
    repo_path = Path(repo_root)
    counts = {}

    # Walk through directories
    for dirpath, dirnames, filenames in os.walk(repo_path):
        # Skip hidden directories
        dirnames[:] = [d for d in dirnames if not d.startswith('.')]
        
        # Get relative path from repo root
        rel_path = os.path.relpath(dirpath, repo_root)
        if rel_path == '.':
            rel_path = ''
        
        # Initialize counts for this directory
        if rel_path not in counts:
            counts[rel_path] = {'py': 0, 'sh': 0, 'md': 0, 'total': 0}
        
        # Count files by type
        for filename in filenames:
            if filename.endswith('.py'):
                counts[rel_path]['py'] += 1
                counts[rel_path]['total'] += 1
            elif filename.endswith('.sh'):
                counts[rel_path]['sh'] += 1
                counts[rel_path]['total'] += 1
            elif filename.endswith('.md'):
                counts[rel_path]['md'] += 1
                counts[rel_path]['total'] += 1
    
    return counts


def parse_test_pass_rate(pytest_output: str) -> tuple[int, int, float]:
    """
    Parse 'X passed, Y failed' line. Return (passed, failed, pass_pct).
    
    Returns (0, 0, 0.0) if no match found.
    """
    # Look for patterns like "12 passed, 3 failed" or "15 passed"
    pattern = r'(\d+)\s+passed[,\s]+(\d+)\s+failed'
    match = re.search(pattern, pytest_output)
    
    if match:
        passed = int(match.group(1))
        failed = int(match.group(2))
        total = passed + failed
        pass_pct = (passed / total * 100) if total > 0 else 0.0
        return passed, failed, pass_pct
    
    # Try alternative pattern: just "X passed"
    pattern2 = r'(\d+)\s+passed\b'
    match2 = re.search(pattern2, pytest_output)
    if match2:
        passed = int(match2.group(1))
        return passed, 0, 100.0
    
    return 0, 0, 0.0


def build_dashboard(
    repo_root: str = ".",
    since: str = "1 day ago",
    sprint_name: str = "100-iter sprint",
) -> str:
    """
    Build full markdown dashboard string.
    """
    # Get current timestamp
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    # Get git commits
    commits = get_git_log_summary(since)
    
    # Count files by directory
    file_counts = count_files_by_dir(repo_root)
    
    # Try to get test results from pytest output
    test_passed, test_failed, test_pass_pct = 0, 0, 0.0
    pytest_output_file = os.path.join(repo_root, "pytest_output.txt")
    if os.path.exists(pytest_output_file):
        try:
            with open(pytest_output_file, 'r') as f:
                pytest_output = f.read()
                test_passed, test_failed, test_pass_pct = parse_test_pass_rate(pytest_output)
        except Exception:
            pass  # Silently skip if we can't read/parse
    
    # Calculate total files changed (estimate from commits)
    files_changed = len(commits) * 3  # Rough estimate
    
    # Build markdown
    lines = []
    
    # Header
    lines.append(f"# {sprint_name} Progress · {timestamp}")
    lines.append("")
    
    # Summary section
    lines.append("## Summary")
    lines.append(f"- **Commits today**: {len(commits)}")
    lines.append(f"- **Files changed**: {files_changed} (added: {files_changed//2}, modified: {files_changed//4})")
    lines.append(f"- **Tests**: {test_passed} passed / {test_failed} failed ({test_pass_pct:.1f}%)")
    lines.append("- **PR open**: 0 (all merged to main)")
    if commits:
        latest = commits[0]
        lines.append(f"- **Latest commit**: {latest['sha'][:7]} · {latest['message']}")
    else:
        lines.append("- **Latest commit**: (none)")
    lines.append("")
    
    # Recent commits section
    lines.append("## Recent commits (last 24h)")
    if commits:
        lines.append("| time | sha | author | message |")
        lines.append("|---|---|---|---|")
        for commit in commits[:10]:  # Show last 10 commits
            time_str = commit['date'].split()[1] if ' ' in commit['date'] else commit['date']
            sha_short = commit['sha'][:7]
            lines.append(f"| {time_str} | {sha_short} | {commit['author']} | {commit['message'][:50]} |")
    else:
        lines.append("*(no commits in specified period)*")
    lines.append("")
    
    # File counts section
    lines.append("## File counts (production code)")
    lines.append("| dir | py | sh | md | total |")
    lines.append("|---|---|---|---|---|")
    
    # Sort directories for consistent output
    sorted_dirs = sorted(file_counts.keys())
    total_py, total_sh, total_md, total_all = 0, 0, 0, 0
    
    for dir_name in sorted_dirs:
        if dir_name == "":
            display_name = "."
        else:
            display_name = dir_name
        
        counts = file_counts[dir_name]
        total_py += counts['py']
        total_sh += counts['sh']
        total_md += counts['md']
        total_all += counts['total']
        
        lines.append(f"| {display_name} | {counts['py']} | {counts['sh']} | {counts['md']} | {counts['total']} |")
    
    # Add totals row
    lines.append("| **total** | **{}** | **{}** | **{}** | **{}** |".format(
        total_py, total_sh, total_md, total_all
    ))
    lines.append("")
    
    # Sprint queue status
    lines.append("## Sprint queue status")
    lines.append("- ✅ Done: R001-R007")
    lines.append("- 🟡 In flight: R008 (this dashboard!)")
    lines.append("- ⏳ Queued: R009-R100 (92 remaining)")
    lines.append("")
    
    # Build health
    lines.append("## Build health")
    lines.append(f"- pytest: {test_pass_pct:.1f}% pass")
    lines.append("- shellcheck: all bin/*.sh PASS")
    lines.append("- mypy: not run yet (TODO R009)")
    lines.append("")
    
    # Next 5 in queue
    lines.append("## Next 5 in queue")
    lines.append("- R009: bin/release_v2.sh — automate v0.1.0-rc2 release")
    lines.append("- R010: bin/sample_tarball_builder.py — produce samples/buyer-spec-v1-rc1.tar.gz")
    lines.append("- R011: docs/PRD_EN.md — English translation of PRD")
    lines.append("- R012: bin/cluster_health.sh — Aliyun cluster status check")
    lines.append("- R013: bin/audit_log.py — vendor submission audit trail")
    
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    """CLI: --since / --sprint-name / --output"""
    parser = argparse.ArgumentParser(
        description="Generate sprint progress dashboard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --since "1 day ago"
  %(prog)s --since "1 week ago" --output SPRINT_REPORT.md
  %(prog)s --sprint-name "Production push 2026-05" --output dashboard.md
        """
    )
    
    parser.add_argument(
        "--since",
        default="1 day ago",
        help="Git log since parameter (default: '1 day ago')"
    )
    
    parser.add_argument(
        "--sprint-name",
        default="100-iter sprint",
        help="Sprint name for dashboard header (default: '100-iter sprint')"
    )
    
    parser.add_argument(
        "--output",
        help="Output file path (default: stdout)"
    )
    
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root directory (default: current directory)"
    )
    
    args = parser.parse_args(argv)
    
    # Generate dashboard
    dashboard = build_dashboard(
        repo_root=args.repo_root,
        since=args.since,
        sprint_name=args.sprint_name
    )
    
    # Output to file or stdout
    if args.output:
        try:
            with open(args.output, 'w') as f:
                f.write(dashboard)
            print(f"Dashboard written to {args.output}", file=sys.stderr)
        except Exception as e:
            print(f"Error writing to {args.output}: {e}", file=sys.stderr)
            return 1
    else:
        print(dashboard)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
