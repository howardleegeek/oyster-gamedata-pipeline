#!/usr/bin/env python3
"""Tests for bin/aggregate_sprint_report.py"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

# Import the module under test
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "bin"))
import aggregate_sprint_report


class TestAggregate:
    """Tests for aggregate() function."""

    def test_aggregate_single_iteration(self, tmp_path):
        """Test aggregate with a single iteration log file."""
        # Create a mock iter_*.json file
        iter_file = tmp_path / "iter_001.json"
        iter_file.write_text(
            json.dumps(
                {
                    "lint_exit": 0,
                    "total_seconds": 120.5,
                    "capture_seconds": 80.0,
                    "adapt_seconds": 40.5,
                    "records": 1000,
                }
            )
        )

        output_file = tmp_path / "report.md"
        result = aggregate_sprint_report.aggregate(str(tmp_path), str(output_file))

        # Verify result structure
        assert result["iter_count"] == 1
        assert result["pass_count"] == 1
        assert result["fail_count"] == 0
        assert result["total_seconds_mean"] == 120.5
        assert output_file.exists()

    def test_aggregate_multiple_iterations(self, tmp_path):
        """Test aggregate with multiple iteration log files."""
        # Create multiple iteration files with mixed pass/fail
        test_data = [
            {"lint_exit": 0, "total_seconds": 100.0, "capture_seconds": 60.0, "adapt_seconds": 40.0, "records": 100},
            {"lint_exit": 1, "total_seconds": 150.0, "capture_seconds": 90.0, "adapt_seconds": 60.0, "records": 200},
            {"lint_exit": 0, "total_seconds": 80.0, "capture_seconds": 50.0, "adapt_seconds": 30.0, "records": 150},
        ]

        for i, data in enumerate(test_data, start=1):
            iter_file = tmp_path / f"iter_{i:03d}.json"
            iter_file.write_text(json.dumps(data))

        output_file = tmp_path / "report.md"
        result = aggregate_sprint_report.aggregate(str(tmp_path), str(output_file))

        # Verify aggregated metrics
        assert result["iter_count"] == 3
        assert result["pass_count"] == 2
        assert result["fail_count"] == 1
        assert result["total_seconds_mean"] == pytest.approx(110.0)
        assert result["total_seconds_p50"] == 100.0
        assert result["total_seconds_p95"] == 150.0

    def test_aggregate_empty_directory_raises(self, tmp_path):
        """Test that empty directory raises FileNotFoundError."""
        output_file = tmp_path / "report.md"
        with pytest.raises(FileNotFoundError, match="No iter_\\*.json files found"):
            aggregate_sprint_report.aggregate(str(tmp_path), str(output_file))

    def test_aggregate_with_missing_optional_fields(self, tmp_path):
        """Test aggregate handles missing optional fields gracefully."""
        # Create file without optional "lint_exit" field
        iter_file = tmp_path / "iter_001.json"
        iter_file.write_text(
            json.dumps(
                {
                    "total_seconds": 100.0,
                    "capture_seconds": 60.0,
                    "adapt_seconds": 40.0,
                    "records": 100,
                    # lint_exit is missing - should default to 1 (fail)
                }
            )
        )

        output_file = tmp_path / "report.md"
        result = aggregate_sprint_report.aggregate(str(tmp_path), str(output_file))

        # Missing lint_exit should default to 1 (fail)
        assert result["iter_count"] == 1
        assert result["pass_count"] == 0
        assert result["fail_count"] == 1


class TestCLI:
    """Tests for CLI entry point via subprocess."""

    def test_cli_missing_log_dir_argument(self, tmp_path):
        """Test CLI exits with error when --log-dir is missing."""
        output_file = tmp_path / "report.md"
        result = subprocess.run(
            [sys.executable, "bin/aggregate_sprint_report.py", "--output", str(output_file)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2  # argparse exits with 2 for missing required arg

    def test_cli_missing_output_argument(self, tmp_path):
        """Test CLI exits with error when --output is missing."""
        result = subprocess.run(
            [sys.executable, "bin/aggregate_sprint_report.py", "--log-dir", str(tmp_path)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2

    def test_cli_with_valid_args(self, tmp_path):
        """Test CLI with valid arguments produces output."""
        # Create a valid iteration file
        iter_file = tmp_path / "iter_001.json"
        iter_file.write_text(
            json.dumps(
                {
                    "lint_exit": 0,
                    "total_seconds": 100.0,
                    "capture_seconds": 60.0,
                    "adapt_seconds": 40.0,
                    "records": 100,
                }
            )
        )

        output_file = tmp_path / "report.md"
        result = subprocess.run(
            [sys.executable, "bin/aggregate_sprint_report.py", "--log-dir", str(tmp_path), "--output", str(output_file)],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parent.parent.parent,
        )

        assert result.returncode == 0
        assert output_file.exists()
        assert "Report written to" in result.stdout
