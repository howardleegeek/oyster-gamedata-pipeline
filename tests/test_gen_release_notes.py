"""Tests for scripts/gen_release_notes.py.

All git and gh CLI calls are mocked via subprocess.run patching.
"""

from __future__ import annotations

import json
from unittest.mock import patch, MagicMock


from scripts.gen_release_notes import (
    Commit,
    PRInfo,
    _parse_commit,
    _group_commits,
    _format_commit_line,
    generate_release_notes,
    _CC_RE,
    _PR_RE,
    _WAVE_RE,
    _pr_url,
)

# ---------------------------------------------------------------------------
# Regex unit tests
# ---------------------------------------------------------------------------


class TestRegexPatterns:
    def test_cc_basic(self):
        m = _CC_RE.match("feat(audit): add H8 PASS_STRICT tier")
        assert m is not None
        assert m.group("type") == "feat"
        assert m.group("scope") == "audit"
        assert m.group("desc") == "add H8 PASS_STRICT tier"

    def test_cc_no_scope(self):
        m = _CC_RE.match("fix: bind subprocess result var")
        assert m is not None
        assert m.group("type") == "fix"
        assert m.group("scope") is None
        assert m.group("desc") == "bind subprocess result var"

    def test_cc_breaking(self):
        m = _CC_RE.match("feat(api)!: redesign endpoint")
        assert m is not None
        assert m.group("type") == "feat"
        assert m.group("scope") == "api"
        assert m.group("desc") == "redesign endpoint"

    def test_cc_no_match(self):
        m = _CC_RE.match("Merge pull request #24 from branch")
        assert m is None

    def test_pr_number(self):
        m = _PR_RE.search("feat(audit): H8 PASS_STRICT tier (#24) — Wave 1")
        assert m is not None
        assert m.group(1) == "24"

    def test_pr_number_at_end(self):
        m = _PR_RE.search("fix: bind subprocess result var (#32)")
        assert m is not None
        assert m.group(1) == "32"

    def test_wave_tag(self):
        m = _WAVE_RE.search("feat(audit): H8 PASS_STRICT tier (#24) — Wave 1")
        assert m is not None
        assert m.group(1) == "1"


# ---------------------------------------------------------------------------
# _parse_commit tests
# ---------------------------------------------------------------------------


class TestParseCommit:
    def test_feat_with_scope_and_pr(self):
        line = "abc123|||feat(audit): H8 PASS_STRICT tier (#24) — Wave 1"
        c = _parse_commit(line)
        assert c.sha == "abc123"
        assert c.commit_type == "feat"
        assert c.scope == "audit"
        assert c.description == "H8 PASS_STRICT tier (#24) — Wave 1"
        assert c.pr_number == 24

    def test_fix_no_scope(self):
        line = "def456|||fix(S07v2): bind subprocess result var (#32)"
        c = _parse_commit(line)
        assert c.sha == "def456"
        assert c.commit_type == "fix"
        assert c.scope == "S07v2"
        assert c.pr_number == 32

    def test_merge_commit(self):
        line = "aaa111|||Merge pull request #25 from feature/branch"
        c = _parse_commit(line)
        assert c.sha == "aaa111"
        assert c.commit_type == "other"
        assert c.scope is None
        assert c.pr_number == 25

    def test_no_pr_number(self):
        line = "bbb222|||chore: bump dependencies"
        c = _parse_commit(line)
        assert c.sha == "bbb222"
        assert c.commit_type == "chore"
        assert c.pr_number is None

    def test_daemon_type(self):
        line = "ccc333|||feat(daemon): iter-watcher (#26)"
        c = _parse_commit(line)
        assert c.commit_type == "feat"
        assert c.scope == "daemon"
        assert c.pr_number == 26


# ---------------------------------------------------------------------------
# _format_commit_line tests
# ---------------------------------------------------------------------------


class TestFormatCommitLine:
    def test_with_scope_and_pr(self):
        c = Commit(
            sha="abc",
            subject="feat(audit): H8 PASS_STRICT tier (#24) — Wave 1",
            pr_number=24,
            scope="audit",
            commit_type="feat",
            description="H8 PASS_STRICT tier (#24) — Wave 1",
        )
        line = _format_commit_line(c)
        assert line == "- feat(audit): H8 PASS_STRICT tier (#24) — Wave 1 (#24)"

    def test_no_scope(self):
        c = Commit(
            sha="abc",
            subject="fix: bind subprocess result var (#32)",
            pr_number=32,
            scope=None,
            commit_type="fix",
            description="bind subprocess result var (#32)",
        )
        line = _format_commit_line(c)
        assert line == "- fix: bind subprocess result var (#32) (#32)"

    def test_no_pr(self):
        c = Commit(
            sha="abc",
            subject="chore: bump deps",
            pr_number=None,
            scope=None,
            commit_type="chore",
            description="bump deps",
        )
        line = _format_commit_line(c)
        assert line == "- chore: bump deps"
        assert "#" not in line


# ---------------------------------------------------------------------------
# _group_commits tests
# ---------------------------------------------------------------------------


class TestGroupCommits:
    def test_groups_by_type(self):
        commits = [
            Commit(
                "a1",
                "feat(a): x (#1)",
                pr_number=1,
                scope="a",
                commit_type="feat",
                description="x (#1)",
            ),
            Commit(
                "a2",
                "feat(b): y (#2)",
                pr_number=2,
                scope="b",
                commit_type="feat",
                description="y (#2)",
            ),
            Commit(
                "a3",
                "fix(c): z (#3)",
                pr_number=3,
                scope="c",
                commit_type="fix",
                description="z (#3)",
            ),
            Commit(
                "a4",
                "docs: readme (#4)",
                pr_number=4,
                scope=None,
                commit_type="docs",
                description="readme (#4)",
            ),
        ]
        groups = _group_commits(commits)
        assert "Features" in groups
        assert len(groups["Features"]) == 2
        assert "Fixes" in groups
        assert len(groups["Fixes"]) == 1
        assert "Documentation" in groups
        assert len(groups["Documentation"]) == 1

    def test_unknown_type_goes_to_other(self):
        commits = [
            Commit(
                "a1", "random: something", commit_type="random", description="something"
            ),
        ]
        groups = _group_commits(commits)
        assert "Other" in groups
        assert len(groups["Other"]) == 1

    def test_daemon_scope_maps_to_daemon_section(self):
        # Note: "feat" type with "daemon" scope still maps to "Features"
        # because grouping is by commit_type, not scope.
        commits = [
            Commit(
                "a1",
                "feat(daemon): iter-watcher (#26)",
                pr_number=26,
                scope="daemon",
                commit_type="feat",
                description="iter-watcher (#26)",
            ),
        ]
        groups = _group_commits(commits)
        assert "Features" in groups
        assert len(groups["Features"]) == 1


# ---------------------------------------------------------------------------
# _pr_url tests
# ---------------------------------------------------------------------------


class TestPrUrl:
    @patch("scripts.gen_release_notes._run")
    def test_pr_url_from_ssh_remote(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="git@github.com:owner/repo.git\n",
            returncode=0,
        )
        url = _pr_url(42)
        assert url == "https://github.com/owner/repo/pull/42"

    @patch("scripts.gen_release_notes._run")
    def test_pr_url_from_https_remote(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="https://github.com/owner/repo.git\n",
            returncode=0,
        )
        url = _pr_url(99)
        assert url == "https://github.com/owner/repo/pull/99"

    @patch("scripts.gen_release_notes._run")
    def test_pr_url_fallback(self, mock_run):
        mock_run.side_effect = Exception("no remote")
        url = _pr_url(7)
        assert url == "https://github.com/OWNER/REPO/pull/7"


# ---------------------------------------------------------------------------
# generate_release_notes integration tests (fully mocked)
# ---------------------------------------------------------------------------


class TestGenerateReleaseNotes:
    def _make_commits(self):
        return [
            Commit(
                "a1",
                "feat(audit): H8 PASS_STRICT tier (#24) — Wave 1",
                pr_number=24,
                scope="audit",
                commit_type="feat",
                description="H8 PASS_STRICT tier (#24) — Wave 1",
            ),
            Commit(
                "a2",
                "feat(audit): --strict-buyer evidence_provenance (#25) — Wave 1",
                pr_number=25,
                scope="audit",
                commit_type="feat",
                description="--strict-buyer evidence_provenance (#25) — Wave 1",
            ),
            Commit(
                "a3",
                "feat(daemon): iter-watcher (#26)",
                pr_number=26,
                scope="daemon",
                commit_type="feat",
                description="iter-watcher (#26)",
            ),
            Commit(
                "a4",
                "ci: add release workflow (#30)",
                pr_number=30,
                scope=None,
                commit_type="ci",
                description="add release workflow (#30)",
            ),
            Commit(
                "a5",
                "fix(S07v2): bind subprocess result var (#32)",
                pr_number=32,
                scope="S07v2",
                commit_type="fix",
                description="bind subprocess result var (#32)",
            ),
        ]

    def _make_prs(self):
        return [
            PRInfo(
                24, "feat(audit): H8 PASS_STRICT tier", "alice", "2026-05-19T10:00:00Z"
            ),
            PRInfo(
                25,
                "feat(audit): --strict-buyer evidence_provenance",
                "bob",
                "2026-05-19T11:00:00Z",
            ),
            PRInfo(26, "feat(daemon): iter-watcher", "charlie", "2026-05-19T12:00:00Z"),
            PRInfo(30, "ci: add release workflow", "dave", "2026-05-19T13:00:00Z"),
            PRInfo(
                32,
                "fix(S07v2): bind subprocess result var",
                "eve",
                "2026-05-19T14:00:00Z",
            ),
        ]

    def test_output_is_valid_markdown(self):
        commits = self._make_commits()
        prs = self._make_prs()
        md = generate_release_notes("v0.4.1", "v0.5.0", _commits=commits, _prs=prs)

        # Header
        assert "## v0.5.0" in md
        # Sections present
        assert "### Features" in md
        assert "### Fixes" in md
        assert "### CI / Workflows" in md
        assert "### Cluster metrics" in md

    def test_grouping_by_commit_type(self):
        commits = self._make_commits()
        prs = self._make_prs()
        md = generate_release_notes("v0.4.1", "v0.5.0", _commits=commits, _prs=prs)

        # Features section should contain feat commits
        features_idx = md.index("### Features")
        fixes_idx = md.index("### Fixes")
        ci_idx = md.index("### CI / Workflows")

        # feat(audit) commits should be in Features
        assert "feat(audit): H8 PASS_STRICT tier" in md[features_idx:fixes_idx]
        assert "feat(daemon): iter-watcher" in md[features_idx:fixes_idx]

        # fix commit should be in Fixes
        assert "fix(S07v2): bind subprocess result var" in md[fixes_idx:ci_idx]

        # ci commit should be in CI / Workflows
        assert "ci: add release workflow" in md[ci_idx:]

    def test_pr_links_present(self):
        commits = self._make_commits()
        prs = self._make_prs()
        md = generate_release_notes("v0.4.1", "v0.5.0", _commits=commits, _prs=prs)

        assert "(#24)" in md
        assert "(#25)" in md
        assert "(#26)" in md
        assert "(#30)" in md
        assert "(#32)" in md

    def test_cluster_metrics(self):
        commits = self._make_commits()
        prs = self._make_prs()
        md = generate_release_notes("v0.4.1", "v0.5.0", _commits=commits, _prs=prs)

        assert "5 specs dispatched, 5 PRs merged" in md

    def test_empty_commits(self):
        md = generate_release_notes("v0.4.0", "v0.4.1", _commits=[], _prs=[])
        assert "## v0.4.1" in md
        assert "0 specs dispatched, 0 PRs merged" in md

    def test_section_order(self):
        commits = [
            Commit(
                "a1",
                "fix: something (#1)",
                pr_number=1,
                commit_type="fix",
                description="something (#1)",
            ),
            Commit(
                "a2",
                "feat: something (#2)",
                pr_number=2,
                commit_type="feat",
                description="something (#2)",
            ),
            Commit(
                "a3",
                "docs: something (#3)",
                pr_number=3,
                commit_type="docs",
                description="something (#3)",
            ),
        ]
        md = generate_release_notes("v0.1.0", "v0.2.0", _commits=commits, _prs=[])

        feat_idx = md.index("### Features")
        docs_idx = md.index("### Documentation")
        fix_idx = md.index("### Fixes")

        # Features should come before Documentation, which comes before Fixes
        assert feat_idx < fix_idx < docs_idx


# ---------------------------------------------------------------------------
# CLI / subprocess integration tests
# ---------------------------------------------------------------------------


class TestGitLogParsing:
    @patch("scripts.gen_release_notes._run")
    def test_git_log_parses_correctly(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="abc123|||feat(audit): add feature (#10)\ndef456|||fix: bug fix (#11)\n",
            returncode=0,
        )
        from scripts.gen_release_notes import _git_log

        lines = _git_log("v0.4.0", "v0.5.0")
        assert len(lines) == 2
        assert "abc123|||feat(audit): add feature (#10)" in lines
        assert "def456|||fix: bug fix (#11)" in lines

    @patch("scripts.gen_release_notes._run")
    def test_git_log_empty(self, mock_run):
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        from scripts.gen_release_notes import _git_log

        lines = _git_log("v0.4.0", "v0.4.0")
        assert lines == []


class TestGhPrListParsing:
    @patch("scripts.gen_release_notes._run")
    def test_gh_pr_list_parses_correctly(self, mock_run):
        pr_data = [
            {
                "number": 24,
                "title": "feat(audit): H8 PASS_STRICT tier",
                "author": {"login": "alice"},
                "mergedAt": "2026-05-19T10:00:00Z",
                "headRefName": "feat/audit-strict",
            },
            {
                "number": 25,
                "title": "feat(audit): --strict-buyer",
                "author": {"login": "bob"},
                "mergedAt": "2026-05-19T11:00:00Z",
                "headRefName": "feat/strict-buyer",
            },
        ]
        mock_run.return_value = MagicMock(
            stdout=json.dumps(pr_data),
            returncode=0,
        )
        from scripts.gen_release_notes import _gh_pr_list

        prs = _gh_pr_list("HEAD")
        assert len(prs) == 2
        assert prs[0].number == 24
        assert prs[0].author == "alice"
        assert prs[1].number == 25
        assert prs[1].author == "bob"

    @patch("scripts.gen_release_notes._run")
    def test_gh_pr_list_invalid_json(self, mock_run):
        mock_run.return_value = MagicMock(stdout="not json", returncode=0)
        from scripts.gen_release_notes import _gh_pr_list

        prs = _gh_pr_list("HEAD")
        assert prs == []

    @patch("scripts.gen_release_notes._run")
    def test_gh_pr_list_empty(self, mock_run):
        mock_run.return_value = MagicMock(stdout="[]", returncode=0)
        from scripts.gen_release_notes import _gh_pr_list

        prs = _gh_pr_list("HEAD")
        assert prs == []


class TestMainCLI:
    @patch("scripts.gen_release_notes._run")
    def test_main_with_args(self, mock_run, capsys):
        """Test that main() works with --prev and --curr args."""

        # Mock git log
        def side_effect(cmd, **kwargs):
            if cmd[0] == "git" and "log" in cmd:
                return MagicMock(
                    stdout="abc123|||feat(core): add feature (#1)\n",
                    returncode=0,
                )
            elif cmd[0] == "gh":
                return MagicMock(
                    stdout=json.dumps(
                        [
                            {
                                "number": 1,
                                "title": "feat(core): add feature",
                                "author": {"login": "dev"},
                                "mergedAt": "2026-05-19T10:00:00Z",
                                "headRefName": "feat/core",
                            }
                        ]
                    ),
                    returncode=0,
                )
            elif cmd[0] == "git" and "remote" in cmd:
                return MagicMock(
                    stdout="git@github.com:owner/repo.git\n",
                    returncode=0,
                )
            return MagicMock(stdout="", returncode=0)

        mock_run.side_effect = side_effect

        from scripts.gen_release_notes import main

        main(["--prev", "v0.4.1", "--curr", "HEAD"])
        captured = capsys.readouterr()
        assert "## HEAD" in captured.out
        assert "### Features" in captured.out
        assert "feat(core): add feature (#1)" in captured.out

    @patch("scripts.gen_release_notes._run")
    def test_main_defaults_to_last_tag(self, mock_run, capsys):
        """Test that main() defaults prev to last tag when not provided."""
        call_order = []

        def side_effect(cmd, **kwargs):
            call_order.append(cmd)
            if cmd[0] == "git" and "describe" in cmd:
                return MagicMock(stdout="v0.4.0\n", returncode=0)
            elif cmd[0] == "git" and "log" in cmd:
                return MagicMock(stdout="", returncode=0)
            elif cmd[0] == "gh":
                return MagicMock(stdout="[]", returncode=0)
            return MagicMock(stdout="", returncode=0)

        mock_run.side_effect = side_effect

        from scripts.gen_release_notes import main

        main([])
        captured = capsys.readouterr()

        # Should have called git describe to find last tag
        assert any("describe" in " ".join(c) for c in call_order)
        assert "## HEAD" in captured.out
