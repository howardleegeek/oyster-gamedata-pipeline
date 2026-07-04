#!/usr/bin/env python3
"""
Tests for bin/sprint_dashboard.py
"""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add parent directory to path to import module
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from bin.sprint_dashboard import (
    build_dashboard,
    count_files_by_dir,
    get_git_log_summary,
    main,
    parse_test_pass_rate,
)


def test_get_git_log_summary_parses_format():
    """Test that git log output is parsed correctly."""
    # Mock subprocess.run to return test data
    mock_output = """abc123|Howard Li|feat: bin/mc_launcher_real.py (Aliyun R001)|2026-05-02 18:25:00 +0800
def456|Howard Li|fix: bin/spectator_follow.py (Aliyun R002)|2026-05-02 18:20:00 +0800"""

    with patch("subprocess.run") as mock_run:
        mock_result = MagicMock()
        mock_result.stdout = mock_output
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        commits = get_git_log_summary("1 day ago")

        assert len(commits) == 2
        assert commits[0]["sha"] == "abc123"
        assert commits[0]["author"] == "Howard Li"
        assert commits[0]["message"] == "feat: bin/mc_launcher_real.py (Aliyun R001)"
        assert commits[0]["date"] == "2026-05-02 18:25:00 +0800"

        assert commits[1]["sha"] == "def456"
        assert commits[1]["author"] == "Howard Li"
        assert commits[1]["message"] == "fix: bin/spectator_follow.py (Aliyun R002)"


def test_get_git_log_summary_handles_empty():
    """Test empty git log output."""
    with patch("subprocess.run") as mock_run:
        mock_result = MagicMock()
        mock_result.stdout = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        commits = get_git_log_summary("1 day ago")
        assert commits == []


def test_get_git_log_summary_git_not_found():
    """Test when git is not found."""
    with patch("subprocess.run", side_effect=FileNotFoundError("git not found")):
        # Should exit with code 2
        try:
            get_git_log_summary("1 day ago")
            assert False, "Should have exited"
        except SystemExit as e:
            assert e.code == 2


def test_count_files_by_dir_handles_empty():
    """Test counting files in empty directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        counts = count_files_by_dir(tmpdir)
        assert counts == {"": {"py": 0, "sh": 0, "md": 0, "total": 0}}


def test_count_files_by_dir_counts_correctly():
    """Test counting files by type in directory structure."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create test directory structure
        os.makedirs(os.path.join(tmpdir, "src"))
        os.makedirs(os.path.join(tmpdir, "bin"))
        os.makedirs(os.path.join(tmpdir, "tests"))

        # Create test files
        open(os.path.join(tmpdir, "src", "module1.py"), "w").close()
        open(os.path.join(tmpdir, "src", "module2.py"), "w").close()
        open(os.path.join(tmpdir, "src", "README.md"), "w").close()

        open(os.path.join(tmpdir, "bin", "script.sh"), "w").close()
        open(os.path.join(tmpdir, "bin", "helper.py"), "w").close()

        open(os.path.join(tmpdir, "tests", "test_module1.py"), "w").close()
        open(os.path.join(tmpdir, "tests", "test_module2.py"), "w").close()

        open(os.path.join(tmpdir, "main.py"), "w").close()
        open(os.path.join(tmpdir, "README.md"), "w").close()

        counts = count_files_by_dir(tmpdir)

        # Check counts
        assert counts[""]["py"] == 1  # main.py
        assert counts[""]["sh"] == 0
        assert counts[""]["md"] == 1  # README.md
        assert counts[""]["total"] == 2

        assert counts["src"]["py"] == 2
        assert counts["src"]["sh"] == 0
        assert counts["src"]["md"] == 1
        assert counts["src"]["total"] == 3

        assert counts["bin"]["py"] == 1
        assert counts["bin"]["sh"] == 1
        assert counts["bin"]["md"] == 0
        assert counts["bin"]["total"] == 2

        assert counts["tests"]["py"] == 2
        assert counts["tests"]["sh"] == 0
        assert counts["tests"]["md"] == 0
        assert counts["tests"]["total"] == 2


def test_parse_test_pass_rate_extracts_numbers():
    """Test parsing pytest output with passed/failed counts."""
    # Test with both passed and failed
    output1 = "12 passed, 3 failed in 0.5s"
    passed1, failed1, pct1 = parse_test_pass_rate(output1)
    assert passed1 == 12
    assert failed1 == 3
    assert pct1 == 80.0  # 12/15 = 80%

    # Test with only passed
    output2 = "15 passed in 0.3s"
    passed2, failed2, pct2 = parse_test_pass_rate(output2)
    assert passed2 == 15
    assert failed2 == 0
    assert pct2 == 100.0

    # Test with different formatting
    output3 = "8 passed, 2 failed, 1 warning in 1.2s"
    passed3, failed3, pct3 = parse_test_pass_rate(output3)
    assert passed3 == 8
    assert failed3 == 2
    assert pct3 == 80.0  # 8/10 = 80%


def test_parse_test_pass_rate_handles_no_match():
    """Test parsing pytest output with no match."""
    output = "No tests ran"
    passed, failed, pct = parse_test_pass_rate(output)
    assert passed == 0
    assert failed == 0
    assert pct == 0.0

    # Empty string
    passed2, failed2, pct2 = parse_test_pass_rate("")
    assert passed2 == 0
    assert failed2 == 0
    assert pct2 == 0.0


def test_build_dashboard_produces_markdown():
    """Test that build_dashboard produces valid markdown."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a minimal git repo structure
        os.makedirs(os.path.join(tmpdir, ".git"))

        # Mock git log to return test data
        mock_git_output = """abc123|Howard Li|feat: test feature|2026-05-02 18:25:00 +0800"""

        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.stdout = mock_git_output
            mock_result.returncode = 0
            mock_run.return_value = mock_result

            # Create a dummy pytest output file
            pytest_file = os.path.join(tmpdir, "pytest_output.txt")
            with open(pytest_file, "w") as f:
                f.write("12 passed, 3 failed in 0.5s")

            dashboard = build_dashboard(
                repo_root=tmpdir, since="1 day ago", sprint_name="Test Sprint"
            )

        # Check basic structure
        assert "# Test Sprint Progress ·" in dashboard
        assert "## Summary" in dashboard
        assert "## Recent commits (last 24h)" in dashboard
        assert "## File counts (production code)" in dashboard
        assert "## Sprint queue status" in dashboard
        assert "## Build health" in dashboard
        assert "## Next 5 in queue" in dashboard

        # Check specific content. The dashboard renders fields with bold
        # markdown (e.g. `- **Tests**: 12 passed / 3 failed`), so assertions
        # match the actual rendered shape rather than a stripped variant.
        assert "Commits today" in dashboard
        assert "**Tests**: 12 passed / 3 failed" in dashboard
        assert "✅ Done: R001-R007" in dashboard
        assert "🟡 In flight: R008" in dashboard


def test_build_dashboard_no_pytest_file():
    """Test build_dashboard when pytest output file doesn't exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, ".git"))

        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.stdout = ""
            mock_result.returncode = 0
            mock_run.return_value = mock_result

            dashboard = build_dashboard(repo_root=tmpdir)

            # Should still work without pytest file. Match the bold-rendered shape.
            assert "**Tests**: 0 passed / 0 failed (0.0%)" in dashboard


def test_main_writes_to_file():
    """Test main function writes to file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        output_file = os.path.join(tmpdir, "dashboard.md")

        # Mock git to avoid actual git calls
        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.stdout = ""
            mock_result.returncode = 0
            mock_run.return_value = mock_result

            # Run main with output file
            exit_code = main(
                [
                    "--since",
                    "1 day ago",
                    "--sprint-name",
                    "Test Sprint",
                    "--output",
                    output_file,
                    "--repo-root",
                    tmpdir,
                ]
            )

            assert exit_code == 0
            assert os.path.exists(output_file)

            # Check file content
            with open(output_file) as f:
                content = f.read()
                assert "# Test Sprint Progress ·" in content


def test_main_stdout():
    """Test main function outputs to stdout."""
    with patch("subprocess.run") as mock_run:
        mock_result = MagicMock()
        mock_result.stdout = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        with patch("sys.stdout") as mock_stdout:
            mock_stdout.write = MagicMock()

            exit_code = main(["--since", "1 day ago", "--repo-root", "."])

            assert exit_code == 0
            # Should have called write
            assert mock_stdout.write.called


def test_main_error_writing_file():
    """Test main handles file write errors."""
    with patch("subprocess.run") as mock_run:
        mock_result = MagicMock()
        mock_result.stdout = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        # Try to write to a directory (should fail)
        exit_code = main(
            [
                "--since",
                "1 day ago",
                "--output",
                "/",  # Root directory, can't write file here
                "--repo-root",
                ".",
            ]
        )

        # Should return error code 1
        assert exit_code == 1


def test_build_dashboard_logs_debug_on_unreadable_pytest_output(caplog):
    """Surface silent error: when pytest_output.txt can't be read/parsed,
    build_dashboard should log at DEBUG level (was: bare `pass`)."""
    import logging

    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, ".git"))

        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.stdout = ""
            mock_result.returncode = 0
            mock_run.return_value = mock_result

            # Create a pytest output file with garbage that will fail to parse
            pytest_file = os.path.join(tmpdir, "pytest_output.txt")
            with open(pytest_file, "w") as f:
                f.write("this is not valid pytest output at all")

            # Force open() to raise so the except branch fires
            with patch("builtins.open", side_effect=OSError("simulated read failure")):
                with caplog.at_level(logging.DEBUG, logger="bin.sprint_dashboard"):
                    dashboard = build_dashboard(
                        repo_root=tmpdir, since="1 day ago", sprint_name="Test"
                    )

            # Control flow: dashboard still builds, with 0/0 tests
            assert "**Tests**: 0 passed / 0 failed" in dashboard
            # And the silent error is now surfaced
            assert any(
                "pytest output" in rec.message.lower()
                for rec in caplog.records
                if rec.levelno == logging.DEBUG
            ), "Expected a DEBUG log record naming the pytest output file"


def test_build_dashboard_falls_back_silently_when_pytest_output_missing(caplog):
    """When pytest_output.txt doesn't exist at all, no log is needed
    (file absence is the expected path, not an error)."""
    import logging

    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, ".git"))

        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.stdout = ""
            mock_result.returncode = 0
            mock_run.return_value = mock_result

            with caplog.at_level(logging.DEBUG, logger="bin.sprint_dashboard"):
                dashboard = build_dashboard(repo_root=tmpdir, sprint_name="Test")

            assert "**Tests**: 0 passed / 0 failed" in dashboard
            # No debug record should fire — file just doesn't exist (expected).
            assert not any(
                "pytest output" in rec.message.lower()
                for rec in caplog.records
            ), "No log expected when pytest_output.txt is simply absent"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
