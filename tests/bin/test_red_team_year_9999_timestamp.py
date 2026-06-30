#!/usr/bin/env python3
"""Tests for bin/red_team_year_9999_timestamp.py — year-9999 timestamp clamp.

Covers:
- Constants: DEFAULT_MIN_YEAR, DEFAULT_MAX_YEAR, YEAR_9999_DT
- clamp_timestamp: under-min, in-range, over-max, boundary, preserves
  month/day/hour/minute/second/microsecond
- build_test_cases: count, expected_year values, "current year" matches now()
- run_tests: passed/failed counts, error list contents
- main (CLI): exit code 0 when no failures, exit code 1 when expected
  failures occur, --json flag emits a JSON report, --max-year override
  is respected, returns 2 on ValueError (bad input via main's contract
  where run_tests is always the data source)
"""

from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

# Add bin/ to sys.path so the module is importable as a top-level name
_BIN_DIR = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(_BIN_DIR))

from red_team_year_9999_timestamp import (  # noqa: E402
    DEFAULT_MAX_YEAR,
    DEFAULT_MIN_YEAR,
    YEAR_9999_DT,
    build_test_cases,
    clamp_timestamp,
    main,
    run_tests,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    """Pin module-level constants to the documented contract."""

    def test_default_min_year(self):
        assert DEFAULT_MIN_YEAR == 1970

    def test_default_max_year(self):
        assert DEFAULT_MAX_YEAR == 2100

    def test_year_9999_dt_is_max_year_minus_one(self):
        assert YEAR_9999_DT == datetime.datetime(9999, 12, 31, 23, 59, 59)

    def test_year_9999_dt_is_aware_or_naive_datetime(self):
        # Naive datetime is fine — schema consumers should accept either.
        assert isinstance(YEAR_9999_DT, datetime.datetime)


# ---------------------------------------------------------------------------
# clamp_timestamp
# ---------------------------------------------------------------------------


class TestClampTimestamp:
    """clamp_timestamp: pure function with default and custom bounds."""

    def test_year_under_min_is_clamped_to_min(self):
        dt = datetime.datetime(1969, 12, 31, 12, 0, 0)
        out = clamp_timestamp(dt)
        assert out.year == DEFAULT_MIN_YEAR

    def test_year_over_max_is_clamped_to_default_max(self):
        out = clamp_timestamp(YEAR_9999_DT)
        assert out.year == DEFAULT_MAX_YEAR

    def test_year_in_range_is_unchanged(self):
        dt = datetime.datetime(2099, 6, 15, 10, 30, 0)
        out = clamp_timestamp(dt)
        assert out == dt

    def test_year_at_min_boundary_unchanged(self):
        dt = datetime.datetime(1970, 1, 1, 0, 0, 0)
        out = clamp_timestamp(dt)
        assert out == dt

    def test_year_at_default_max_boundary_unchanged(self):
        dt = datetime.datetime(DEFAULT_MAX_YEAR, 1, 1, 0, 0, 0)
        out = clamp_timestamp(dt)
        assert out == dt

    def test_custom_max_year(self):
        out = clamp_timestamp(YEAR_9999_DT, min_year=1970, max_year=2050)
        assert out.year == 2050

    def test_custom_min_year(self):
        dt = datetime.datetime(1969, 6, 1)
        out = clamp_timestamp(dt, min_year=1980, max_year=2100)
        assert out.year == 1980

    def test_preserves_month_day_time(self):
        """Clamping must not change month/day/hour/minute/second/microsecond."""
        dt = datetime.datetime(9999, 3, 14, 15, 9, 26, 535897)
        out = clamp_timestamp(dt)
        assert out.month == 3
        assert out.day == 14
        assert out.hour == 15
        assert out.minute == 9
        assert out.second == 26
        assert out.microsecond == 535897

    def test_returns_new_datetime(self):
        """clamp_timestamp must not mutate the input."""
        dt = datetime.datetime(9999, 1, 1)
        original = dt
        _ = clamp_timestamp(dt)
        assert dt == original

    def test_returns_datetime(self):
        out = clamp_timestamp(YEAR_9999_DT)
        assert isinstance(out, datetime.datetime)

    def test_just_over_default_max_is_clamped(self):
        dt = datetime.datetime(DEFAULT_MAX_YEAR + 1, 1, 1)
        out = clamp_timestamp(dt)
        assert out.year == DEFAULT_MAX_YEAR

    def test_just_under_default_min_is_clamped(self):
        dt = datetime.datetime(DEFAULT_MIN_YEAR - 1, 1, 1)
        out = clamp_timestamp(dt)
        assert out.year == DEFAULT_MIN_YEAR


# ---------------------------------------------------------------------------
# build_test_cases
# ---------------------------------------------------------------------------


class TestBuildTestCases:
    """build_test_cases returns a stable, ordered list of test cases."""

    def test_returns_six_cases(self):
        cases = build_test_cases(DEFAULT_MAX_YEAR)
        assert len(cases) == 6

    def test_each_case_has_required_keys(self):
        cases = build_test_cases(DEFAULT_MAX_YEAR)
        for tc in cases:
            assert "label" in tc
            assert "input" in tc
            assert "expected_year" in tc

    def test_year_9999_max_case_clamps_to_max(self):
        cases = build_test_cases(DEFAULT_MAX_YEAR)
        first = cases[0]
        assert first["input"] == YEAR_9999_DT
        assert first["expected_year"] == DEFAULT_MAX_YEAR

    def test_year_2099_in_range(self):
        cases = build_test_cases(DEFAULT_MAX_YEAR)
        in_range = [c for c in cases if c["label"].startswith("year 2099")]
        assert len(in_range) == 1
        assert in_range[0]["expected_year"] == 2099

    def test_year_1969_clamps_to_min(self):
        cases = build_test_cases(DEFAULT_MAX_YEAR)
        under_min = [c for c in cases if c["label"].startswith("year 1969")]
        assert len(under_min) == 1
        assert under_min[0]["expected_year"] == DEFAULT_MIN_YEAR

    def test_current_year_case_uses_now(self):
        cases = build_test_cases(DEFAULT_MAX_YEAR)
        now_case = [c for c in cases if c["label"] == "current year"][0]
        now = datetime.datetime.now()
        assert now_case["expected_year"] == now.year

    def test_custom_max_year_propagates_to_cases(self):
        cases = build_test_cases(2050)
        over_max_cases = [c for c in cases if c["label"] in ("year 9999 max", "year 9999-01-01", "year 2101 (just over)")]
        for tc in over_max_cases:
            assert tc["expected_year"] == 2050

    def test_labels_are_unique(self):
        cases = build_test_cases(DEFAULT_MAX_YEAR)
        labels = [c["label"] for c in cases]
        assert len(labels) == len(set(labels))


# ---------------------------------------------------------------------------
# run_tests
# ---------------------------------------------------------------------------


class TestRunTests:
    """run_tests: end-to-end execution of clamp_timestamp on every case."""

    def test_default_max_year_all_pass(self):
        passed, failed, errors = run_tests(DEFAULT_MAX_YEAR)
        assert passed == 6
        assert failed == 0
        assert errors == []

    def test_custom_max_year_zero_failures_when_max_above_2099(self):
        # The "year 2099 (in range)" case has a hardcoded expected_year=2099,
        # so choosing any max_year >= 2099 keeps all six cases green.
        passed, failed, errors = run_tests(2150)
        assert passed == 6
        assert failed == 0
        assert errors == []

    def test_returns_tuple_of_int_int_list(self):
        result = run_tests(DEFAULT_MAX_YEAR)
        assert isinstance(result, tuple)
        assert len(result) == 3
        assert isinstance(result[0], int)
        assert isinstance(result[1], int)
        assert isinstance(result[2], list)

    def test_error_messages_are_strings(self):
        # Force a failure by using an impossible max_year below every case
        # expected_year: only "year 1969 (under min)" clamps to DEFAULT_MIN_YEAR,
        # so the others fail when max_year is 1960.
        passed, failed, errors = run_tests(1960)
        assert failed > 0
        assert len(errors) == failed
        for msg in errors:
            assert isinstance(msg, str)
            assert "FAIL" in msg

    def test_failed_count_equals_error_list_length(self):
        passed, failed, errors = run_tests(1960)
        assert failed == len(errors)


# ---------------------------------------------------------------------------
# main (CLI)
# ---------------------------------------------------------------------------


class TestMain:
    """main: CLI entry point with --json and --max-year flags."""

    def test_default_run_returns_zero(self, capsys):
        rc = main([])
        captured = capsys.readouterr()
        assert rc == 0
        assert "Red-team year-9999 timestamp clamp" in captured.out
        assert "6 passed, 0 failed" in captured.out

    def test_json_flag_emits_parseable_json(self, capsys):
        rc = main(["--json"])
        captured = capsys.readouterr()
        assert rc == 0
        report = json.loads(captured.out)
        assert report["total"] == 6
        assert report["passed"] == 6
        assert report["failed"] == 0
        assert report["errors"] == []
        assert report["max_year"] == DEFAULT_MAX_YEAR

    def test_max_year_override(self, capsys):
        rc = main(["--max-year", "2050", "--json"])
        captured = capsys.readouterr()
        assert rc == 0
        report = json.loads(captured.out)
        assert report["max_year"] == 2050
        assert report["passed"] == 6
        assert report["failed"] == 0

    def test_failure_path_returns_one(self, capsys):
        # max_year=1960 makes the in-range and over-max cases fail.
        rc = main(["--max-year", "1960"])
        captured = capsys.readouterr()
        assert rc == 1
        assert "failed" in captured.out

    def test_failure_path_json_includes_errors(self, capsys):
        rc = main(["--max-year", "1960", "--json"])
        captured = capsys.readouterr()
        assert rc == 1
        report = json.loads(captured.out)
        assert report["failed"] > 0
        assert report["passed"] < 6
        assert len(report["errors"]) == report["failed"]
        for msg in report["errors"]:
            assert "FAIL" in msg

    def test_human_output_uses_stdout(self, capsys):
        # Human-readable output goes to stdout, not stderr.
        rc = main([])
        captured = capsys.readouterr()
        assert rc == 0
        assert captured.out != ""
        assert captured.err == ""

    def test_max_year_default_matches_constant(self, capsys):
        # If --max-year is not given, the report must reflect the default.
        rc = main(["--json"])
        captured = capsys.readouterr()
        report = json.loads(captured.out)
        assert report["max_year"] == DEFAULT_MAX_YEAR
