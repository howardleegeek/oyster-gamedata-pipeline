#!/usr/bin/env python3
"""Tests for bin/autoresearch_failure_modes.py"""

from __future__ import annotations

import collections
import io
import os
import pathlib
import subprocess
import sys
import tarfile
import tempfile

# Import the module under test
sys.path.insert(0, str(pathlib.Path(__file__).parents[2] / "bin"))
import autoresearch_failure_modes as afm


class TestClassifyCompileError:
    """Tests for _classify_compile_error function."""

    def test_syntax_error_invalid_syntax(self):
        """Maps syntax errors correctly."""
        err = SyntaxError("invalid syntax")
        assert afm._classify_compile_error(err) == "syntax_error"

    def test_syntax_error_unexpected_indent(self):
        """Maps unexpected indent errors."""
        err = SyntaxError("unexpected indent")
        assert afm._classify_compile_error(err) == "syntax_error"

    def test_undefined_name(self):
        """Maps undefined name errors."""
        err = NameError("name 'foo' is not defined")
        assert afm._classify_compile_error(err) == "undefined_name"

    def test_unused_import(self):
        """Maps unused import errors."""
        err = SyntaxError("assigned to but never used")
        assert afm._classify_compile_error(err) == "unused_import"

    def test_duplicate_argument(self):
        """Maps duplicate keyword argument errors."""
        err = SyntaxError("keyword argument repeated")
        assert afm._classify_compile_error(err) == "duplicate_argument"

    def test_return_outside_function(self):
        """Maps return outside function errors."""
        err = SyntaxError("'return' outside function")
        assert afm._classify_compile_error(err) == "return_outside_function"

    def test_yield_outside_function(self):
        """Maps yield outside function errors."""
        err = SyntaxError("'yield' outside function")
        assert afm._classify_compile_error(err) == "yield_outside_function"

    def test_unknown_error_defaults_to_syntax(self):
        """Unknown errors default to syntax_error."""
        err = RuntimeError("some unknown error")
        assert afm._classify_compile_error(err) == "syntax_error"


class TestLintSource:
    """Tests for _lint_source function."""

    def test_valid_source_returns_empty(self):
        """Valid Python source returns empty list."""
        source = '"""Module docstring."""\nprint("hello")\n'
        result = afm._lint_source(source, "test.py")
        assert result == []

    def test_syntax_error_returns_syntax_error(self):
        """Syntax error returns syntax_error mode."""
        source = "def foo(\n"
        result = afm._lint_source(source, "test.py")
        assert "syntax_error" in result

    def test_star_import(self):
        """Detects wildcard imports."""
        source = '"""Module docstring."""\nfrom os import *\n'
        result = afm._lint_source(source, "test.py")
        assert "star_import" in result

    def test_bare_except(self):
        """Detects bare except clauses."""
        source = '"""Module docstring."""\ntry:\n    pass\nexcept:\n    pass\n'
        result = afm._lint_source(source, "test.py")
        assert "bare_except" in result

    def test_missing_docstring(self):
        """Detects missing module-level docstring."""
        source = "print('hello')\n"
        result = afm._lint_source(source, "test.py")
        assert "missing_docstring" in result

    def test_line_too_long(self):
        """Detects lines over 120 characters."""
        source = '"""Module docstring."""\n' + "x" * 121 + "\n"
        result = afm._lint_source(source, "test.py")
        assert "line_too_long" in result

    def test_trailing_whitespace(self):
        """Detects trailing whitespace."""
        source = '"""Module docstring."""\nprint("hello")   \n'
        result = afm._lint_source(source, "test.py")
        assert "trailing_whitespace" in result

    def test_mixed_indentation(self):
        """Detects mixed tabs and spaces in valid Python."""
        # Use a source that is syntactically valid but has mixed indentation
        # The regex checks for space+tab OR tab+space (mixed), not pure tab
        source = '"""Module docstring."""\nif True:\n \tprint("hello")\n'
        result = afm._lint_source(source, "test.py")
        assert "mixed_indentation" in result


class TestFindTarballs:
    """Tests for _find_tarballs function."""

    def test_finds_tar_gz(self):
        """Finds .tar.gz files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pathlib.Path(tmpdir, "test.tar.gz").touch()
            result = afm._find_tarballs(tmpdir)
            assert len(result) == 1
            assert result[0].endswith("test.tar.gz")

    def test_finds_tgz(self):
        """Finds .tgz files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pathlib.Path(tmpdir, "test.tgz").touch()
            result = afm._find_tarballs(tmpdir)
            assert len(result) == 1

    def test_finds_tar_bz2(self):
        """Finds .tar.bz2 files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pathlib.Path(tmpdir, "test.tar.bz2").touch()
            result = afm._find_tarballs(tmpdir)
            assert len(result) == 1

    def test_finds_tar_xz(self):
        """Finds .tar.xz files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pathlib.Path(tmpdir, "test.tar.xz").touch()
            result = afm._find_tarballs(tmpdir)
            assert len(result) == 1

    def test_ignores_non_tar_files(self):
        """Ignores non-tar files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pathlib.Path(tmpdir, "test.txt").touch()
            pathlib.Path(tmpdir, "test.gz").touch()
            result = afm._find_tarballs(tmpdir)
            assert result == []

    def test_returns_sorted(self):
        """Returns sorted list of tarballs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pathlib.Path(tmpdir, "z.tar.gz").touch()
            pathlib.Path(tmpdir, "a.tar.gz").touch()
            pathlib.Path(tmpdir, "m.tar.gz").touch()
            result = afm._find_tarballs(tmpdir)
            names = [os.path.basename(p) for p in result]
            assert names == ["a.tar.gz", "m.tar.gz", "z.tar.gz"]

    def test_ignores_directories(self):
        """Ignores directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pathlib.Path(tmpdir, "subdir").mkdir()
            pathlib.Path(tmpdir, "test.tar.gz").touch()
            result = afm._find_tarballs(tmpdir)
            assert len(result) == 1


class TestExtractAndLint:
    """Tests for _extract_and_lint function."""

    def _create_tarball(self, tmpdir: str, name: str, content: bytes) -> str:
        """Helper to create a valid tarball with correct sizes."""
        tarball_path = pathlib.Path(tmpdir, name)
        with tarfile.open(tarball_path, "w:gz") as tf:
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
        return str(tarball_path)

    def test_extracts_and_lints_valid_python(self):
        """Extracts tarball and lints Python files."""

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a tarball with a Python file
            tarball_path = self._create_tarball(
                tmpdir, "test.py", b'"""Module docstring."""\nprint("hello")\n'
            )

            extract_dir = pathlib.Path(tmpdir, "extracted")
            extract_dir.mkdir()
            result = afm._extract_and_lint(tarball_path, str(extract_dir))
            assert result == []

    def test_extracts_and_lints_syntax_error(self):
        """Detects syntax errors in extracted Python files."""

        with tempfile.TemporaryDirectory() as tmpdir:
            tarball_path = self._create_tarball(tmpdir, "bad.py", b"def foo(\n")

            extract_dir = pathlib.Path(tmpdir, "extracted")
            extract_dir.mkdir()
            result = afm._extract_and_lint(tarball_path, str(extract_dir))
            assert "syntax_error" in result

    def test_handles_corrupt_tarball(self):
        """Handles corrupt tarball gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tarball_path = pathlib.Path(tmpdir, "bad.tar.gz")
            tarball_path.write_bytes(b"not a tarball")

            extract_dir = pathlib.Path(tmpdir, "extracted")
            extract_dir.mkdir()
            result = afm._extract_and_lint(str(tarball_path), str(extract_dir))
            assert result == []  # Empty list on failure

    def test_ignores_non_python_files(self):
        """Ignores non-Python files."""

        with tempfile.TemporaryDirectory() as tmpdir:
            tarball_path = self._create_tarball(tmpdir, "readme.txt", b"hello")

            extract_dir = pathlib.Path(tmpdir, "extracted")
            extract_dir.mkdir()
            result = afm._extract_and_lint(tarball_path, str(extract_dir))
            assert result == []


class TestReportTop:
    """Tests for _report_top function."""

    def test_reports_top_n(self):
        """Reports top N failure modes."""
        counter = collections.Counter(
            {"syntax_error": 10, "undefined_name": 5, "unused_import": 3}
        )
        # Should not raise
        afm._report_top(counter, 2, 100, 10)

    def test_handles_empty_counter(self):
        """Handles empty counter."""
        counter = collections.Counter()
        afm._report_top(counter, 5, 0, 0)


class TestMainCLI:
    """Tests for main CLI function."""

    def test_help_flag(self):
        """CLI --help works."""
        result = subprocess.run(
            [sys.executable, "bin/autoresearch_failure_modes.py", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "--tarballs" in result.stdout

    def test_requires_tarballs_dir(self):
        """CLI requires --tarballs argument."""
        result = subprocess.run(
            [sys.executable, "bin/autoresearch_failure_modes.py"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0

    def test_invalid_directory(self):
        """CLI handles non-existent directory."""
        result = subprocess.run(
            [
                sys.executable,
                "bin/autoresearch_failure_modes.py",
                "--tarballs",
                "/nonexistent/path/12345",
            ],
            capture_output=True,
            text=True,
        )
        # Returns non-zero for invalid directory
        assert result.returncode != 0

    def test_top_argument(self):
        """CLI accepts --top argument."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                [
                    sys.executable,
                    "bin/autoresearch_failure_modes.py",
                    "--tarballs",
                    tmpdir,
                    "--top",
                    "5",
                ],
                capture_output=True,
                text=True,
            )
            # Returns non-zero when no tarballs found
            assert result.returncode != 0

    def test_verbose_flag(self):
        """CLI accepts -v/--verbose flag."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                [
                    sys.executable,
                    "bin/autoresearch_failure_modes.py",
                    "--tarballs",
                    tmpdir,
                    "-v",
                ],
                capture_output=True,
                text=True,
            )
            # Returns non-zero when no tarballs found
            assert result.returncode != 0
