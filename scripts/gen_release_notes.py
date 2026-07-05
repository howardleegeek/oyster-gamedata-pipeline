#!/usr/bin/env python3
"""gen_release_notes.py — Auto-generate release notes from git log + merged PRs.

Usage:
    python3 scripts/gen_release_notes.py --prev v0.4.1 --curr HEAD
    python3 scripts/gen_release_notes.py              # defaults: last tag .. HEAD

Output: markdown release notes printed to stdout.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
from dataclasses import dataclass
from datetime import date
from typing import Optional

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class Commit:
    """A single git commit with parsed conventional-commit metadata."""

    sha: str
    subject: str
    pr_number: Optional[int] = None
    scope: Optional[str] = None
    commit_type: str = "other"
    description: str = ""


@dataclass
class PRInfo:
    """Metadata for a merged PR from `gh pr list`."""

    number: int
    title: str
    author: str
    merged_at: str  # ISO-ish string


# ---------------------------------------------------------------------------
# Conventional-commit type → section mapping
# ---------------------------------------------------------------------------

SECTION_ORDER = [
    ("feat", "Features"),
    ("fix", "Fixes"),
    ("docs", "Documentation"),
    ("style", "Style"),
    ("refactor", "Refactors"),
    ("perf", "Performance"),
    ("test", "Tests"),
    ("build", "Build"),
    ("ci", "CI / Workflows"),
    ("chore", "Chores"),
    ("daemon", "Daemons"),
]

# Build a lookup: type → section name
_TYPE_TO_SECTION: dict[str, str] = {t: s for t, s in SECTION_ORDER}

# Build a reverse lookup for ordering
_SECTION_ORDER: dict[str, int] = {s: i for i, (_, s) in enumerate(SECTION_ORDER)}


# ---------------------------------------------------------------------------
# Shell helpers
# ---------------------------------------------------------------------------


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a command, return CompletedProcess. Raises on non-zero exit."""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True,
        **kwargs,
    )


def _git_log(prev: str, curr: str) -> list[str]:
    """Return raw lines from `git log --pretty` between prev and curr."""
    fmt = "%H|||%s"  # sha|||subject
    result = _run(["git", "log", "--pretty=" + fmt, f"{prev}..{curr}"])
    lines = result.stdout.strip().splitlines()
    return [line for line in lines if line.strip()]


def _gh_pr_list(curr: str) -> list[PRInfo]:
    """Return merged PRs via `gh pr list --state merged --json ...`."""
    result = _run(
        [
            "gh",
            "pr",
            "list",
            "--state",
            "merged",
            "--json",
            "number,title,author,mergedAt,headRefName",
            "--limit",
            "500",
        ]
    )
    prs: list[PRInfo] = []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return prs
    for item in data:
        prs.append(
            PRInfo(
                number=item.get("number", 0),
                title=item.get("title", ""),
                author=(
                    item.get("author", {}).get("login", "unknown")
                    if isinstance(item.get("author"), dict)
                    else "unknown"
                ),
                merged_at=item.get("mergedAt", ""),
            )
        )
    return prs


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

# Conventional commit regex: type(scope): description
_CC_RE = re.compile(
    r"^(?P<type>[a-z]+)" r"(?:\((?P<scope>[^)]+)\))?" r"(?:!)?:\s*" r"(?P<desc>.+)$"
)

# PR reference in commit subject: (#NN) or #NN at end
_PR_RE = re.compile(r"#(\d+)")

# Wave tag in commit subject: — Wave N
_WAVE_RE = re.compile(r"—\s*Wave\s+(\d+)")


def _parse_commit(raw_line: str) -> Commit:
    """Parse a single `sha|||subject` line into a Commit."""
    parts = raw_line.split("|||", 1)
    sha = parts[0].strip()
    subject = parts[1].strip() if len(parts) > 1 else ""

    commit = Commit(sha=sha, subject=subject)

    m = _CC_RE.match(subject)
    if m:
        commit.commit_type = m.group("type")
        commit.scope = m.group("scope")
        commit.description = m.group("desc")
    else:
        commit.description = subject

    # Extract PR number from subject
    pr_match = _PR_RE.search(subject)
    if pr_match:
        commit.pr_number = int(pr_match.group(1))

    return commit


def _pr_url(number: int) -> str:
    """Return a GitHub PR URL for the given number."""
    # We try to detect the repo from git remote; fallback to placeholder
    try:
        result = _run(["git", "remote", "get-url", "origin"], check=False)
        remote_url = result.stdout.strip()
        # Parse owner/repo from SSH or HTTPS URL
        m = re.search(r"[:/]([^/]+/[^/]+?)(?:\.git)?$", remote_url)
        if m:
            return f"https://github.com/{m.group(1)}/pull/{number}"
    except Exception as e:
        _LOG.debug("Could not derive PR URL from git remote for #%s: %s", number, e)
    return f"https://github.com/OWNER/REPO/pull/{number}"


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------


def _group_commits(commits: list[Commit]) -> dict[str, list[Commit]]:
    """Group commits by their conventional-commit type section."""
    groups: dict[str, list[Commit]] = {}
    for c in commits:
        section = _TYPE_TO_SECTION.get(c.commit_type, "Other")
        groups.setdefault(section, []).append(c)
    return groups


def _format_commit_line(c: Commit) -> str:
    """Format a single commit as a markdown bullet."""
    parts = []
    if c.scope:
        parts.append(f"{c.commit_type}({c.scope})")
    else:
        parts.append(c.commit_type)
    parts.append(c.description)
    line = "- " + ": ".join(parts) if len(parts) > 1 else "- " + c.description

    # Append PR link
    if c.pr_number:
        line += f" (#{c.pr_number})"

    return line


# ---------------------------------------------------------------------------
# Main generation
# ---------------------------------------------------------------------------


def generate_release_notes(
    prev_tag: str,
    curr_tag: str,
    *,
    _commits: Optional[list[Commit]] = None,
    _prs: Optional[list[PRInfo]] = None,
) -> str:
    """Generate release notes markdown.

    Parameters
    ----------
    prev_tag : str
        Previous git ref (tag, commit sha, etc.).
    curr_tag : str
        Current git ref.
    _commits : list[Commit] | None
        If provided, skip git log and use these commits directly (for testing).
    _prs : list[PRInfo] | None
        If provided, skip gh CLI and use these PRs directly (for testing).
    """
    # Gather data
    if _commits is not None:
        commits = _commits
    else:
        raw_lines = _git_log(prev_tag, curr_tag)
        commits = [_parse_commit(line) for line in raw_lines]

    if _prs is not None:
        prs = _prs
    else:
        prs = _gh_pr_list(curr_tag)

    # Build PR number → PRInfo lookup

    # Group commits
    groups = _group_commits(commits)

    # Determine version label
    version = curr_tag
    if curr_tag == "HEAD":
        # Try to derive from prev_tag + 1 or just use HEAD
        version = curr_tag

    today = date.today().isoformat()

    # Build markdown
    lines: list[str] = []
    lines.append(f"## {version} ({today})")
    lines.append("")

    # Ordered sections
    ordered_sections: list[tuple[str, str]] = list(SECTION_ORDER)
    # Add any sections not in our predefined list
    for section_name in groups:
        if section_name not in _SECTION_ORDER:
            ordered_sections.append(("other", section_name))

    for _, section_name in ordered_sections:
        if section_name not in groups:
            continue
        section_commits = groups[section_name]
        lines.append(f"### {section_name}")
        lines.append("")
        for c in section_commits:
            lines.append(_format_commit_line(c))
        lines.append("")

    # Cluster metrics
    lines.append("### Cluster metrics")
    lines.append("")
    lines.append(f"- {len(commits)} specs dispatched, {len(prs)} PRs merged")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _find_last_tag() -> str:
    """Find the most recent git tag."""
    try:
        result = _run(["git", "describe", "--tags", "--abbrev=0"], check=False)
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception as e:
        _LOG.debug("Could not determine last git tag: %s", e)
    return "HEAD"


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Generate release notes from git log + merged PRs."
    )
    parser.add_argument(
        "--prev",
        default=None,
        help="Previous git ref (tag or commit). Default: last git tag.",
    )
    parser.add_argument(
        "--curr",
        default="HEAD",
        help="Current git ref. Default: HEAD.",
    )
    args = parser.parse_args(argv)

    prev = args.prev or _find_last_tag()
    curr = args.curr

    markdown = generate_release_notes(prev, curr)
    print(markdown)


if __name__ == "__main__":
    main()
