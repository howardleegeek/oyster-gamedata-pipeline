#!/usr/bin/env python3
"""
Extract release notes from git commits between two references.
Groups commits by type (feat/fix/docs/test) and outputs formatted release notes.
"""

import argparse
import subprocess
import sys
from collections import defaultdict
from typing import Dict, List, Tuple


def run_git_log(since_ref: str, until_ref: str = "HEAD") -> str:
    """Run git log and return output."""
    cmd = ["git", "log", "--oneline", "--no-merges", f"{since_ref}..{until_ref}"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f"Error running git log: {e}", file=sys.stderr)
        sys.exit(1)


def parse_commits(git_log_output: str) -> List[Tuple[str, str]]:
    """Parse git log output into list of (type, message) tuples."""
    commits = []
    for line in git_log_output.strip().split("\n"):
        if not line:
            continue

        # Extract commit message (skip hash)
        parts = line.split(" ", 1)
        if len(parts) < 2:
            continue

        message = parts[1]

        # Extract type from conventional commit format
        # Format: type(scope): description
        type_part = message.split(":", 1)[0].lower()

        # Check for common types
        commit_type = "other"
        for t in ["feat", "fix", "docs", "test"]:
            if type_part.startswith(t):
                commit_type = t
                break

        commits.append((commit_type, message))

    return commits


def group_commits(commits: List[Tuple[str, str]]) -> Dict[str, List[str]]:
    """Group commits by type."""
    grouped = defaultdict(list)
    for commit_type, message in commits:
        grouped[commit_type].append(message)
    return dict(grouped)


def format_release_notes(grouped_commits: Dict[str, List[str]]) -> str:
    """Format grouped commits into release notes."""
    output_lines = []

    # Define order and labels for types
    type_order = ["feat", "fix", "docs", "test", "other"]
    type_labels = {
        "feat": "Features",
        "fix": "Bug Fixes",
        "docs": "Documentation",
        "test": "Tests",
        "other": "Other Changes",
    }

    for commit_type in type_order:
        if commit_type in grouped_commits and grouped_commits[commit_type]:
            output_lines.append(f"### {type_labels[commit_type]}")
            for message in grouped_commits[commit_type]:
                output_lines.append(f"- {message}")
            output_lines.append("")

    return "\n".join(output_lines).strip()


def main():
    parser = argparse.ArgumentParser(description="Extract release notes from git commits")
    parser.add_argument(
        "--since-ref", default="origin/main", help="Starting reference (default: origin/main)"
    )
    parser.add_argument("--output", help="Output file (default: stdout)")

    args = parser.parse_args()

    # Get git log output
    git_log = run_git_log(args.since_ref)

    if not git_log.strip():
        print(f"No commits found between {args.since_ref} and HEAD", file=sys.stderr)
        sys.exit(0)

    # Parse and group commits
    commits = parse_commits(git_log)
    grouped = group_commits(commits)

    # Format release notes
    release_notes = format_release_notes(grouped)

    # Output results
    if args.output:
        with open(args.output, "w") as f:
            f.write(release_notes)
        print(f"Release notes written to {args.output}", file=sys.stderr)
    else:
        print(release_notes)

    sys.exit(0)


if __name__ == "__main__":
    main()
