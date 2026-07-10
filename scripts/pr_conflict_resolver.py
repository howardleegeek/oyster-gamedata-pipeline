#!/usr/bin/env python3
"""PR Conflict Resolver — auto-rebase sibling feat/SXX-cluster branches after main merges.

Usage:
    python scripts/pr_conflict_resolver.py [--dry-run] [--only <pattern>]

Flow:
    1. gh pr list --state open --json number,headRefName
    2. For each PR: git fetch + checkout + git rebase origin/main
    3. On conflict: dump to dashboard/pr_conflicts/<PR>.diff + gh pr comment
    4. No conflict: git push --force-with-lease origin <branch>
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONFLICTS_DIR = Path("dashboard/pr_conflicts")


@dataclass
class PRInfo:
    """Minimal PR info from GitHub CLI."""

    number: int
    head_ref_name: str


@dataclass
class RebaseResult:
    """Result of attempting to rebase a single PR."""

    pr: PRInfo
    success: bool
    conflict_diff: str | None = None
    error: str | None = None


def run_cmd(
    cmd: list[str], *, check: bool = True, capture: bool = True
) -> subprocess.CompletedProcess:
    """Run a shell command, optionally capturing output."""
    return subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        check=check,
    )


def list_open_prs() -> list[PRInfo]:
    """Fetch open PRs via GitHub CLI."""
    result = run_cmd(
        ["gh", "pr", "list", "--state", "open", "--json", "number,headRefName"],
    )
    raw: list[dict[str, Any]] = json.loads(result.stdout)
    return [
        PRInfo(number=item["number"], head_ref_name=item["headRefName"]) for item in raw
    ]


def filter_prs(prs: list[PRInfo], *, only_pattern: str | None = None) -> list[PRInfo]:
    """Filter PRs by branch name pattern."""
    if only_pattern is None:
        return prs
    regex = re.compile(only_pattern)
    return [pr for pr in prs if regex.search(pr.head_ref_name)]


def ensure_conflicts_dir() -> None:
    """Create the conflicts output directory if it doesn't exist."""
    CONFLICTS_DIR.mkdir(parents=True, exist_ok=True)


def rebase_pr(pr: PRInfo, *, dry_run: bool = False) -> RebaseResult:
    """Attempt to rebase a single PR branch onto origin/main.

    Returns a RebaseResult indicating success or failure.
    """
    branch = pr.head_ref_name

    if dry_run:
        return RebaseResult(pr=pr, success=True)

    # Step 1: fetch latest
    try:
        run_cmd(["git", "fetch", "origin"])
    except subprocess.CalledProcessError as exc:
        return RebaseResult(
            pr=pr, success=False, error=f"git fetch failed: {exc.stderr.strip()}"
        )

    # Step 2: checkout the branch
    try:
        run_cmd(["git", "checkout", branch])
    except subprocess.CalledProcessError as exc:
        return RebaseResult(
            pr=pr, success=False, error=f"git checkout failed: {exc.stderr.strip()}"
        )

    # Step 3: rebase onto origin/main
    try:
        run_cmd(["git", "rebase", "origin/main"])
    except subprocess.CalledProcessError:
        # Conflict detected — capture the diff
        conflict_diff = _capture_conflict_diff()
        _write_conflict_file(pr, conflict_diff)
        _comment_on_pr(pr, conflict_diff)

        # Abort the rebase to leave repo in clean state
        try:
            run_cmd(["git", "rebase", "--abort"])
        except subprocess.CalledProcessError:
            # best effort — rebase may already be in clean state
            pass

        return RebaseResult(pr=pr, success=False, conflict_diff=conflict_diff)

    # Step 4: push with --force-with-lease
    try:
        run_cmd(["git", "push", "--force-with-lease", "origin", branch])
    except subprocess.CalledProcessError as exc:
        return RebaseResult(
            pr=pr, success=False, error=f"push failed: {exc.stderr.strip()}"
        )

    return RebaseResult(pr=pr, success=True)


def _capture_conflict_diff() -> str:
    """Capture the current conflict state as a diff string."""
    parts: list[str] = []

    # Unmerged files
    try:
        result = run_cmd(["git", "diff", "--name-only", "--diff-filter=U"])
        unmerged = result.stdout.strip().splitlines()
        parts.append(f"Unmerged files ({len(unmerged)}):")
        for f in unmerged:
            parts.append(f"  {f}")
    except subprocess.CalledProcessError as exc:
        err_msg = exc.stderr.strip() if exc.stderr else str(exc)
        parts.append(f"Could not list unmerged files: {err_msg}")

    parts.append("")
    parts.append("--- Conflict diff ---")

    # git diff for unmerged
    try:
        result = run_cmd(["git", "diff"])
        parts.append(result.stdout)
    except subprocess.CalledProcessError as exc:
        err_msg = exc.stderr.strip() if exc.stderr else str(exc)
        parts.append(f"(could not capture diff: {err_msg})")

    return "\n".join(parts)


def _write_conflict_file(pr: PRInfo, diff_content: str) -> None:
    """Write conflict details to dashboard/pr_conflicts/<PR>.diff."""
    ensure_conflicts_dir()
    filepath = CONFLICTS_DIR / f"{pr.number}.diff"
    filepath.write_text(diff_content, encoding="utf-8")
    print(f"  Conflict details written to {filepath}")


def _comment_on_pr(pr: PRInfo, diff_content: str) -> None:
    """Post a comment on the PR about the auto-rebase failure."""
    # Truncate diff for comment body if too long
    max_len = 4000
    body = (
        diff_content
        if len(diff_content) <= max_len
        else diff_content[:max_len] + "\n... (truncated)"
    )

    comment_body = (
        f"⚠️ **auto-rebase failed** for `{pr.head_ref_name}`\n\n"
        f"Conflict details:\n```\n{body}\n```\n\n"
        f"Full diff saved to `dashboard/pr_conflicts/{pr.number}.diff`"
    )

    try:
        run_cmd(
            ["gh", "pr", "comment", str(pr.number), "--body", comment_body],
        )
        print(f"  Comment posted on PR #{pr.number}")
    except subprocess.CalledProcessError as exc:
        print(f"  WARNING: failed to comment on PR #{pr.number}: {exc.stderr.strip()}")


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(
        description="Auto-rebase sibling feat/SXX-cluster PRs after main merges.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List PRs that would be rebased without making changes.",
    )
    parser.add_argument(
        "--only",
        metavar="PATTERN",
        default=None,
        help="Only process PRs whose branch name matches this regex pattern.",
    )
    args = parser.parse_args(argv)

    print("Fetching open PRs...")
    prs = list_open_prs()
    print(f"Found {len(prs)} open PR(s).")

    prs = filter_prs(prs, only_pattern=args.only)
    if args.only:
        print(f"Filtered to {len(prs)} PR(s) matching '{args.only}'.")

    if not prs:
        print("No PRs to process.")
        return 0

    if args.dry_run:
        print("\n--dry-run: would rebase the following PRs:")
        for pr in prs:
            print(f"  PR #{pr.number}  branch: {pr.head_ref_name}")
        return 0

    results: list[RebaseResult] = []
    for pr in prs:
        print(f"\nProcessing PR #{pr.number} ({pr.head_ref_name})...")
        result = rebase_pr(pr)
        results.append(result)
        if result.success:
            print("  ✅ Rebased and pushed successfully.")
        elif result.conflict_diff:
            print("  ❌ Conflict detected during rebase.")
        else:
            print(f"  ❌ Error: {result.error}")

    # Summary
    success_count = sum(1 for r in results if r.success)
    conflict_count = sum(1 for r in results if r.conflict_diff)
    error_count = sum(1 for r in results if r.error and not r.conflict_diff)

    print(f"\n{'=' * 50}")
    print(
        f"Summary: {success_count} succeeded, {conflict_count} conflicts, {error_count} errors"
    )
    print(f"{'=' * 50}")

    return 0 if (conflict_count + error_count) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
