#!/usr/bin/env python3
"""Tests for bin/dependency_pinning_check.py — pip dep pinning validator.

Covers:
- UnpinnedDep NamedTuple (basic field access, equality)
- check_file (pinned ==, unpinned range, no-version, comments, blank lines,
  mixed lines, missing file, OSError on read, hash-rendered, -r/-e options)
- find_requirements_files (root patterns, rglob discovery, sort order, dedup)
- main (--root flag, all-pinned rc=0, unpinned rc=1, no files rc=0,
  print format, custom file argument, file-list positional)
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

# Add bin/ to sys.path so the module is importable as a top-level name
_BIN_DIR = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(_BIN_DIR))

from bin.dependency_pinning_check import (  # noqa: E402
    COMMENT_OR_OPTION,
    NO_VERSION_PATTERN,
    RANGE_PATTERN,
    UnpinnedDep,
    check_file,
    find_requirements_files,
    main,
)

# ---------------------------------------------------------------------------
# UnpinnedDep NamedTuple
# ---------------------------------------------------------------------------


class TestUnpinnedDep:
    """Tests for the UnpinnedDep NamedTuple."""

    def test_field_access(self):
        dep = UnpinnedDep(name="requests", specifier=">=1.0", line=5, path="req.txt")
        assert dep.name == "requests"
        assert dep.specifier == ">=1.0"
        assert dep.line == 5
        assert dep.path == "req.txt"

    def test_equality(self):
        a = UnpinnedDep("a", ">=1", 1, "p")
        b = UnpinnedDep("a", ">=1", 1, "p")
        c = UnpinnedDep("a", ">=2", 1, "p")
        assert a == b
        assert a != c

    def test_iterable_unpack(self):
        dep = UnpinnedDep("foo", ">=9", 3, "x.txt")
        n, s, ln, p = dep
        assert (n, s, ln, p) == ("foo", ">=9", 3, "x.txt")


# ---------------------------------------------------------------------------
# regex sanity (cheap belt-and-suspenders)
# ---------------------------------------------------------------------------


class TestRegexPatterns:
    """Regex constants exist and match expected forms."""

    def test_range_pattern_matches_geq(self):
        m = RANGE_PATTERN.match("foo>=1.2.3")
        assert m is not None
        assert m.group("name") == "foo"
        assert m.group("op") == ">="
        assert m.group("ver") == "1.2.3"

    def test_range_pattern_matches_legacy(self):
        m = RANGE_PATTERN.match("requests ~=2.28")
        assert m is not None
        assert m.group("op") == "~="

    def test_range_pattern_ignores_pinned(self):
        # Plain "==" should NOT match the range regex
        assert RANGE_PATTERN.match("foo==1.0") is None

    def test_no_version_pattern_matches_name_only(self):
        m = NO_VERSION_PATTERN.match("requests\n")
        assert m is not None
        assert m.group("name") == "requests"

    def test_comment_or_option_matches_hash(self):
        assert COMMENT_OR_OPTION.match("# a comment") is not None

    def test_comment_or_option_matches_blank(self):
        assert COMMENT_OR_OPTION.match("") is not None

    def test_comment_or_option_matches_option(self):
        assert COMMENT_OR_OPTION.match("--extra-index-url https://x") is not None


# ---------------------------------------------------------------------------
# check_file
# ---------------------------------------------------------------------------


class TestCheckFile:
    """Tests for check_file(): scan a single requirements file."""

    def test_pinned_exact_returns_empty(self, tmp_path: Path):
        f = tmp_path / "requirements.txt"
        f.write_text("requests==1.2.3\nflask==2.0.0\n", encoding="utf-8")
        assert check_file(f) == []

    def test_range_specifier_detected(self, tmp_path: Path):
        f = tmp_path / "requirements.txt"
        f.write_text("requests>=1.2.3\n", encoding="utf-8")
        result = check_file(f)
        assert len(result) == 1
        assert result[0].name == "requests"
        assert result[0].specifier == ">=1.2.3"
        assert result[0].line == 1

    def test_no_version_specifier_detected(self, tmp_path: Path):
        f = tmp_path / "requirements.txt"
        f.write_text("flask\n", encoding="utf-8")
        result = check_file(f)
        assert len(result) == 1
        assert result[0].name == "flask"
        assert result[0].specifier == "(no version)"

    def test_multiple_unpinned(self, tmp_path: Path):
        f = tmp_path / "requirements.txt"
        f.write_text("a>=1\nb==2.0\nc\nd<=3.0\n", encoding="utf-8")
        result = check_file(f)
        # a is range, b is pinned, c is no-version, d is range
        names = [r.name for r in result]
        assert names == ["a", "c", "d"]

    def test_comment_line_ignored(self, tmp_path: Path):
        f = tmp_path / "requirements.txt"
        f.write_text("# requests>=1.0\nrequests==2.0\n", encoding="utf-8")
        assert check_file(f) == []

    def test_blank_line_ignored(self, tmp_path: Path):
        f = tmp_path / "requirements.txt"
        f.write_text("\n\nrequests==1.0\n\n", encoding="utf-8")
        assert check_file(f) == []

    def test_inline_comment_strips_specifier(self, tmp_path: Path):
        f = tmp_path / "requirements.txt"
        # The unpinned spec is on the line, the rest is a comment.
        f.write_text("requests>=1.0  # legacy pin\n", encoding="utf-8")
        result = check_file(f)
        # The "# legacy pin" suffix is stripped before the range regex matches,
        # so "requests>=1.0" is detected as a range specifier.
        assert len(result) == 1
        assert result[0].name == "requests"
        assert result[0].specifier == ">=1.0"

    def test_r_option_ignored(self, tmp_path: Path):
        f = tmp_path / "requirements.txt"
        f.write_text("-r other.txt\nrequests==1.0\n", encoding="utf-8")
        assert check_file(f) == []

    def test_e_option_ignored(self, tmp_path: Path):
        f = tmp_path / "requirements.txt"
        f.write_text("-e git+https://example.com/pkg\nrequests==1.0\n", encoding="utf-8")
        assert check_file(f) == []

    def test_double_dash_option_ignored(self, tmp_path: Path):
        f = tmp_path / "requirements.txt"
        f.write_text("--hash=sha256:abc==\nrequests==1.0\n", encoding="utf-8")
        assert check_file(f) == []

    def test_missing_file_returns_empty(self, tmp_path: Path):
        f = tmp_path / "does_not_exist.txt"
        assert check_file(f) == []

    def test_oserror_on_read_returns_empty(self, tmp_path: Path):
        f = tmp_path / "requirements.txt"
        f.write_text("requests==1.0\n", encoding="utf-8")
        with patch.object(Path, "read_text", side_effect=OSError("boom")):
            assert check_file(f) == []

    def test_line_number_correct(self, tmp_path: Path):
        f = tmp_path / "requirements.txt"
        f.write_text("\n\n\nrequests>=1.0\n", encoding="utf-8")
        result = check_file(f)
        assert result[0].line == 4

    def test_path_stringified(self, tmp_path: Path):
        f = tmp_path / "requirements.txt"
        f.write_text("flask\n", encoding="utf-8")
        result = check_file(f)
        assert result[0].path == str(f)

    def test_pinned_with_hashes(self, tmp_path: Path):
        # Modern pip format: name==ver --hash=sha256:... — should pass.
        f = tmp_path / "requirements.txt"
        f.write_text("requests==2.31.0 --hash=sha256:abc\n", encoding="utf-8")
        assert check_file(f) == []

    def test_mixed_pinned_and_unpinned(self, tmp_path: Path):
        f = tmp_path / "requirements.txt"
        f.write_text(
            "a==1.0\nb>=2.0\nc==3.0\nd\n",
            encoding="utf-8",
        )
        result = check_file(f)
        assert [r.name for r in result] == ["b", "d"]
        assert [r.line for r in result] == [2, 4]

    def test_name_with_dash(self, tmp_path: Path):
        f = tmp_path / "requirements.txt"
        f.write_text("py-cpuinfo>=5.0\n", encoding="utf-8")
        result = check_file(f)
        assert len(result) == 1
        assert result[0].name == "py-cpuinfo"


# ---------------------------------------------------------------------------
# find_requirements_files
# ---------------------------------------------------------------------------


class TestFindRequirementsFiles:
    """Tests for find_requirements_files(): discover requirements files."""

    def test_finds_top_level_requirements(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text("a==1.0\n", encoding="utf-8")
        files = find_requirements_files(tmp_path)
        assert tmp_path / "requirements.txt" in files

    def test_finds_requirements_dev(self, tmp_path: Path):
        (tmp_path / "requirements-dev.txt").write_text("a==1.0\n", encoding="utf-8")
        files = find_requirements_files(tmp_path)
        assert tmp_path / "requirements-dev.txt" in files

    def test_finds_in_requirements_subdir(self, tmp_path: Path):
        sub = tmp_path / "requirements"
        sub.mkdir()
        (sub / "test.txt").write_text("a==1.0\n", encoding="utf-8")
        files = find_requirements_files(tmp_path)
        assert sub / "test.txt" in files

    def test_dedupes_across_patterns(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text("a==1.0\n", encoding="utf-8")
        files = find_requirements_files(tmp_path)
        # The same file is matched by both "requirements*.txt" and
        # "*requirements*.txt" — we expect a single entry in the deduped set.
        matches = [f for f in files if f.name == "requirements.txt"]
        assert len(matches) == 1

    def test_returns_sorted(self, tmp_path: Path):
        (tmp_path / "z.txt").mkdir()  # dirs shouldn't match but won't break sort
        (tmp_path / "requirements-b.txt").write_text("a==1.0\n", encoding="utf-8")
        (tmp_path / "requirements-a.txt").write_text("a==1.0\n", encoding="utf-8")
        files = find_requirements_files(tmp_path)
        names = [f.name for f in files if f.name.startswith("requirements-")]
        assert names == sorted(names)

    def test_empty_root_returns_empty(self, tmp_path: Path):
        assert find_requirements_files(tmp_path) == []

    def test_ignores_random_txt(self, tmp_path: Path):
        (tmp_path / "notes.txt").write_text("a==1.0\n", encoding="utf-8")
        files = find_requirements_files(tmp_path)
        # notes.txt should NOT be picked up — pattern is requirements-prefixed
        # OR in requirements/ subdir.
        assert all(f.name != "notes.txt" for f in files)


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


class TestMain:
    """Tests for the CLI main() entry point."""

    def test_all_pinned_returns_zero(self, tmp_path: Path, capsys):
        f = tmp_path / "requirements.txt"
        f.write_text("a==1.0\nb==2.0\n", encoding="utf-8")
        rc = main([str(f)])
        assert rc == 0
        out = capsys.readouterr()
        assert "✓" in out.out
        assert "1" in out.out  # 1 requirements file

    def test_unpinned_returns_one(self, tmp_path: Path, capsys):
        f = tmp_path / "requirements.txt"
        f.write_text("a>=1.0\nb==2.0\n", encoding="utf-8")
        rc = main([str(f)])
        assert rc == 1
        out = capsys.readouterr()
        assert "✗" in out.err
        assert "a" in out.err
        assert ">=1.0" in out.err
        # The pinned dep should NOT be in the error list.
        assert "b" not in out.err.split("a ")[1] if "a " in out.err else True

    def test_no_files_returns_zero(self, tmp_path: Path, capsys):
        # Pass an explicit empty list as positional — but the parser accepts
        # zero positionals, so we pass no args and use --root on an empty dir.
        rc = main(["--root", str(tmp_path)])
        assert rc == 0
        out = capsys.readouterr()
        assert "No requirements files found." in out.err

    def test_default_root(self, capsys, monkeypatch, tmp_path: Path):
        # When no --root and no files, default is Path(".")
        # Run inside tmp_path by chdir.
        monkeypatch.chdir(tmp_path)
        # Create a file so we don't hit the "no files" branch.
        (tmp_path / "requirements.txt").write_text("a==1.0\n", encoding="utf-8")
        rc = main([])
        assert rc == 0

    def test_custom_root_finds_files(self, tmp_path: Path, capsys):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "requirements.txt").write_text("a>=1.0\n", encoding="utf-8")
        rc = main(["--root", str(sub)])
        assert rc == 1
        out = capsys.readouterr()
        assert "a" in out.err

    def test_multiple_files(self, tmp_path: Path, capsys):
        f1 = tmp_path / "r1.txt"
        f2 = tmp_path / "r2.txt"
        f1.write_text("alpha-pkg==1.0\n", encoding="utf-8")
        f2.write_text("beta-pkg>=2.0\n", encoding="utf-8")
        rc = main([str(f1), str(f2)])
        assert rc == 1
        out = capsys.readouterr()
        # The pinned dep's name should not appear as a violation.
        assert "alpha-pkg" not in out.err
        # The unpinned dep's name should appear.
        assert "beta-pkg" in out.err
        # The violation file path should appear in the report.
        assert "r2.txt" in out.err

    def test_line_numbers_in_output(self, tmp_path: Path, capsys):
        f = tmp_path / "requirements.txt"
        f.write_text("\n\nx>=1.0\n", encoding="utf-8")
        rc = main([str(f)])
        assert rc == 1
        out = capsys.readouterr()
        assert ":3:" in out.err  # line 3 is where x lives

    def test_explicit_empty_file_arg(self, tmp_path: Path, capsys):
        # An existing but empty file → all-pinned rc=0.
        f = tmp_path / "requirements.txt"
        f.write_text("", encoding="utf-8")
        rc = main([str(f)])
        assert rc == 0

    def test_path_field_in_output(self, tmp_path: Path, capsys):
        f = tmp_path / "myreq.txt"
        f.write_text("a>=1.0\n", encoding="utf-8")
        rc = main([str(f)])
        assert rc == 1
        out = capsys.readouterr()
        assert "myreq.txt" in out.err
