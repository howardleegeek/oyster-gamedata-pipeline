"""
Tests for scripts/auto_merge_green_prs.sh — mocks the `gh` CLI.
"""

import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "auto_merge_green_prs.sh"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ── Helpers ─────────────────────────────────────────────────────────────────

def _make_pr(
    number=1,
    title="Test PR",
    head_ref="feat/S28-cluster-thing",
    mergeable="MERGEABLE",
    merge_state_status="CLEAN",
    labels=None,
):
    """Build a single PR dict as returned by `gh pr list --json ...`."""
    if labels is None:
        labels = [{"name": "auto-merge"}]
    return {
        "number": number,
        "title": title,
        "headRefName": head_ref,
        "mergeable": mergeable,
        "mergeStateStatus": merge_state_status,
        "labels": labels,
    }


def _run_with_mock_handler(*args, prs_json="[]", checks_json="[]", merge_succeeds=True):
    """
    Run the script with a Python-based mock handler for `gh`.
    All positional args are passed to the script.
    """
    mock_gh_dir = Path(__file__).resolve().parent / "bin"
    mock_gh_dir.mkdir(parents=True, exist_ok=True)

    # Write the mock handler
    handler_path = mock_gh_dir / "gh_handler.py"
    handler_path.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env python3
            import sys, json

            prs_json = '''{prs_json}'''
            checks_json = '''{checks_json}'''
            merge_succeeds = {str(merge_succeeds)}

            args = sys.argv[1:]

            if args[0] == "pr" and args[1] == "list":
                print(prs_json)
                sys.exit(0)
            elif args[0] == "pr" and args[1] == "checks":
                print(checks_json)
                sys.exit(0)
            elif args[0] == "pr" and args[1] == "merge":
                if merge_succeeds:
                    print("Pull request successfully merged.")
                    sys.exit(0)
                else:
                    print("Merge failed.", file=sys.stderr)
                    sys.exit(1)
            elif args[0] == "pr" and args[1] == "view":
                print(prs_json)
                sys.exit(0)
            else:
                print("[]")
                sys.exit(0)
            """
        ),
        encoding="utf-8",
    )
    handler_path.chmod(0o755)

    # Write the mock gh wrapper
    mock_gh = mock_gh_dir / "gh"
    mock_gh.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import sys, subprocess, os
            handler = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gh_handler.py")
            result = subprocess.run(
                ["python3", handler] + sys.argv[1:],
                capture_output=True, text=True
            )
            sys.stdout.write(result.stdout)
            sys.stderr.write(result.stderr)
            sys.exit(result.returncode)
            """
        ),
        encoding="utf-8",
    )
    mock_gh.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = str(mock_gh_dir) + os.pathsep + env.get("PATH", "")

    cmd = ["bash", str(SCRIPT_PATH)] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=30)
    return result


# ── Tests ───────────────────────────────────────────────────────────────────

class TestDryRun:
    """--dry-run lists PRs but does not merge them."""

    def test_dry_run_lists_eligible_pr(self):
        prs = json.dumps([_make_pr(number=42, title="Ready to merge")])
        result = _run_with_mock_handler("--dry-run", prs_json=prs, checks_json="[]", merge_succeeds=True)
        assert result.returncode == 0
        assert "DRY-RUN" in result.stdout
        assert "PR#42" in result.stdout
        # Should NOT have actually called merge
        assert "MERGING" not in result.stdout

    def test_dry_run_skips_non_mergeable(self):
        prs = json.dumps([_make_pr(number=10, mergeable="CONFLICTING")])
        result = _run_with_mock_handler("--dry-run", prs_json=prs, checks_json="[]", merge_succeeds=True)
        assert result.returncode == 0
        assert "PR#10" in result.stdout
        # Should skip due to CONFLICTING, not show as DRY-RUN candidate
        assert "Would merge" not in result.stdout

    def test_dry_run_skips_dirty_state(self):
        prs = json.dumps([_make_pr(number=11, merge_state_status="DIRTY")])
        result = _run_with_mock_handler("--dry-run", prs_json=prs, checks_json="[]", merge_succeeds=True)
        assert result.returncode == 0
        assert "Would merge" not in result.stdout

    def test_dry_run_skips_wip_label(self):
        prs = json.dumps([_make_pr(number=20, labels=[{"name": "WIP"}])])
        result = _run_with_mock_handler("--dry-run", prs_json=prs, checks_json="[]", merge_succeeds=True)
        assert result.returncode == 0
        assert "WIP" in result.stdout
        assert "Would merge" not in result.stdout

    def test_dry_run_skips_do_not_merge_label(self):
        prs = json.dumps([_make_pr(number=21, labels=[{"name": "DO NOT MERGE"}])])
        result = _run_with_mock_handler("--dry-run", prs_json=prs, checks_json="[]", merge_succeeds=True)
        assert result.returncode == 0
        assert "DO NOT MERGE" in result.stdout
        assert "Would merge" not in result.stdout


class TestAutoMode:
    """--auto mode merges feat/SXX-cluster* PRs without needing the label."""

    def test_auto_mode_matches_cluster_branch(self):
        prs = json.dumps([
            _make_pr(number=55, head_ref="feat/S28-cluster-api", labels=[])
        ])
        result = _run_with_mock_handler("--auto", prs_json=prs, checks_json="[]", merge_succeeds=True)
        assert result.returncode == 0
        assert "MERGING" in result.stdout
        assert "PR#55" in result.stdout

    def test_auto_mode_rejects_non_cluster_branch(self):
        prs = json.dumps([
            _make_pr(number=56, head_ref="feat/random-thing", labels=[])
        ])
        result = _run_with_mock_handler("--auto", prs_json=prs, checks_json="[]", merge_succeeds=True)
        assert result.returncode == 0
        assert "MERGING" not in result.stdout

    def test_auto_mode_matches_s99_cluster(self):
        prs = json.dumps([
            _make_pr(number=57, head_ref="feat/S99-cluster-worker", labels=[])
        ])
        result = _run_with_mock_handler("--auto", prs_json=prs, checks_json="[]", merge_succeeds=True)
        assert result.returncode == 0
        assert "MERGING" in result.stdout

    def test_without_auto_needs_label(self):
        """Without --auto, a PR without auto-merge label should be skipped."""
        prs = json.dumps([
            _make_pr(number=60, head_ref="feat/S28-cluster-thing", labels=[])
        ])
        result = _run_with_mock_handler(prs_json=prs, checks_json="[]", merge_succeeds=True)
        assert result.returncode == 0
        assert "MERGING" not in result.stdout


class TestMaxMerges:
    """--max N limits the number of merges."""

    def test_max_one_merges_only_one(self):
        prs = json.dumps([
            _make_pr(number=70, title="First"),
            _make_pr(number=71, title="Second"),
        ])
        result = _run_with_mock_handler("--max", "1", "--auto", prs_json=prs, checks_json="[]", merge_succeeds=True)
        assert result.returncode == 0
        # Should merge PR#70 but not PR#71
        assert result.stdout.count("MERGING") == 1
        assert "PR#70" in result.stdout

    def test_max_zero_means_unlimited(self):
        prs = json.dumps([
            _make_pr(number=80),
            _make_pr(number=81),
        ])
        result = _run_with_mock_handler("--max", "0", "--auto", prs_json=prs, checks_json="[]", merge_succeeds=True)
        assert result.returncode == 0
        # Both should be merged
        assert result.stdout.count("MERGING") == 2

    def test_max_two_merges_two(self):
        prs = json.dumps([
            _make_pr(number=90),
            _make_pr(number=91),
            _make_pr(number=92),
        ])
        result = _run_with_mock_handler("--max", "2", "--auto", prs_json=prs, checks_json="[]", merge_succeeds=True)
        assert result.returncode == 0
        assert result.stdout.count("MERGING") == 2


class TestCheckGates:
    """All required checks must be GREEN."""

    def test_failing_checks_skip_pr(self):
        prs = json.dumps([_make_pr(number=100)])
        checks = json.dumps([
            {"name": "iron-law-gate", "conclusion": "FAILURE"}
        ])
        result = _run_with_mock_handler("--dry-run", prs_json=prs, checks_json=checks, merge_succeeds=True)
        assert result.returncode == 0
        assert "Would merge" not in result.stdout

    def test_pending_checks_skip_pr(self):
        prs = json.dumps([_make_pr(number=101)])
        checks = json.dumps([
            {"name": "iron-law-gate", "conclusion": "PENDING"}
        ])
        result = _run_with_mock_handler("--dry-run", prs_json=prs, checks_json=checks, merge_succeeds=True)
        assert result.returncode == 0
        assert "Would merge" not in result.stdout

    def test_passing_checks_allow_merge(self):
        prs = json.dumps([_make_pr(number=102)])
        checks = json.dumps([
            {"name": "iron-law-gate", "conclusion": "SUCCESS"}
        ])
        result = _run_with_mock_handler("--dry-run", prs_json=prs, checks_json=checks, merge_succeeds=True)
        assert result.returncode == 0
        assert "DRY-RUN" in result.stdout

    def test_no_checks_means_green(self):
        """If there are no checks, the PR is considered green."""
        prs = json.dumps([_make_pr(number=103)])
        result = _run_with_mock_handler("--dry-run", prs_json=prs, checks_json="[]", merge_succeeds=True)
        assert result.returncode == 0
        assert "DRY-RUN" in result.stdout


class TestMergeFailure:
    """Failed merges are logged to dashboard/merge_failures.log."""

    def test_failed_merge_writes_to_log(self):
        prs = json.dumps([_make_pr(number=200)])
        result = _run_with_mock_handler("--auto", prs_json=prs, checks_json="[]", merge_succeeds=False)
        assert result.returncode == 0
        assert "FAILED" in result.stdout or "FAILED" in result.stderr

        # Check the failure log
        failure_log = PROJECT_ROOT / "dashboard" / "merge_failures.log"
        if failure_log.exists():
            content = failure_log.read_text()
            assert "PR#200" in content
            assert "FAILED" in content


class TestEdgeCases:
    """Edge cases and argument validation."""

    def test_no_open_prs(self):
        result = _run_with_mock_handler(prs_json="[]", checks_json="[]", merge_succeeds=True)
        assert result.returncode == 0
        assert "No open PRs" in result.stdout

    def test_invalid_max_arg(self):
        result = _run_with_mock_handler("--max", "abc", prs_json="[]")
        assert result.returncode != 0
        assert "ERROR" in result.stderr or "error" in result.stderr.lower()

    def test_unknown_arg(self):
        result = _run_with_mock_handler("--bogus", prs_json="[]")
        assert result.returncode != 0

    def test_help_flag(self):
        result = _run_with_mock_handler("--help", prs_json="[]")
        assert result.returncode == 0
        assert "Usage" in result.stdout

    def test_mixed_eligible_and_ineligible(self):
        """A mix of eligible and ineligible PRs — only eligible ones merge."""
        prs = json.dumps([
            _make_pr(number=300, title="Good PR", labels=[{"name": "auto-merge"}]),
            _make_pr(number=301, title="WIP PR", labels=[{"name": "WIP"}]),
            _make_pr(number=302, title="Conflict PR", mergeable="CONFLICTING"),
            _make_pr(number=303, title="Another good", head_ref="feat/S10-cluster-x", labels=[]),
        ])
        result = _run_with_mock_handler("--auto", prs_json=prs, checks_json="[]", merge_succeeds=True)
        assert result.returncode == 0
        # PR#300 (has label) and PR#303 (auto mode + cluster branch) should merge
        assert result.stdout.count("MERGING") == 2


class TestSquashAndDeleteBranch:
    """Verify the script uses --squash --delete-branch."""

    def test_script_contains_squash_flag(self):
        """The script source must contain --squash --delete-branch."""
        content = SCRIPT_PATH.read_text()
        assert "--squash" in content
        assert "--delete-branch" in content

    def test_no_force_flag(self):
        """The script must NOT contain force-merge."""
        content = SCRIPT_PATH.read_text()
        assert "--force" not in content
