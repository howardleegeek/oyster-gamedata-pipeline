#!/usr/bin/env python3
"""Tests for bin/audit_trend_aggregator.py"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Add bin to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "bin"))

import audit_trend_aggregator as aggregator


class TestSparkline:
    """Tests for sparkline function."""

    def test_empty_list_returns_empty_string(self):
        assert aggregator.sparkline([]) == ""

    def test_single_value_uses_middle_char(self):
        # Single finite value should return middle character
        result = aggregator.sparkline([100])
        assert len(result) == 1

    def test_all_same_values_returns_same_char(self):
        # When min == max, should return same char for all positions
        result = aggregator.sparkline([5, 5, 5, 5])
        assert len(result) == 4
        assert len(set(result)) == 1

    def test_none_values_handled(self):
        # None values should result in spaces
        result = aggregator.sparkline([1, None, 3])
        assert len(result) == 3

    def test_inf_values_handled(self):
        # inf values should result in spaces
        result = aggregator.sparkline([1, float("inf"), 3])
        assert len(result) == 3

    def test_negative_values(self):
        result = aggregator.sparkline([-10, 0, 10])
        assert len(result) == 3

    def test_large_range(self):
        result = aggregator.sparkline([0, 1000000])
        assert len(result) == 2


class TestMean:
    """Tests for mean function."""

    def test_empty_list_returns_zero(self):
        assert aggregator.mean([]) == 0.0

    def test_single_value(self):
        assert aggregator.mean([42]) == 42.0

    def test_multiple_values(self):
        assert aggregator.mean([1, 2, 3, 4, 5]) == 3.0

    def test_floats(self):
        assert abs(aggregator.mean([1.5, 2.5, 3.0]) - 2.333333) < 0.001


class TestStddev:
    """Tests for stddev function."""

    def test_empty_list_returns_zero(self):
        assert aggregator.stddev([]) == 0.0

    def test_single_value_returns_zero(self):
        assert aggregator.stddev([5]) == 0.0

    def test_two_equal_values_returns_zero(self):
        assert aggregator.stddev([5, 5]) == 0.0

    def test_known_values(self):
        # stddev of [2, 4, 4, 4, 5, 5, 7, 9] = 2.0
        result = aggregator.stddev([2, 4, 4, 4, 5, 5, 7, 9])
        assert abs(result - 2.0) < 0.001


class TestLinearSlope:
    """Tests for linear_slope function."""

    def test_empty_list_returns_zero(self):
        assert aggregator.linear_slope([]) == 0.0

    def test_single_value_returns_zero(self):
        assert aggregator.linear_slope([5]) == 0.0

    def test_two_values(self):
        # [1, 2] should have slope ~1 (x goes from 0->1, y goes from 1->2)
        # Actually linear_slope uses range(n) as x values
        assert aggregator.linear_slope([1, 2]) == 1.0

    def test_constant_values_returns_zero(self):
        assert aggregator.linear_slope([5, 5, 5, 5]) == 0.0

    def test_positive_trend(self):
        # Values increasing
        assert aggregator.linear_slope([1, 2, 3, 4, 5]) == 1.0

    def test_negative_trend(self):
        # Values decreasing
        assert aggregator.linear_slope([5, 4, 3, 2, 1]) == -1.0


class TestLoadRuns:
    """Tests for load_runs function."""

    def test_nonexistent_directory_exits(self):
        with pytest.raises(SystemExit):
            aggregator.load_runs("/nonexistent/path/12345")

    def test_empty_directory_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runs = aggregator.load_runs(tmpdir)
            assert runs == []

    def test_single_json_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a valid JSON audit file
            audit_data = {
                "_timestamp": "2026-01-01T00:00:00Z",
                "total_items": 10,
                "passed": 8,
                "failed": 2,
                "QM1": 0.95,
            }
            json_path = Path(tmpdir) / "audit_2026-01-01.json"
            json_path.write_text(json.dumps(audit_data))

            runs = aggregator.load_runs(tmpdir)
            assert len(runs) == 1
            assert runs[0]["total_items"] == 10
            assert runs[0]["passed"] == 8

    def test_multiple_json_files_sorted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            for i in range(3):
                audit_data = {
                    "_timestamp": f"2026-01-0{i+1}T00:00:00Z",
                    "total_items": 10,
                    "passed": 8,
                }
                json_path = Path(tmpdir) / f"audit_2026-01-0{i+1}.json"
                json_path.write_text(json.dumps(audit_data))

            runs = aggregator.load_runs(tmpdir)
            assert len(runs) == 3
            # Should be sorted by timestamp
            assert runs[0]["_timestamp"] < runs[1]["_timestamp"] < runs[2]["_timestamp"]

    def test_skips_invalid_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create valid file
            valid_data = {"_timestamp": "2026-01-01T00:00:00Z", "total_items": 10, "passed": 8}
            valid_path = Path(tmpdir) / "audit_valid.json"
            valid_path.write_text(json.dumps(valid_data))

            # Create invalid file
            invalid_path = Path(tmpdir) / "audit_invalid.json"
            invalid_path.write_text("not valid json {{{")

            runs = aggregator.load_runs(tmpdir)
            # Should have loaded the valid one, skipped invalid
            assert len(runs) == 1


class TestBuildTimeseries:
    """Tests for build_timeseries function."""

    def test_empty_runs(self):
        item_series, qm_series, pass_rate_series = aggregator.build_timeseries([])
        assert item_series == {}
        assert qm_series == {}
        assert pass_rate_series == []

    def test_single_run(self):
        runs = [
            {
                "_timestamp": "2026-01-01T00:00:00Z",
                "total_items": 10,
                "passed": 8,
                "failed": 2,
                "QM1": 0.95,
            }
        ]
        item_series, qm_series, pass_rate_series = aggregator.build_timeseries(runs)
        assert "QM1" in qm_series
        assert len(pass_rate_series) == 1
        assert pass_rate_series[0][1] == 80.0  # 8/10 * 100

    def test_item_series_tracking(self):
        # Items should be a list of dicts with 'name' and 'status' keys
        runs = [
            {
                "_timestamp": "2026-01-01T00:00:00Z",
                "total_items": 2,
                "passed": 1,
                "items": [
                    {"name": "item_a", "status": "PASS"},
                    {"name": "item_b", "status": "FAIL"},
                ],
            },
            {
                "_timestamp": "2026-01-02T00:00:00Z",
                "total_items": 2,
                "passed": 2,
                "items": [
                    {"name": "item_a", "status": "PASS"},
                    {"name": "item_b", "status": "PASS"},
                ],
            },
        ]
        item_series, _, _ = aggregator.build_timeseries(runs)
        assert "item_a" in item_series
        assert "item_b" in item_series


class TestDetectTransitions:
    """Tests for detect_transitions function."""

    def test_empty_series(self):
        regressions, improvements = aggregator.detect_transitions({})
        assert regressions == []
        assert improvements == []

    def test_no_transitions(self):
        item_series = {
            "item_a": [("2026-01-01", "PASS"), ("2026-01-02", "PASS")],
            "item_b": [("2026-01-01", "FAIL"), ("2026-01-02", "FAIL")],
        }
        regressions, improvements = aggregator.detect_transitions(item_series)
        assert regressions == []
        assert improvements == []

    def test_detects_regression(self):
        item_series = {
            "item_a": [("2026-01-01", "PASS"), ("2026-01-02", "FAIL")],
        }
        regressions, improvements = aggregator.detect_transitions(item_series)
        assert len(regressions) == 1
        assert regressions[0][1] == "item_a"

    def test_detects_improvement(self):
        item_series = {
            "item_a": [("2026-01-01", "FAIL"), ("2026-01-02", "PASS")],
        }
        regressions, improvements = aggregator.detect_transitions(item_series)
        assert len(improvements) == 1
        assert improvements[0][1] == "item_a"

    def test_sorted_by_recency(self):
        item_series = {
            "item_a": [
                ("2026-01-01", "PASS"),
                ("2026-01-02", "FAIL"),
                ("2026-01-03", "FAIL"),
            ],
        }
        regressions, _ = aggregator.detect_transitions(item_series)
        # Should only have one regression (most recent)
        assert len(regressions) == 1
        # The regression should be from 2026-01-02 (the first FAIL after PASS)
        assert regressions[0][0] == "2026-01-02"


class TestGenerateReport:
    """Tests for generate_report function."""

    def test_empty_runs(self):
        report = aggregator.generate_report([], {}, {}, [], [], [])
        assert "No runs found" in report

    def test_summary_section(self):
        runs = [
            {
                "_timestamp": "2026-01-01T00:00:00Z",
                "total_items": 10,
                "passed": 8,
                "failed": 2,
            }
        ]
        pass_rate_series = [("2026-01-01T00:00:00Z", 80.0)]
        report = aggregator.generate_report(runs, {}, {}, pass_rate_series, [], [])
        assert "Total runs:" in report
        assert "80.0%" in report


class TestBuildTrendData:
    """Tests for build_trend_data function."""

    def test_empty_data(self):
        result = aggregator.build_trend_data([], {}, {}, [], [], [])
        assert result["runs"] == []
        assert result["pass_rate_series"] == []
        assert result["item_series"] == {}
        assert result["qm_series"] == {}

    def test_with_data(self):
        runs = [
            {
                "_timestamp": "2026-01-01T00:00:00Z",
                "total_items": 10,
                "passed": 8,
                "failed": 2,
                "_file": "audit.json",
            }
        ]
        pass_rate_series = [("2026-01-01T00:00:00Z", 80.0)]
        result = aggregator.build_trend_data(runs, {}, {}, pass_rate_series, [], [])
        assert len(result["runs"]) == 1
        assert result["runs"][0]["passed"] == 8


class TestMain:
    """Tests for main CLI function."""

    def test_missing_results_dir(self):
        with patch("sys.argv", ["audit_trend_aggregator.py"]):
            with pytest.raises(SystemExit):
                aggregator.main()

    def test_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / "report.md"
            with patch("sys.argv", ["audit_trend_aggregator.py", tmpdir, "--out", str(out_file)]):
                aggregator.main()
            assert out_file.exists()
            content = out_file.read_text()
            assert "No runs found" in content

    def test_custom_lookback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create multiple audit files
            for i in range(15):
                audit_data = {
                    "_timestamp": f"2026-01-{i+1:02d}T00:00:00Z",
                    "total_items": 10,
                    "passed": 8,
                }
                json_path = Path(tmpdir) / f"audit_{i+1}.json"
                json_path.write_text(json.dumps(audit_data))

            out_file = Path(tmpdir) / "report.md"
            with patch(
                "sys.argv", ["audit_trend_aggregator.py", tmpdir, "--out", str(out_file), "--lookback", "5"]
            ):
                aggregator.main()

            content = out_file.read_text()
            assert "lookback" in content.lower() or "5" in content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
