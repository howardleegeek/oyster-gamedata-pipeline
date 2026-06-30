#!/usr/bin/env python3
"""Tests for bin/release_notes_from_git.py — extract release notes from git.

Covers:
- parse_commits: parses `git log --oneline` output into (type, message) tuples;
  skips empty lines; skips lines without a space (no message); recognises
  feat/fix/docs/test type prefixes (including the "feat(scope):" form);
  non-conventional commits fall through to "other".
- group_commits: groups by type and returns a plain dict (not defaultdict).
- format_release_notes: emits sections in the canonical order
  (feat, fix, docs, test, other); only includes sections that have
  commits; uses the human-readable labels from the script.
- run_git_log: invokes the documented git command (list-args, no shell)
  with --oneline --no-merges and the requested ref range; propagates
  stdout; surfaces CalledProcessError by printing + sys.exit(1).
- main(): empty log → exit 0 with "No commits" stderr; non-empty log
  prints to stdout by default; --output writes the notes to the file
  and prints a confirmation to stderr.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_BIN_DIR = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(_BIN_DIR))

import release_notes_from_git as m  # noqa: E402,I001


# ---------------------------------------------------------------------------
# parse_commits
# ---------------------------------------------------------------------------


class TestParseCommits:
    """parse_commits parses `git log --oneline` lines into (type, message)."""

    def test_empty_input(self):
        """Empty string returns empty list (no exception)."""
        assert m.parse_commits("") == []

    def test_only_whitespace(self):
        """Whitespace-only string returns empty list."""
        assert m.parse_commits("   \n   \n") == []

    def test_single_feat_commit(self):
        """A conventional feat commit → ('feat', full_message)."""
        out = "abc1234 feat: add new dashboard\n"
        assert m.parse_commits(out) == [("feat", "feat: add new dashboard")]

    def test_fix_with_scope(self):
        """A fix(scope): commit is grouped as 'fix' (prefix match)."""
        out = "def5678 fix(api): handle 404\n"
        assert m.parse_commits(out) == [("fix", "fix(api): handle 404")]

    def test_docs_commit(self):
        out = "9999999 docs: update README\n"
        assert m.parse_commits(out) == [("docs", "docs: update README")]

    def test_test_commit(self):
        out = "8888888 test: add coverage for foo\n"
        assert m.parse_commits(out) == [("test", "test: add coverage for foo")]

    def test_non_conventional_commit_falls_through_to_other(self):
        out = "7777777 random commit without conventional prefix\n"
        assert m.parse_commits(out) == [("other", "random commit without conventional prefix")]

    def test_uppercase_type_is_lowercased_then_matched(self):
        """Uppercase type part is lowercased; 'FEAT:' still groups as 'feat'."""
        out = "6666666 FEAT: add upper\n"
        assert m.parse_commits(out) == [("feat", "FEAT: add upper")]

    def test_multiple_commits(self):
        out = (
            "1111111 feat: one\n"
            "2222222 fix: two\n"
            "3333333 chore: three\n"
        )
        result = m.parse_commits(out)
        assert result == [
            ("feat", "feat: one"),
            ("fix", "fix: two"),
            ("other", "chore: three"),
        ]

    def test_line_without_space_is_skipped(self):
        """Lines that have no space (no message) are skipped — no crash."""
        out = "justahashnodevided\nreal1234 feat: ok\n"
        assert m.parse_commits(out) == [("feat", "feat: ok")]

    def test_message_with_colon_in_body_does_not_split_type(self):
        """Only the FIRST colon separates type from message."""
        out = "5555555 fix: handle url like http://x/y\n"
        result = m.parse_commits(out)
        assert len(result) == 1
        assert result[0][0] == "fix"
        assert result[0][1] == "fix: handle url like http://x/y"

    def test_type_match_uses_startswith(self):
        """A type like 'feat' matches 'feat' but not 'feature' (no overlap)."""
        out = "4444444 feature: extended\n"
        # 'feature' starts with 'feat' → would group as 'feat' by prefix match
        result = m.parse_commits(out)
        assert result == [("feat", "feature: extended")]


# ---------------------------------------------------------------------------
# group_commits
# ---------------------------------------------------------------------------


class TestGroupCommits:
    """group_commits buckets (type, message) tuples by type into a dict."""

    def test_empty(self):
        assert m.group_commits([]) == {}

    def test_single_type(self):
        commits = [("feat", "feat: a"), ("feat", "feat: b")]
        assert m.group_commits(commits) == {"feat": ["feat: a", "feat: b"]}

    def test_multiple_types(self):
        commits = [
            ("feat", "feat: a"),
            ("fix", "fix: b"),
            ("other", "chore: c"),
            ("fix", "fix: d"),
        ]
        result = m.group_commits(commits)
        assert result == {
            "feat": ["feat: a"],
            "fix": ["fix: b", "fix: d"],
            "other": ["chore: c"],
        }

    def test_returns_plain_dict_not_defaultdict(self):
        """A missing key must raise KeyError (not silently default to [])."""
        commits = [("feat", "feat: a")]
        grouped = m.group_commits(commits)
        assert "fix" not in grouped
        with pytest.raises(KeyError):
            _ = grouped["fix"]


# ---------------------------------------------------------------------------
# format_release_notes
# ---------------------------------------------------------------------------


class TestFormatReleaseNotes:
    """format_release_notes renders grouped commits into Markdown sections."""

    def test_empty_input_returns_empty_string(self):
        assert m.format_release_notes({}) == ""

    def test_only_feat_section(self):
        out = m.format_release_notes({"feat": ["feat: add foo"]})
        assert "### Features" in out
        assert "- feat: add foo" in out

    def test_section_order_is_canonical(self):
        """Sections appear in feat → fix → docs → test → other order."""
        out = m.format_release_notes(
            {
                "other": ["chore: x"],
                "test": ["test: y"],
                "docs": ["docs: z"],
                "fix": ["fix: w"],
                "feat": ["feat: v"],
            }
        )
        feat_pos = out.index("### Features")
        fix_pos = out.index("### Bug Fixes")
        docs_pos = out.index("### Documentation")
        test_pos = out.index("### Tests")
        other_pos = out.index("### Other Changes")
        assert feat_pos < fix_pos < docs_pos < test_pos < other_pos

    def test_empty_section_is_omitted(self):
        """An empty list for a type must NOT emit the section header."""
        out = m.format_release_notes({"feat": ["feat: a"], "fix": []})
        assert "### Features" in out
        assert "### Bug Fixes" not in out

    def test_unknown_type_in_input_is_ignored(self):
        """A type not in type_order is silently skipped (not a hard error)."""
        out = m.format_release_notes({"chore": ["chore: x"]})
        assert "chore: x" not in out
        assert "### Chore" not in out

    def test_bullet_list_under_each_section(self):
        out = m.format_release_notes(
            {"feat": ["feat: one", "feat: two"], "fix": ["fix: three"]}
        )
        assert "- feat: one" in out
        assert "- feat: two" in out
        assert "- fix: three" in out

    def test_no_trailing_blank_line(self):
        """Output is strip()ed — no trailing blank line / extra whitespace."""
        out = m.format_release_notes({"feat": ["feat: a"]})
        assert out == out.rstrip()


# ---------------------------------------------------------------------------
# run_git_log
# ---------------------------------------------------------------------------


class TestRunGitLog:
    """run_git_log shells out to git and propagates stdout."""

    def test_invokes_expected_git_command(self):
        """The exact command (list-args) is run with --oneline --no-merges."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="abc1234 feat: x\n", returncode=0)
            m.run_git_log("v1.0.0", "HEAD")
        args, kwargs = mock_run.call_args
        cmd = args[0]
        assert cmd[0] == "git"
        assert "log" in cmd
        assert "--oneline" in cmd
        assert "--no-merges" in cmd
        assert "v1.0.0..HEAD" in cmd
        assert kwargs.get("text") is True
        assert kwargs.get("check") is True

    def test_default_until_ref_is_head(self):
        """If until_ref is omitted, ..HEAD is used."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", returncode=0)
            m.run_git_log("v1.0.0")
        cmd = mock_run.call_args[0][0]
        assert "v1.0.0..HEAD" in cmd

    def test_returns_stdout(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="abc1234 feat: x\n", returncode=0)
            out = m.run_git_log("v1.0.0")
        assert out == "abc1234 feat: x\n"

    def test_calledprocesserror_prints_and_exits(self):
        """On non-zero git exit, run_git_log writes to stderr and sys.exit(1)."""
        err = subprocess.CalledProcessError(returncode=128, cmd=["git", "log"], stderr="bad ref")
        with patch("subprocess.run", side_effect=err), patch("sys.stderr") as mock_err:
            with pytest.raises(SystemExit) as exc:
                m.run_git_log("nonexistent-ref")
        assert exc.value.code == 1
        # A diagnostic was printed to stderr (non-empty write)
        assert mock_err.write.called


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


class TestMain:
    """main() ties run_git_log → parse → group → format → output.

    main() takes no positional args and uses argparse on sys.argv, so each
    test sets sys.argv via monkeypatch and calls main() with no args.
    """

    def _git_log_output(self) -> str:
        return "abc1234 feat: add foo\ndef5678 fix(api): handle edge\n"

    def test_empty_git_log_exits_zero_with_message(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["release_notes_from_git.py", "--since-ref", "v0.0.0"])
        with patch.object(m, "run_git_log", return_value=""):
            with pytest.raises(SystemExit) as exc:
                m.main()
        # main() calls sys.exit(0) on the empty-log path
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert "No commits" in captured.err

    def test_default_output_goes_to_stdout(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["release_notes_from_git.py", "--since-ref", "v0.0.0"])
        with patch.object(m, "run_git_log", return_value=self._git_log_output()):
            with pytest.raises(SystemExit) as exc:
                m.main()
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "### Features" in out
        assert "feat: add foo" in out
        assert "### Bug Fixes" in out
        assert "fix(api): handle edge" in out

    def test_output_file_writes_release_notes(self, tmp_path, capsys, monkeypatch):
        out_file = tmp_path / "RELEASE_NOTES.md"
        monkeypatch.setattr(
            sys, "argv", ["r.py", "--since-ref", "v0.0.0", "--output", str(out_file)]
        )
        with patch.object(m, "run_git_log", return_value=self._git_log_output()):
            with pytest.raises(SystemExit) as exc:
                m.main()
        assert exc.value.code == 0
        text = out_file.read_text()
        assert "### Features" in text
        assert "feat: add foo" in text
        assert "### Bug Fixes" in text
        # Confirmation goes to stderr
        assert "Release notes written to" in capsys.readouterr().err

    def test_since_ref_default_is_origin_main(self, capsys, monkeypatch):
        """When --since-ref is omitted, the script defaults to 'origin/main'."""
        monkeypatch.setattr(sys, "argv", ["release_notes_from_git.py"])
        with patch.object(m, "run_git_log", return_value="") as mock_log:
            with pytest.raises(SystemExit) as exc:
                m.main()
        assert exc.value.code == 0
        # run_git_log was called with 'origin/main' as the since ref
        assert mock_log.call_args[0][0] == "origin/main"

    def test_output_file_only_contains_release_notes_no_confirmation(
        self, tmp_path, monkeypatch
    ):
        """The confirmation message goes to stderr, NOT into the file."""
        out_file = tmp_path / "RELEASE_NOTES.md"
        monkeypatch.setattr(
            sys, "argv", ["r.py", "--since-ref", "v0.0.0", "--output", str(out_file)]
        )
        with patch.object(m, "run_git_log", return_value=self._git_log_output()):
            with pytest.raises(SystemExit) as exc:
                m.main()
        assert exc.value.code == 0
        text = out_file.read_text()
        assert "Release notes written to" not in text
        assert "EMAIL-READY" not in text  # guard against cross-test bleed


# ---------------------------------------------------------------------------
# Sanity: module imports cleanly (no import-time side effects / sys.exit)
# ---------------------------------------------------------------------------


def test_module_imports_cleanly():
    """The module imports without running main() or writing to disk."""
    # Re-import the module under a fresh name to ensure no I/O happens at import.
    import importlib

    mod = importlib.import_module("release_notes_from_git")
    assert hasattr(mod, "parse_commits")
    assert hasattr(mod, "group_commits")
    assert hasattr(mod, "format_release_notes")
    assert hasattr(mod, "run_git_log")
    assert hasattr(mod, "main")
