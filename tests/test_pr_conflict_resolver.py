"""Tests for scripts/pr_conflict_resolver.py — all git/gh calls are mocked."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, call, patch

from scripts.pr_conflict_resolver import (
    CONFLICTS_DIR,
    PRInfo,
    _capture_conflict_diff,
    _comment_on_pr,
    _write_conflict_file,
    filter_prs,
    list_open_prs,
    main,
    rebase_pr,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_PRS = [
    {"number": 101, "headRefName": "feat/S01-cluster"},
    {"number": 102, "headRefName": "feat/S02-cluster"},
    {"number": 103, "headRefName": "feat/S40-pr-conflict-resolver"},
    {"number": 200, "headRefName": "docs/readme-update"},
]


def _make_completed_process(stdout: str = "", stderr: str = "", returncode: int = 0) -> MagicMock:
    """Helper to create a mock CompletedProcess."""
    m = MagicMock(spec=subprocess.CompletedProcess)
    m.stdout = stdout
    m.stderr = stderr
    m.returncode = returncode
    return m


# ---------------------------------------------------------------------------
# list_open_prs
# ---------------------------------------------------------------------------


class TestListOpenPrs:
    def test_returns_pr_list(self):
        with patch("scripts.pr_conflict_resolver.run_cmd") as mock_run:
            mock_run.return_value = _make_completed_process(
                stdout=json.dumps(SAMPLE_PRS),
            )
            result = list_open_prs()

        assert len(result) == 4
        assert result[0] == PRInfo(number=101, head_ref_name="feat/S01-cluster")
        assert result[2] == PRInfo(number=103, head_ref_name="feat/S40-pr-conflict-resolver")
        mock_run.assert_called_once_with(
            ["gh", "pr", "list", "--state", "open", "--json", "number,headRefName"],
        )

    def test_empty_list(self):
        with patch("scripts.pr_conflict_resolver.run_cmd") as mock_run:
            mock_run.return_value = _make_completed_process(stdout="[]")
            result = list_open_prs()

        assert result == []


# ---------------------------------------------------------------------------
# filter_prs
# ---------------------------------------------------------------------------


class TestFilterPrs:
    def test_no_filter_returns_all(self):
        prs = [PRInfo(number=1, head_ref_name="feat/S01-cluster")]
        assert filter_prs(prs) == prs

    def test_pattern_matches_subset(self):
        prs = [
            PRInfo(number=1, head_ref_name="feat/S01-cluster"),
            PRInfo(number=2, head_ref_name="feat/S02-cluster"),
            PRInfo(number=3, head_ref_name="docs/readme"),
        ]
        result = filter_prs(prs, only_pattern=r"S01")
        assert len(result) == 1
        assert result[0].number == 1

    def test_pattern_matches_multiple(self):
        prs = [
            PRInfo(number=1, head_ref_name="feat/S01-cluster"),
            PRInfo(number=2, head_ref_name="feat/S02-cluster"),
            PRInfo(number=3, head_ref_name="docs/readme"),
        ]
        result = filter_prs(prs, only_pattern=r"S\d\d")
        assert len(result) == 2

    def test_pattern_no_match(self):
        prs = [PRInfo(number=1, head_ref_name="feat/S01-cluster")]
        result = filter_prs(prs, only_pattern=r"nonexistent")
        assert result == []


# ---------------------------------------------------------------------------
# rebase_pr — dry_run
# ---------------------------------------------------------------------------


class TestRebasePrDryRun:
    def test_dry_run_returns_success(self):
        pr = PRInfo(number=101, head_ref_name="feat/S01-cluster")
        result = rebase_pr(pr, dry_run=True)

        assert result.success is True
        assert result.conflict_diff is None
        assert result.error is None


# ---------------------------------------------------------------------------
# rebase_pr — success path
# ---------------------------------------------------------------------------


class TestRebasePrSuccess:
    def test_successful_rebase_and_push(self):
        pr = PRInfo(number=101, head_ref_name="feat/S01-cluster")

        with patch("scripts.pr_conflict_resolver.run_cmd") as mock_run:
            mock_run.return_value = _make_completed_process()
            result = rebase_pr(pr)

        assert result.success is True
        assert result.conflict_diff is None
        assert result.error is None

        # Verify the sequence of commands
        calls = mock_run.call_args_list
        assert calls[0] == call(["git", "fetch", "origin"])
        assert calls[1] == call(["git", "checkout", "feat/S01-cluster"])
        assert calls[2] == call(["git", "rebase", "origin/main"])
        assert calls[3] == call(["git", "push", "--force-with-lease", "origin", "feat/S01-cluster"])


# ---------------------------------------------------------------------------
# rebase_pr — conflict path
# ---------------------------------------------------------------------------


class TestRebasePrConflict:
    def test_conflict_captures_diff_and_aborts(self, tmp_path, monkeypatch):
        """When rebase fails, conflict diff is captured and rebase is aborted."""
        pr = PRInfo(number=101, head_ref_name="feat/S01-cluster")

        # Change to tmp_path so CONFLICTS_DIR is created there
        monkeypatch.chdir(tmp_path)

        def side_effect(cmd, **kwargs):
            if cmd == ["git", "rebase", "origin/main"]:
                raise subprocess.CalledProcessError(1, cmd, stderr="CONFLICT")
            if cmd == ["git", "diff", "--name-only", "--diff-filter=U"]:
                return _make_completed_process(stdout="src/file1.py\nsrc/file2.py")
            if cmd == ["git", "diff"]:
                return _make_completed_process(
                    stdout="diff --git a/src/file1.py\n+++ b/src/file1.py\n@@ -1,3 +1,3 @@\n- old\n+ new\n<<<<<<< HEAD\nmain\n=======\nbranch\n>>>>>>> feat/S01-cluster"
                )
            if cmd == ["git", "rebase", "--abort"]:
                return _make_completed_process()
            return _make_completed_process()

        with patch("scripts.pr_conflict_resolver.run_cmd", side_effect=side_effect):
            result = rebase_pr(pr)

        assert result.success is False
        assert result.conflict_diff is not None
        assert "Unmerged files" in result.conflict_diff
        assert result.error is None

        # Verify conflict file was written
        conflict_file = tmp_path / CONFLICTS_DIR / "101.diff"
        assert conflict_file.exists()
        content = conflict_file.read_text()
        assert "Unmerged files" in content

    def test_conflict_comments_on_pr(self, tmp_path, monkeypatch):
        """When rebase fails, a comment is posted on the PR."""
        pr = PRInfo(number=101, head_ref_name="feat/S01-cluster")
        monkeypatch.chdir(tmp_path)

        comment_posted = False

        def side_effect(cmd, **kwargs):
            nonlocal comment_posted
            if cmd == ["git", "rebase", "origin/main"]:
                raise subprocess.CalledProcessError(1, cmd, stderr="CONFLICT")
            if cmd == ["git", "diff", "--name-only", "--diff-filter=U"]:
                return _make_completed_process(stdout="src/file1.py")
            if cmd == ["git", "diff"]:
                return _make_completed_process(stdout="conflict here")
            if cmd == ["git", "rebase", "--abort"]:
                return _make_completed_process()
            if cmd[:3] == ["gh", "pr", "comment"]:
                comment_posted = True
                return _make_completed_process()
            return _make_completed_process()

        with patch("scripts.pr_conflict_resolver.run_cmd", side_effect=side_effect):
            rebase_pr(pr)

        assert comment_posted is True


# ---------------------------------------------------------------------------
# rebase_pr — error paths
# ---------------------------------------------------------------------------


class TestRebasePrErrors:
    def test_fetch_failure(self):
        pr = PRInfo(number=101, head_ref_name="feat/S01-cluster")

        with patch("scripts.pr_conflict_resolver.run_cmd") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                1, ["git", "fetch", "origin"], stderr="network error"
            )
            result = rebase_pr(pr)

        assert result.success is False
        assert "git fetch failed" in result.error

    def test_checkout_failure(self):
        pr = PRInfo(number=101, head_ref_name="feat/S01-cluster")

        call_count = 0

        def side_effect(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_completed_process()  # fetch ok
            raise subprocess.CalledProcessError(1, cmd, stderr="branch not found")

        with patch("scripts.pr_conflict_resolver.run_cmd", side_effect=side_effect):
            result = rebase_pr(pr)

        assert result.success is False
        assert "git checkout failed" in result.error

    def test_push_failure(self):
        pr = PRInfo(number=101, head_ref_name="feat/S01-cluster")

        call_count = 0

        def side_effect(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 4:  # push is 4th call
                raise subprocess.CalledProcessError(1, cmd, stderr="rejected")
            return _make_completed_process()

        with patch("scripts.pr_conflict_resolver.run_cmd", side_effect=side_effect):
            result = rebase_pr(pr)

        assert result.success is False
        assert "push failed" in result.error


# ---------------------------------------------------------------------------
# _write_conflict_file
# ---------------------------------------------------------------------------


class TestWriteConflictFile:
    def test_creates_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        pr = PRInfo(number=42, head_ref_name="feat/S42-cluster")
        _write_conflict_file(pr, "some diff content")

        filepath = tmp_path / CONFLICTS_DIR / "42.diff"
        assert filepath.exists()
        assert filepath.read_text() == "some diff content"


# ---------------------------------------------------------------------------
# _comment_on_pr
# ---------------------------------------------------------------------------


class TestCommentOnPr:
    def test_posts_comment(self):
        pr = PRInfo(number=99, head_ref_name="feat/S99-cluster")

        with patch("scripts.pr_conflict_resolver.run_cmd") as mock_run:
            mock_run.return_value = _make_completed_process()
            _comment_on_pr(pr, "short diff")

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[:4] == ["gh", "pr", "comment", "99"]
        assert "auto-rebase failed" in args[5]  # --body value

    def test_truncates_long_diff(self):
        pr = PRInfo(number=99, head_ref_name="feat/S99-cluster")
        long_diff = "x" * 5000

        with patch("scripts.pr_conflict_resolver.run_cmd") as mock_run:
            mock_run.return_value = _make_completed_process()
            _comment_on_pr(pr, long_diff)

        args = mock_run.call_args[0][0]
        body = args[5]
        assert "(truncated)" in body


# ---------------------------------------------------------------------------
# main — integration with mocks
# ---------------------------------------------------------------------------


class TestMain:
    def test_dry_run_lists_prs(self, capsys):
        with patch("scripts.pr_conflict_resolver.list_open_prs") as mock_list:
            mock_list.return_value = [
                PRInfo(number=101, head_ref_name="feat/S01-cluster"),
                PRInfo(number=102, head_ref_name="feat/S02-cluster"),
            ]
            rc = main(["--dry-run"])

        assert rc == 0
        captured = capsys.readouterr()
        assert "PR #101" in captured.out
        assert "PR #102" in captured.out
        assert "feat/S01-cluster" in captured.out

    def test_dry_run_with_only_filter(self, capsys):
        with patch("scripts.pr_conflict_resolver.list_open_prs") as mock_list:
            mock_list.return_value = [
                PRInfo(number=101, head_ref_name="feat/S01-cluster"),
                PRInfo(number=102, head_ref_name="feat/S02-cluster"),
                PRInfo(number=200, head_ref_name="docs/readme"),
            ]
            rc = main(["--dry-run", "--only", "S01"])

        assert rc == 0
        captured = capsys.readouterr()
        assert "PR #101" in captured.out
        assert "PR #102" not in captured.out
        assert "Filtered to 1" in captured.out

    def test_no_prs(self, capsys):
        with patch("scripts.pr_conflict_resolver.list_open_prs") as mock_list:
            mock_list.return_value = []
            rc = main([])

        assert rc == 0
        captured = capsys.readouterr()
        assert "No PRs to process" in captured.out

    def test_successful_rebase_all(self, capsys, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        with (
            patch("scripts.pr_conflict_resolver.list_open_prs") as mock_list,
            patch("scripts.pr_conflict_resolver.run_cmd") as mock_run,
        ):
            mock_list.return_value = [
                PRInfo(number=101, head_ref_name="feat/S01-cluster"),
            ]
            mock_run.return_value = _make_completed_process()
            rc = main([])

        assert rc == 0
        captured = capsys.readouterr()
        assert "1 succeeded" in captured.out

    def test_conflict_returns_nonzero(self, capsys, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        call_count = 0

        def side_effect(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            if cmd == ["git", "rebase", "origin/main"]:
                raise subprocess.CalledProcessError(1, cmd, stderr="CONFLICT")
            if cmd == ["git", "diff", "--name-only", "--diff-filter=U"]:
                return _make_completed_process(stdout="file.py")
            if cmd == ["git", "diff"]:
                return _make_completed_process(stdout="conflict")
            if cmd == ["git", "rebase", "--abort"]:
                return _make_completed_process()
            if cmd[:3] == ["gh", "pr", "comment"]:
                return _make_completed_process()
            return _make_completed_process()

        with (
            patch("scripts.pr_conflict_resolver.list_open_prs") as mock_list,
            patch("scripts.pr_conflict_resolver.run_cmd", side_effect=side_effect),
        ):
            mock_list.return_value = [
                PRInfo(number=101, head_ref_name="feat/S01-cluster"),
            ]
            rc = main([])

        assert rc == 1
        captured = capsys.readouterr()
        assert "1 conflicts" in captured.out

    def test_failure_does_not_block_other_prs(self, capsys, tmp_path, monkeypatch):
        """A failed rebase on one PR should not prevent processing of subsequent PRs."""
        monkeypatch.chdir(tmp_path)

        pr_index = 0

        def side_effect(cmd, **kwargs):
            nonlocal pr_index
            if cmd == ["git", "rebase", "origin/main"]:
                if pr_index == 0:
                    pr_index += 1
                    raise subprocess.CalledProcessError(1, cmd, stderr="CONFLICT")
                else:
                    pr_index += 1
                    return _make_completed_process()
            if cmd == ["git", "diff", "--name-only", "--diff-filter=U"]:
                return _make_completed_process(stdout="file.py")
            if cmd == ["git", "diff"]:
                return _make_completed_process(stdout="conflict")
            if cmd == ["git", "rebase", "--abort"]:
                return _make_completed_process()
            if cmd[:3] == ["gh", "pr", "comment"]:
                return _make_completed_process()
            return _make_completed_process()

        with (
            patch("scripts.pr_conflict_resolver.list_open_prs") as mock_list,
            patch("scripts.pr_conflict_resolver.run_cmd", side_effect=side_effect),
        ):
            mock_list.return_value = [
                PRInfo(number=101, head_ref_name="feat/S01-cluster"),
                PRInfo(number=102, head_ref_name="feat/S02-cluster"),
            ]
            main([])

        captured = capsys.readouterr()
        # Both PRs should have been processed
        assert "1 succeeded" in captured.out
        assert "1 conflicts" in captured.out

    def test_only_filter_in_real_mode(self, capsys, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        with (
            patch("scripts.pr_conflict_resolver.list_open_prs") as mock_list,
            patch("scripts.pr_conflict_resolver.run_cmd") as mock_run,
        ):
            mock_list.return_value = [
                PRInfo(number=101, head_ref_name="feat/S01-cluster"),
                PRInfo(number=102, head_ref_name="feat/S02-cluster"),
            ]
            mock_run.return_value = _make_completed_process()
            rc = main(["--only", "S02"])

        assert rc == 0
        captured = capsys.readouterr()
        # Only PR 102 should be processed
        assert "PR #102" in captured.out
        assert "PR #101" not in captured.out or "Processing PR #101" not in captured.out


# ---------------------------------------------------------------------------
# _capture_conflict_diff
# ---------------------------------------------------------------------------


class TestCaptureConflictDiff:
    def test_returns_diff_string(self):
        with patch("scripts.pr_conflict_resolver.run_cmd") as mock_run:
            mock_run.side_effect = [
                _make_completed_process(stdout="file1.py\nfile2.py"),
                _make_completed_process(stdout="diff content here"),
            ]
            result = _capture_conflict_diff()

        assert "Unmerged files" in result
        assert "file1.py" in result
        assert "file2.py" in result
        assert "diff content here" in result

    def test_handles_git_diff_failure(self):
        with patch("scripts.pr_conflict_resolver.run_cmd") as mock_run:
            mock_run.side_effect = [
                _make_completed_process(stdout="file1.py"),
                subprocess.CalledProcessError(1, ["git", "diff"]),
            ]
            result = _capture_conflict_diff()

        assert "Unmerged files" in result
        assert "could not capture diff" in result
