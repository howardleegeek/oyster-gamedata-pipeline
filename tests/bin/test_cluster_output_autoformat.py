#!/usr/bin/env python3
"""
Tests for bin/cluster_output_autoformat.py — Pre-commit auto-formatter for cluster code.

Tests: _find_staged_python_files, _validate_syntax, _run_formatter,
format_files, build_parser, main.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Import the module under test
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import bin.cluster_output_autoformat as cluster_output_autoformat


class TestFindStagedPythonFiles:
    """Tests for _find_staged_python_files function."""

    def test_returns_staged_python_files(self):
        """Verify staged Python files are returned from git diff --cached."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="src/foo.py\n  bar/baz.py\nlib/test.py\n",
            )
            result = cluster_output_autoformat._find_staged_python_files()
            assert "src/foo.py" in result
            assert "bar/baz.py" in result
            assert "lib/test.py" in result

    def test_filters_non_python_files(self):
        """Verify non-Python files are filtered out."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="src/foo.py\nreadme.txt\nlib/test.py\ndata.json\n",
            )
            result = cluster_output_autoformat._find_staged_python_files()
            assert len(result) == 2
            assert "src/foo.py" in result
            assert "lib/test.py" in result
            assert "readme.txt" not in result
            assert "data.json" not in result

    def test_handles_git_not_available(self):
        """Verify fallback to glob when git is not available."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("git not found")
            with patch("pathlib.Path.rglob") as mock_rglob:
                mock_rglob.return_value = [Path("test.py")]
                result = cluster_output_autoformat._find_staged_python_files()
                assert "test.py" in result

    def test_handles_subprocess_error(self):
        """Verify graceful handling when git diff fails."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "git")
            with patch("pathlib.Path.rglob") as mock_rglob:
                mock_rglob.return_value = [Path("test.py")]
                result = cluster_output_autoformat._find_staged_python_files()
                assert "test.py" in result


class TestValidateSyntax:
    """Tests for _validate_syntax function."""

    def test_valid_python_syntax_returns_true(self):
        """Verify valid Python syntax returns True."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            f.write("def hello():\n    print('world')\n")
            f.flush()
            result = cluster_output_autoformat._validate_syntax(f.name)
            assert result is True
            Path(f.name).unlink()

    def test_invalid_python_syntax_returns_false(self):
        """Verify invalid Python syntax returns False."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            f.write("def hello():\n    print('world'\n")  # missing closing paren
            f.flush()
            result = cluster_output_autoformat._validate_syntax(f.name)
            assert result is False
            Path(f.name).unlink()

    def test_empty_file_returns_true(self):
        """Verify empty file is considered valid."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            f.write("")
            f.flush()
            result = cluster_output_autoformat._validate_syntax(f.name)
            assert result is True
            Path(f.name).unlink()


class TestRunFormatter:
    """Tests for _run_formatter function."""

    def test_returns_zero_when_formatter_succeeds(self):
        """Verify formatter success returns 0."""
        with patch("shutil.which") as mock_which:
            mock_which.return_value = "/usr/bin/black"
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stderr="")
                result = cluster_output_autoformat._run_formatter(
                    ["black"], ["test.py"]
                )
                assert result == 0

    def test_returns_nonzero_when_formatter_fails(self):
        """Verify formatter failure returns non-zero."""
        with patch("shutil.which") as mock_which:
            mock_which.return_value = "/usr/bin/black"
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=1, stderr="error message"
                )
                result = cluster_output_autoformat._run_formatter(
                    ["black"], ["test.py"]
                )
                assert result == 1

    def test_returns_one_when_formatter_missing(self):
        """Verify missing formatter returns 1."""
        with patch("shutil.which") as mock_which:
            mock_which.return_value = None
            result = cluster_output_autoformat._run_formatter(
                ["nonexistent_formatter"], ["test.py"]
            )
            assert result == 1


class TestFormatFiles:
    """Tests for format_files function."""

    def test_runs_black_and_ruff(self):
        """Verify format_files runs both black and ruff."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            f.write("x = 1\n")
            f.flush()
            temp_path = f.name
        try:
            with patch("shutil.which") as mock_which:
                mock_which.return_value = "/usr/bin/formatter"
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock(returncode=0, stderr="")
                    result = cluster_output_autoformat.format_files([temp_path])
                    assert result == 0
                    assert mock_run.call_count == 2  # black + ruff
        finally:
            Path(temp_path).unlink()

    def test_dry_run_checks_without_modifying(self):
        """Verify dry_run only checks without modifying."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            f.write("x = 1\n")
            f.flush()
            temp_path = f.name
        try:
            with patch("shutil.which") as mock_which:
                mock_which.return_value = "/usr/bin/formatter"
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock(returncode=0, stderr="")
                    result = cluster_output_autoformat.format_files(
                        [temp_path], dry_run=True
                    )
                    assert result == 0
        finally:
            Path(temp_path).unlink()


class TestMain:
    """Tests for main function."""

    def test_exit_code_zero_on_success(self):
        """Verify main returns 0 on successful format."""
        with patch.object(
            cluster_output_autoformat, "format_files", return_value=0
        ):
            result = cluster_output_autoformat.main(["file.py"])
            assert result == 0

    def test_exit_code_one_on_failure(self):
        """Verify main returns 1 on format failure."""
        with patch.object(
            cluster_output_autoformat, "format_files", return_value=1
        ):
            result = cluster_output_autoformat.main(["file.py"])
            assert result == 1

    def test_exit_code_two_on_cli_error(self):
        """Verify main returns 2 on CLI usage error."""
        with pytest.raises(SystemExit):
            cluster_output_autoformat.main(["--invalid"])

    def test_dry_run_passed_to_format_files(self):
        """Verify --dry-run flag is passed to format_files."""
        with patch.object(
            cluster_output_autoformat, "format_files", return_value=0
        ) as mock_format:
            cluster_output_autoformat.main(["--dry-run", "file.py"])
            mock_format.assert_called_once()
            _, kwargs = mock_format.call_args
            assert kwargs.get("dry_run") is True

    def test_staged_flag_enables_staged_detection(self):
        """Verify --staged flag is parsed."""
        with patch.object(
            cluster_output_autoformat,
            "_find_staged_python_files",
            return_value=["staged.py"],
        ):
            with patch.object(
                cluster_output_autoformat, "format_files", return_value=0
            ) as mock_format:
                result = cluster_output_autoformat.main(["--staged"])
                assert result == 0
                mock_format.assert_called_once()
                call_args = mock_format.call_args[0][0]
                assert "staged.py" in call_args
