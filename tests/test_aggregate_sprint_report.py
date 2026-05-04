"""Tests for aggregate_sprint_report.py"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import tempfile

from aggregate_sprint_report import aggregate


def _make_iter_json(tmpdir, iter_num, **overrides):
    data = {
        "iter": iter_num,
        "capture_exit": 0,
        "adapt_exit": 0,
        "lint_exit": 0,
        "capture_seconds": 5,
        "adapt_seconds": 50,
        "lint_seconds": 3,
        "total_seconds": 58,
        "records": 9000,
        "lint_errors": 0,
        "lint_warnings": 0,
    }
    data.update(overrides)
    path = os.path.join(tmpdir, f"iter_{iter_num:04d}.json")
    with open(path, "w") as f:
        json.dump(data, f)
    return path


def test_basic_aggregation():
    with tempfile.TemporaryDirectory() as tmpdir:
        for i in range(1, 101):
            _make_iter_json(tmpdir, i, total_seconds=100 + i)
        out = os.path.join(tmpdir, "report.md")
        result = aggregate(tmpdir, out)
        assert result["iter_count"] == 100
        assert result["pass_count"] == 100
        assert result["fail_count"] == 0
        assert result["total_seconds_min"] == 101
        assert result["total_seconds_max"] == 200
        assert os.path.exists(out)
        with open(out) as f:
            content = f.read()
        assert "Sprint validation report" in content
        assert "100 iterations" in content
        assert "Drift check" in content


def test_failures_counted():
    with tempfile.TemporaryDirectory() as tmpdir:
        for i in range(1, 11):
            lint_exit = 1 if i <= 3 else 0
            _make_iter_json(tmpdir, i, lint_exit=lint_exit)
        out = os.path.join(tmpdir, "report.md")
        result = aggregate(tmpdir, out)
        assert result["pass_count"] == 7
        assert result["fail_count"] == 3


def test_drift_buckets():
    with tempfile.TemporaryDirectory() as tmpdir:
        for i in range(1, 101):
            ts = 50 if i <= 25 else 100
            _make_iter_json(tmpdir, i, total_seconds=ts)
        out = os.path.join(tmpdir, "report.md")
        result = aggregate(tmpdir, out)
        assert result["drift_buckets"]["1-25"] == 50.0
        assert result["drift_buckets"]["26-50"] == 100.0


def test_histogram_in_report():
    with tempfile.TemporaryDirectory() as tmpdir:
        for i in range(1, 6):
            _make_iter_json(tmpdir, i, total_seconds=10)
        out = os.path.join(tmpdir, "report.md")
        aggregate(tmpdir, out)
        with open(out) as f:
            content = f.read()
        assert "10s" in content
        assert "(5)" in content
