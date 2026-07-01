#!/usr/bin/env python3
"""Tests for bin/auto_fix_ci_failures.py"""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

# Import the module under test
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "bin"))
import auto_fix_ci_failures


class TestRunCommand:
    """Tests for run_command() function."""

    def test_run_command_success(self):
        """Test run_command returns (0, stdout, stderr) on success."""
        returncode, stdout, stderr = auto_fix_ci_failures.run_command(["echo", "hello"])
        assert returncode == 0
        assert stdout.strip() == "hello"
        assert stderr == ""

    def test_run_command_failure(self):
        """Test run_command returns (non-zero, stdout, stderr) on failure."""
        returncode, stdout, stderr = auto_fix_ci_failures.run_command(["ls", "/nonexistent_path_12345"])
        assert returncode != 0

    def test_run_command_with_cwd(self, tmp_path):
        """Test run_command respects cwd parameter."""
        returncode, stdout, stderr = auto_fix_ci_failures.run_command(["pwd"], cwd=str(tmp_path))
        assert returncode == 0
        assert tmp_path.name in stdout

    def test_run_command_exception(self):
        """Test run_command handles exceptions gracefully."""
        # Invalid command should raise exception caught and return (1, "", error_msg)
        returncode, stdout, stderr = auto_fix_ci_failures.run_command(["nonexistent_command_12345"])
        assert returncode == 1


class TestParseBlackFailure:
    """Tests for parse_black_failure() function."""

    def test_parse_black_failure_simple_pattern(self, tmp_path):
        """Test parsing simple 'would reformat' pattern."""
        # Create a real file to pass the exists() check
        test_file = tmp_path / "test_module.py"
        test_file.write_text("x=1")

        logs = f"would reformat {test_file}"
        result = auto_fix_ci_failures.parse_black_failure(logs)
        assert str(test_file) in result

    def test_parse_black_failure_summary_line(self, tmp_path):
        """Test parsing black --check summary output."""
        test_file = tmp_path / "example.py"
        test_file.write_text("x  =  1")

        # The function requires "black --check" in logs to trigger the context search
        logs = f"""black --check on some files
Oh no! 💥 💥 💥 1 file would be reformatted
{test_file}:1:1 would reformat
"""
        result = auto_fix_ci_failures.parse_black_failure(logs)
        assert str(test_file) in result

    def test_parse_black_failure_no_match(self):
        """Test parse_black_failure returns empty when no match."""
        logs = "Some other CI failure message"
        result = auto_fix_ci_failures.parse_black_failure(logs)
        assert result == []

    def test_parse_black_failure_nonexistent_file(self):
        """Test that nonexistent files are filtered out."""
        logs = "would reformat /nonexistent/path/to/file.py"
        result = auto_fix_ci_failures.parse_black_failure(logs)
        assert result == []


class TestParseRuffFailure:
    """Tests for parse_ruff_failure() function."""

    def test_parse_ruff_failure_found_errors(self, tmp_path):
        """Test parsing 'Found X errors' pattern."""
        test_file = tmp_path / "bad_file.py"
        test_file.write_text("x=1\n")

        logs = f"""Found 1 error in 1 file
{test_file}:1:5: E501 Line too long (81 > 79 characters)
"""
        result = auto_fix_ci_failures.parse_ruff_failure(logs)
        assert str(test_file) in result

    def test_parse_ruff_failure_multiple_files(self, tmp_path):
        """Test parsing multiple files with errors."""
        file1 = tmp_path / "file1.py"
        file1.write_text("x=1\n")
        file2 = tmp_path / "file2.py"
        file2.write_text("x=1\n")

        logs = f"""Found 2 errors in 2 files
{file1}:1:1: E501 Line too long
{file2}:2:3: F401 imported but unused
"""
        result = auto_fix_ci_failures.parse_ruff_failure(logs)
        assert len(result) == 2

    def test_parse_ruff_failure_no_match(self):
        """Test parse_ruff_failure returns empty when no match."""
        logs = "All tests passed!"
        result = auto_fix_ci_failures.parse_ruff_failure(logs)
        assert result == []

    def test_parse_ruff_failure_nonexistent_file(self):
        """Test that nonexistent files are filtered out."""
        logs = "Found 1 error in 1 file\n/nonexistent/file.py:1:1: E501"
        result = auto_fix_ci_failures.parse_ruff_failure(logs)
        assert result == []


class TestParseMissingImports:
    """Tests for parse_missing_imports() function."""

    def test_parse_missing_imports_simple(self, tmp_path):
        """Test parsing simple 'No module named' pattern."""
        test_file = tmp_path / "missing_import.py"
        test_file.write_text("x=1\n")

        logs = f"""{test_file}:1:1: E402 Module import error
ModuleNotFoundError: No module named 'requests'
"""
        result = auto_fix_ci_failures.parse_missing_imports(logs)
        assert str(test_file) in result

    def test_parse_missing_imports_multiple(self, tmp_path):
        """Test parsing multiple missing imports."""
        test_file = tmp_path / "test_file.py"
        test_file.write_text("x=1\n")

        logs = f"""{test_file}:1:1: E402
ModuleNotFoundError: No module named 'numpy'
ModuleNotFoundError: No module named 'pandas'
"""
        result = auto_fix_ci_failures.parse_missing_imports(logs)
        assert str(test_file) in result

    def test_parse_missing_imports_no_match(self):
        """Test parse_missing_imports returns empty when no match."""
        logs = "Some other error message"
        result = auto_fix_ci_failures.parse_missing_imports(logs)
        assert result == {}


class TestApplyBlackFix:
    """Tests for apply_black_fix() function."""

    def test_apply_black_fix_empty_list(self):
        """Test apply_black_fix with empty file list returns True."""
        result = auto_fix_ci_failures.apply_black_fix([])
        assert result is True

    @patch("auto_fix_ci_failures.run_command")
    def test_apply_black_fix_success(self, mock_run):
        """Test successful black fix application."""
        mock_run.return_value = (0, "reformatted 1 file", "")
        result = auto_fix_ci_failures.apply_black_fix(["/path/to/file.py"])
        assert result is True

    @patch("auto_fix_ci_failures.run_command")
    def test_apply_black_fix_failure(self, mock_run):
        """Test black fix failure handling."""
        mock_run.return_value = (1, "", "black not found")
        result = auto_fix_ci_failures.apply_black_fix(["/path/to/file.py"])
        assert result is False


class TestApplyRuffFix:
    """Tests for apply_ruff_fix() function."""

    def test_apply_ruff_fix_empty_list(self):
        """Test apply_ruff_fix with empty file list returns True."""
        result = auto_fix_ci_failures.apply_ruff_fix([])
        assert result is True

    @patch("auto_fix_ci_failures.run_command")
    def test_apply_ruff_fix_success(self, mock_run):
        """Test successful ruff fix application."""
        # First call is --version check, second is actual fix
        mock_run.side_effect = [(0, "ruff 0.1.0", ""), (0, "Fixed 1 file", "")]
        result = auto_fix_ci_failures.apply_ruff_fix(["/path/to/file.py"])
        assert result is True

    @patch("auto_fix_ci_failures.run_command")
    def test_apply_ruff_fix_version_check_fails(self, mock_run):
        """Test ruff fix when --version check fails."""
        # First call (--version) fails, second uses ruff check
        mock_run.side_effect = [(1, "", "not found"), (0, "Fixed", "")]
        result = auto_fix_ci_failures.apply_ruff_fix(["/path/to/file.py"])
        assert result is True

    @patch("auto_fix_ci_failures.run_command")
    def test_apply_ruff_fix_failure(self, mock_run):
        """Test ruff fix failure handling."""
        mock_run.side_effect = [(0, "ruff 0.1.0", ""), (1, "", "error")]
        result = auto_fix_ci_failures.apply_ruff_fix(["/path/to/file.py"])
        assert result is False


class TestAddMissingImports:
    """Tests for add_missing_imports() function."""

    def test_add_missing_imports_empty_dict(self):
        """Test add_missing_imports with empty dict returns True."""
        result = auto_fix_ci_failures.add_missing_imports({})
        assert result is True

    def test_add_missing_imports_simple(self, tmp_path):
        """Test adding simple import."""
        test_file = tmp_path / "test.py"
        test_file.write_text("x = 1\n")

        result = auto_fix_ci_failures.add_missing_imports({str(test_file): ["requests"]})
        assert result is True

        # Verify import was added
        content = test_file.read_text()
        assert "import requests" in content

    def test_add_missing_imports_from_clause(self, tmp_path):
        """Test adding 'from X import Y' style import."""
        test_file = tmp_path / "test.py"
        test_file.write_text("x = 1\n")

        result = auto_fix_ci_failures.add_missing_imports({str(test_file): ["json from json"]})
        assert result is True

        content = test_file.read_text()
        assert "from json import json" in content

    def test_add_missing_imports_with_existing_imports(self, tmp_path):
        """Test adding import after existing imports."""
        test_file = tmp_path / "test.py"
        test_file.write_text("import os\nimport sys\n\nx = 1\n")

        result = auto_fix_ci_failures.add_missing_imports({str(test_file): ["requests"]})
        assert result is True

        content = test_file.read_text()
        assert "import requests" in content
        # Original imports should still be there
        assert "import os" in content
        assert "import sys" in content


class TestCommitChanges:
    """Tests for commit_changes() function."""

    @patch("auto_fix_ci_failures.run_command")
    def test_commit_changes_success(self, mock_run):
        """Test successful commit."""
        # Mock git commands: status, add, commit, tag
        mock_run.side_effect = [
            (0, "M file.py", ""),  # git status
            (0, "", ""),  # git add
            (0, "[main abc1234] auto-fix-ci", ""),  # git commit
            (0, "", ""),  # git tag
        ]
        result = auto_fix_ci_failures.commit_changes()
        assert result is True

    @patch("auto_fix_ci_failures.run_command")
    def test_commit_changes_no_changes(self, mock_run):
        """Test commit with no changes returns True."""
        mock_run.return_value = (0, "", "")
        result = auto_fix_ci_failures.commit_changes()
        assert result is True


class TestMain:
    """Tests for main() CLI function."""

    def test_main_no_logs(self):
        """Test main with no logs returns 0."""
        with patch("auto_fix_ci_failures.get_failed_logs") as mock_get:
            mock_get.return_value = ""
            result = auto_fix_ci_failures.main(["--log-file", "/nonexistent"])
            assert result == 1

    def test_main_dry_run(self, tmp_path):
        """Test main with dry-run flag."""
        log_file = tmp_path / "ci_log.txt"
        log_file.write_text("would reformat /nonexistent/file.py")

        with patch("auto_fix_ci_failures.get_failed_logs") as mock_get:
            mock_get.return_value = ""
            result = auto_fix_ci_failures.main(["--dry-run", "--log-file", str(log_file)])
            assert result == 0

    def test_main_invalid_log_file(self):
        """Test main with invalid log file returns 1."""
        result = auto_fix_ci_failures.main(["--log-file", "/nonexistent/path/log.txt"])
        assert result == 1

    @patch("auto_fix_ci_failures.get_failed_logs")
    @patch("auto_fix_ci_failures.parse_black_failure")
    @patch("auto_fix_ci_failures.parse_ruff_failure")
    @patch("auto_fix_ci_failures.parse_missing_imports")
    def test_main_no_fixable_issues(self, mock_missing, mock_ruff, mock_black, mock_get):
        """Test main when no fixable issues found."""
        mock_get.return_value = "Some unrelated CI error"
        mock_black.return_value = []
        mock_ruff.return_value = []
        mock_missing.return_value = {}

        result = auto_fix_ci_failures.main([])
        assert result == 0


class TestIntegration:
    """Integration-style tests using subprocess."""

    def test_cli_help(self):
        """Test CLI --help works."""
        result = subprocess.run(
            [sys.executable, "-m", "auto_fix_ci_failures", "--help"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parent.parent.parent / "bin"),
        )
        assert result.returncode == 0
        assert "--dry-run" in result.stdout
        assert "--log-file" in result.stdout
