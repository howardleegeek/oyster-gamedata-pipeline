#!/usr/bin/env python3
"""
Tests for bin/ci_health_check.py — CI health probe.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

# Import the module under test
from bin import ci_health_check


class TestParseArgs:
    """Tests for parse_args function."""

    def test_defaults(self):
        """Test default argument values."""
        args = ci_health_check.parse_args([])
        assert args.ci_logs_dir == Path("ci_logs")
        assert args.days == 7
        assert args.min_lint_pass_rate == 0.95
        assert args.min_test_count == 100
        assert args.min_redteam_coverage == 0.80
        assert args.output is None
        assert args.verbose is False

    def test_custom_values(self):
        """Test custom argument values."""
        args = ci_health_check.parse_args([
            "--ci-logs-dir", "/tmp/ci",
            "--days", "14",
            "--min-lint-pass-rate", "0.90",
            "--min-test-count", "50",
            "--min-redteam-coverage", "0.70",
            "--output", "/tmp/report.json",
            "-v",
        ])
        assert args.ci_logs_dir == Path("/tmp/ci")
        assert args.days == 14
        assert args.min_lint_pass_rate == 0.90
        assert args.min_test_count == 50
        assert args.min_redteam_coverage == 0.70
        assert args.output == Path("/tmp/report.json")
        assert args.verbose is True


class TestSafeJson:
    """Tests for _safe_json function."""

    def test_valid_json(self, tmp_path):
        """Test parsing valid JSON."""
        json_file = tmp_path / "data.json"
        json_file.write_text('{"key": "value", "num": 42}')
        result = ci_health_check._safe_json(json_file)
        assert result == {"key": "value", "num": 42}

    def test_invalid_json(self, tmp_path):
        """Test handling invalid JSON."""
        json_file = tmp_path / "bad.json"
        json_file.write_text('{"key": invalid}')
        result = ci_health_check._safe_json(json_file)
        assert result is None

    def test_missing_file(self, tmp_path):
        """Test handling missing file."""
        result = ci_health_check._safe_json(tmp_path / "nonexistent.json")
        assert result is None


class TestAnalyzeCiLogs:
    """Tests for analyze_ci_logs function."""

    def test_empty_directory(self, tmp_path):
        """Test with empty directory."""
        result = ci_health_check.analyze_ci_logs(tmp_path, 7)
        assert result["lint_pass_rate"] == 0.0
        assert result["total_test_count"] == 0
        assert result["redteam_coverage"] == 0.0
        assert result["total_runs"] == 0

    def test_nonexistent_directory(self):
        """Test with nonexistent directory."""
        result = ci_health_check.analyze_ci_logs(Path("/nonexistent/path"), 7)
        assert result["lint_pass_rate"] == 0.0
        assert result["total_test_count"] == 0

    def test_parse_lint_json(self, tmp_path):
        """Test parsing lint JSON file."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        lint_file = log_dir / "lint_20260101.json"
        lint_file.write_text(json.dumps({"pass_rate": 0.98}))
        
        # Create file with recent timestamp
        import time
        recent_time = time.time()
        os.utime(lint_file, (recent_time, recent_time))
        
        result = ci_health_check.analyze_ci_logs(log_dir, 7)
        assert result["lint_pass_rate"] == 0.98

    def test_parse_test_json(self, tmp_path):
        """Test parsing test count JSON file."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        test_file = log_dir / "test_results.json"
        test_file.write_text(json.dumps({"test_count": 500}))
        
        import time
        recent_time = time.time()
        os.utime(test_file, (recent_time, recent_time))
        
        result = ci_health_check.analyze_ci_logs(log_dir, 7)
        assert result["total_test_count"] == 500

    def test_parse_redteam_json(self, tmp_path):
        """Test parsing redteam coverage JSON file."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        coverage_file = log_dir / "redteam_coverage.json"
        coverage_file.write_text(json.dumps({"coverage": 0.88}))
        
        import time
        recent_time = time.time()
        os.utime(coverage_file, (recent_time, recent_time))
        
        result = ci_health_check.analyze_ci_logs(log_dir, 7)
        assert result["redteam_coverage"] == 0.88

    def test_count_runs(self, tmp_path):
        """Test counting total runs."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        
        # Create multiple files
        (log_dir / "lint.log").write_text("lint output")
        (log_dir / "test.log").write_text("test output")
        (log_dir / "fail.log").write_text("fail output")
        
        import time
        recent_time = time.time()
        for f in log_dir.glob("*.log"):
            os.utime(f, (recent_time, recent_time))
        
        result = ci_health_check.analyze_ci_logs(log_dir, 7)
        assert result["total_runs"] == 3
        assert result["failed_runs"] == 1  # fail.log
        assert result["successful_runs"] == 2  # lint.log, test.log


class TestEvaluate:
    """Tests for evaluate function."""

    def test_all_pass(self):
        """Test when all metrics pass."""
        metrics = {
            "lint_pass_rate": 0.96,
            "total_test_count": 150,
            "redteam_coverage": 0.85,
        }
        passed, failures = ci_health_check.evaluate(metrics, 0.95, 100, 0.80)
        assert passed is True
        assert failures == []

    def test_lint_fail(self):
        """Test lint pass rate failure."""
        metrics = {
            "lint_pass_rate": 0.90,
            "total_test_count": 150,
            "redteam_coverage": 0.85,
        }
        passed, failures = ci_health_check.evaluate(metrics, 0.95, 100, 0.80)
        assert passed is False
        assert any("lint_pass_rate" in f for f in failures)

    def test_test_count_fail(self):
        """Test test count failure."""
        metrics = {
            "lint_pass_rate": 0.96,
            "total_test_count": 50,
            "redteam_coverage": 0.85,
        }
        passed, failures = ci_health_check.evaluate(metrics, 0.95, 100, 0.80)
        assert passed is False
        assert any("total_test_count" in f for f in failures)

    def test_redteam_fail(self):
        """Test redteam coverage failure."""
        metrics = {
            "lint_pass_rate": 0.96,
            "total_test_count": 150,
            "redteam_coverage": 0.70,
        }
        passed, failures = ci_health_check.evaluate(metrics, 0.95, 100, 0.80)
        assert passed is False
        assert any("redteam_coverage" in f for f in failures)

    def test_multiple_failures(self):
        """Test multiple metric failures."""
        metrics = {
            "lint_pass_rate": 0.80,
            "total_test_count": 50,
            "redteam_coverage": 0.60,
        }
        passed, failures = ci_health_check.evaluate(metrics, 0.95, 100, 0.80)
        assert passed is False
        assert len(failures) == 3


class TestMain:
    """Tests for main function."""

    def test_main_pass(self, tmp_path, capsys):
        """Test main function with passing metrics."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        
        # Create passing metrics
        (log_dir / "lint.json").write_text(json.dumps({"pass_rate": 0.98}))
        (log_dir / "test.json").write_text(json.dumps({"test_count": 200}))
        (log_dir / "coverage.json").write_text(json.dumps({"coverage": 0.90}))
        
        import time
        recent_time = time.time()
        for f in log_dir.glob("*.json"):
            os.utime(f, (recent_time, recent_time))
        
        with patch.object(sys, "argv", ["ci_health_check", "--ci-logs-dir", str(log_dir)]):
            exit_code = ci_health_check.main()
        
        assert exit_code == 0

    def test_main_fail(self, tmp_path):
        """Test main function with failing metrics."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        
        # Create failing metrics (too low)
        (log_dir / "lint.json").write_text(json.dumps({"pass_rate": 0.50}))
        (log_dir / "test.json").write_text(json.dumps({"test_count": 10}))
        
        import time
        recent_time = time.time()
        for f in log_dir.glob("*.json"):
            os.utime(f, (recent_time, recent_time))
        
        with patch.object(sys, "argv", ["ci_health_check", "--ci-locs-dir", str(log_dir), "--min-test-count", "100"]):
            exit_code = ci_health_check.main(["--ci-logs-dir", str(log_dir), "--min-test-count", "100"])
        
        assert exit_code == 1

    def test_main_verbose(self, tmp_path):
        """Test main function with verbose flag."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        
        # Create all required metrics to meet thresholds
        (log_dir / "lint.json").write_text(json.dumps({"pass_rate": 0.98}))
        (log_dir / "test.json").write_text(json.dumps({"test_count": 150}))
        (log_dir / "coverage.json").write_text(json.dumps({"coverage": 0.85}))
        
        import time
        recent_time = time.time()
        for f in log_dir.glob("*.json"):
            os.utime(f, (recent_time, recent_time))
        
        with patch.object(sys, "argv", ["ci_health_check", "--ci-logs-dir", str(log_dir), "-v"]):
            exit_code = ci_health_check.main()
        
        assert exit_code == 0

    def test_main_output_file(self, tmp_path):
        """Test main function writes output file."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        output_file = tmp_path / "report.json"
        
        # Create all required metrics to meet thresholds
        (log_dir / "lint.json").write_text(json.dumps({"pass_rate": 0.98}))
        (log_dir / "test.json").write_text(json.dumps({"test_count": 150}))
        (log_dir / "coverage.json").write_text(json.dumps({"coverage": 0.85}))
        
        import time
        recent_time = time.time()
        for f in log_dir.glob("*.json"):
            os.utime(f, (recent_time, recent_time))
        
        with patch.object(sys, "argv", ["ci_health_check"]):
            exit_code = ci_health_check.main([
                "--ci-logs-dir", str(log_dir),
                "--output", str(output_file),
            ])
        
        assert exit_code == 0
        assert output_file.exists()
        
        # Verify output content
        report = json.loads(output_file.read_text())
        assert "timestamp" in report
        assert "metrics" in report
        assert "passed" in report


# Add os import for the os.utime calls
import os
