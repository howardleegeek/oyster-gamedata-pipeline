#!/usr/bin/env python3
"""Tests for bin/edge_test_negative_timestamps.py — Boundary test for negative timestamps.

Verifies the timestamp schema validation correctly rejects:
- Negative epoch timestamps (pre-1970-01-01)
- Dates before 2020 (per schema)
- Far-future dates (beyond current_year + 10)
- Invalid string formats

And accepts valid dates from 2020 onward.
"""

from __future__ import annotations

import datetime
import subprocess
import sys
from pathlib import Path

# Path to the edge test script (in parent bin/ directory)
EDGE_TEST_SCRIPT = (
    Path(__file__).parent.parent.parent / "bin" / "edge_test_negative_timestamps.py"
)


class TestEdgeTestNegativeTimestamps:
    """Test suite for edge_test_negative_timestamps.py."""

    def test_script_exists(self):
        """Verify the edge test script exists and is executable."""
        assert EDGE_TEST_SCRIPT.exists(), f"Script not found: {EDGE_TEST_SCRIPT}"

    def test_runs_successfully(self):
        """Verify the edge test runs and exits with success (0)."""
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"

    def test_verbose_mode(self):
        """Verify verbose mode works without errors."""
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT), "--verbose"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Verbose mode failed: {result.stderr}"
        # Verify expected output markers are present
        assert "Test Results Summary" in result.stdout

    def test_validate_only_mode(self):
        """Verify --validate-only flag runs the schema existence check."""
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT), "--validate-only"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"validate-only failed: {result.stderr}"
        assert "Schema validation function check" in result.stdout
        assert "Function exists: True" in result.stdout

    def test_validate_specific_timestamp_arg(self):
        """Verify --timestamp flag validates a specific ISO string."""
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT), "--timestamp", "2024-06-15T12:00:00Z"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"timestamp arg failed: {result.stderr}"
        assert "Valid: True" in result.stdout

    def test_validate_specific_pre_2020_timestamp_arg(self):
        """Verify --timestamp flag rejects a pre-2020 ISO string (exit 1)."""
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT), "--timestamp", "2019-12-31T23:59:59Z"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1, f"Expected exit 1 for pre-2020, got {result.returncode}"
        assert "Valid: False" in result.stdout

    def test_validate_specific_numeric_timestamp_arg(self):
        """Verify --timestamp flag validates a numeric epoch (post-2020)."""
        # 2024-01-01 00:00:00 UTC = 1704067200
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT), "--timestamp", "1704067200"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"numeric timestamp failed: {result.stderr}"
        assert "Valid: True" in result.stdout
        assert "Year: 2024" in result.stdout

    def test_json_output_mode(self):
        """Verify --json flag outputs parseable JSON summary."""
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT), "--json"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"json mode failed: {result.stderr}"
        import json

        text = result.stdout
        start = text.find("{")
        assert start != -1, f"No JSON object in output: {text!r}"
        payload = json.loads(text[start:])
        # The script wraps the summary under a "summary" key.
        summary = payload.get("summary", payload)
        assert "total_tests" in summary
        assert "passed" in summary
        assert "failed" in summary
        assert summary["failed"] == 0
        assert summary["total_tests"] == summary["passed"] + summary["failed"]

    # --- validate_timestamp_schema unit tests ---

    def test_validate_rejects_negative_epoch(self):
        """Negative epoch (pre-1970) must be rejected."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_negative_timestamps import validate_timestamp_schema

        assert validate_timestamp_schema(-1) is False
        assert validate_timestamp_schema(-31536000) is False  # 1969-01-01
        assert validate_timestamp_schema(-2208988800) is False  # 1900-01-01

    def test_validate_rejects_epoch_zero(self):
        """Epoch 0 (1970-01-01) is pre-2020 and must be rejected."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_negative_timestamps import validate_timestamp_schema

        assert validate_timestamp_schema(0) is False

    def test_validate_rejects_pre_2020_int_epoch(self):
        """Integer epochs before 2020-01-01 must be rejected."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_negative_timestamps import validate_timestamp_schema

        assert validate_timestamp_schema(1546300800) is False  # 2019-01-01
        # Boundary: 1 second before 2020-01-01 UTC
        assert validate_timestamp_schema(1577836799) is False

    def test_validate_accepts_2020_boundary(self):
        """2020-01-01 00:00:00 UTC (1577836800) is the lower boundary — accept."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_negative_timestamps import validate_timestamp_schema

        assert validate_timestamp_schema(1577836800) is True

    def test_validate_accepts_recent_dates(self):
        """Recent post-2020 integer epochs must be accepted."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_negative_timestamps import validate_timestamp_schema

        assert validate_timestamp_schema(1609459200) is True  # 2021-01-01
        assert validate_timestamp_schema(1704067200) is True  # 2024-01-01

    def test_validate_rejects_far_future(self):
        """Dates > current_year + 10 must be rejected."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_negative_timestamps import validate_timestamp_schema

        current_year = datetime.datetime.now(datetime.timezone.utc).year
        # Build a year well beyond current_year + 10
        far_year = current_year + 50
        try:
            far_dt = datetime.datetime(far_year, 1, 1, tzinfo=datetime.timezone.utc)
        except (ValueError, OverflowError):
            # Some platforms reject very large years
            far_dt = None
        if far_dt is not None:
            far_epoch = far_dt.timestamp()
            assert validate_timestamp_schema(far_epoch) is False

    def test_validate_accepts_iso_strings(self):
        """Valid ISO 8601 strings from 2020 onward must be accepted."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_negative_timestamps import validate_timestamp_schema

        assert validate_timestamp_schema("2020-06-15T14:30:00Z") is True
        assert validate_timestamp_schema("2024-01-01T00:00:00") is True
        assert validate_timestamp_schema("2023-12-31 23:59:59") is True

    def test_validate_rejects_pre_2020_iso_strings(self):
        """ISO 8601 strings from before 2020 must be rejected."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_negative_timestamps import validate_timestamp_schema

        assert validate_timestamp_schema("2019-12-31T23:59:59Z") is False
        assert validate_timestamp_schema("2018-01-01") is False
        assert validate_timestamp_schema("2010-06-15 12:00:00") is False

    def test_validate_rejects_malformed_strings(self):
        """Unparseable strings must be rejected (not raise)."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_negative_timestamps import validate_timestamp_schema

        assert validate_timestamp_schema("not-a-date") is False
        assert validate_timestamp_schema("") is False
        assert validate_timestamp_schema("2024-13-99") is False  # invalid month/day

    def test_validate_accepts_datetime_objects(self):
        """datetime.datetime objects with valid years must be accepted."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_negative_timestamps import validate_timestamp_schema

        dt_valid = datetime.datetime(2024, 6, 15, 12, 0, 0, tzinfo=datetime.timezone.utc)
        assert validate_timestamp_schema(dt_valid) is True

        dt_invalid = datetime.datetime(2019, 1, 1, tzinfo=datetime.timezone.utc)
        assert validate_timestamp_schema(dt_invalid) is False

    def test_validate_rejects_unsupported_types(self):
        """Non-int/float/str/datetime inputs must be rejected (not raise)."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_negative_timestamps import validate_timestamp_schema

        assert validate_timestamp_schema(None) is False
        assert validate_timestamp_schema([2024, 1, 1]) is False
        assert validate_timestamp_schema({"year": 2024}) is False
        assert validate_timestamp_schema(object()) is False

    def test_validate_does_not_raise_on_overflow(self):
        """Extremely large/small floats must be caught (return False), not raise."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_negative_timestamps import validate_timestamp_schema

        # Far beyond datetime range on most platforms
        assert validate_timestamp_schema(1e300) is False
        assert validate_timestamp_schema(-1e300) is False

    # --- test suite helpers ---

    def test_negative_epoch_suite_shape(self):
        """test_negative_epoch_timestamps returns a list of dicts with required keys."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_negative_timestamps import test_negative_epoch_timestamps

        cases = test_negative_epoch_timestamps()
        assert isinstance(cases, list)
        assert len(cases) >= 5
        for c in cases:
            assert {"timestamp", "description", "is_valid", "expected", "passed"} <= set(c)
            assert c["expected"] is False
            # All pre-2020 negatives must be rejected
            assert c["is_valid"] is False
            assert c["passed"] is True

    def test_pre_2020_suite_shape(self):
        """test_pre_2020_dates returns a list of dicts; pre-2020 rejected, 2020+ accepted."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_negative_timestamps import test_pre_2020_dates

        cases = test_pre_2020_dates()
        assert isinstance(cases, list)
        assert len(cases) >= 5
        saw_boundary_accept = False
        saw_pre_2020_reject = False
        for c in cases:
            assert {"timestamp", "description", "is_valid", "expected", "passed"} <= set(c)
            # is_valid and expected must agree (this is the suite's invariant).
            assert c["is_valid"] == c["expected"]
            # The "passed" flag must be True because the suite is self-consistent.
            assert c["passed"] is True
            if c["expected"] is True:
                saw_boundary_accept = True
            else:
                saw_pre_2020_reject = True
        # The suite must exercise both the boundary-accept and pre-2020-reject paths.
        assert saw_boundary_accept, "Suite missing 2020-boundary accept case"
        assert saw_pre_2020_reject, "Suite missing pre-2020 reject case"

    def test_valid_post_2020_suite_shape(self):
        """test_valid_post_2020_dates returns a list of dicts; all post-2020 must be accepted."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_negative_timestamps import test_valid_post_2020_dates

        cases = test_valid_post_2020_dates()
        assert isinstance(cases, list)
        assert len(cases) >= 5
        for c in cases:
            assert {"timestamp", "description", "is_valid", "expected", "passed"} <= set(c)
            assert c["expected"] is True
            assert c["is_valid"] is True
            assert c["passed"] is True

    def test_run_all_tests_summary_consistent(self):
        """run_all_tests returns a dict with consistent totals (passed + failed == total)."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_negative_timestamps import run_all_tests

        summary = run_all_tests(verbose=False)
        assert {"total_tests", "passed", "failed", "all_tests"} <= set(summary)
        assert summary["total_tests"] == summary["passed"] + summary["failed"]
        # The script's own self-tests should all pass (its data was designed to).
        assert summary["failed"] == 0
        assert summary["passed"] == summary["total_tests"]
        assert summary["total_tests"] > 0
