#!/usr/bin/env python3
"""Tests for bin/edge_test_zero_records.py — Boundary test for empty records list.

Verifies that the adapter fails-closed (raises error) when given an
empty records list, rather than silently passing or crashing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Path to the edge test script (in parent bin/ directory)
EDGE_TEST_SCRIPT = Path(__file__).parent.parent.parent / "bin" / "edge_test_zero_records.py"


class TestEdgeTestZeroRecords:
    """Test suite for edge_test_zero_records.py."""

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
        assert "Test 1" in result.stdout or "Test 2" in result.stdout

    def test_adapter_rejects_empty_records(self):
        """Verify AdapterError is raised for empty records list."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_zero_records import AdapterError, RecordAdapter

        adapter = RecordAdapter()
        # Create temp file with empty records
        fd, path = tempfile.mkstemp(suffix=".json", prefix="action_camera_")
        try:
            data = {"source": "action_camera", "timestamp": "2024-01-01T00:00:00Z", "records": []}
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)

            loaded = adapter.load(path)
            # Should raise AdapterError for empty records
            try:
                adapter.validate(loaded)
                assert False, "Expected AdapterError for empty records"
            except AdapterError as e:
                assert "Empty records list" in str(e)
        finally:
            os.unlink(path)

    def test_adapter_accepts_non_empty_records(self):
        """Verify non-empty records pass validation."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_zero_records import RecordAdapter

        adapter = RecordAdapter()
        fd, path = tempfile.mkstemp(suffix=".json", prefix="action_camera_")
        try:
            data = {
                "source": "action_camera",
                "timestamp": "2024-01-01T00:00:00Z",
                "records": [{"id": 1, "value": "test"}],
            }
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)

            loaded = adapter.load(path)
            result = adapter.validate(loaded)
            assert result is True

            processed = adapter.process(loaded)
            assert len(processed) == 1
            assert processed[0]["id"] == 1
        finally:
            os.unlink(path)

    def test_adapter_rejects_missing_records_field(self):
        """Verify AdapterError is raised when records field is missing."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_zero_records import AdapterError, RecordAdapter

        adapter = RecordAdapter()
        fd, path = tempfile.mkstemp(suffix=".json", prefix="action_camera_")
        try:
            data = {"source": "action_camera", "timestamp": "2024-01-01T00:00:00Z"}
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)

            loaded = adapter.load(path)
            try:
                adapter.validate(loaded)
                assert False, "Expected AdapterError for missing records field"
            except AdapterError as e:
                assert "records" in str(e).lower()
        finally:
            os.unlink(path)

    def test_adapter_rejects_non_list_records(self):
        """Verify AdapterError is raised when records is not a list."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_zero_records import AdapterError, RecordAdapter

        adapter = RecordAdapter()
        fd, path = tempfile.mkstemp(suffix=".json", prefix="action_camera_")
        try:
            data = {"source": "action_camera", "timestamp": "2024-01-01T00:00:00Z", "records": "not-a-list"}
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)

            loaded = adapter.load(path)
            try:
                adapter.validate(loaded)
                assert False, "Expected AdapterError for non-list records"
            except AdapterError as e:
                assert "list" in str(e).lower()
        finally:
            os.unlink(path)

    def test_adapter_rejects_invalid_data_type(self):
        """Verify AdapterError is raised for non-dict data."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_zero_records import AdapterError, RecordAdapter

        adapter = RecordAdapter()
        fd, path = tempfile.mkstemp(suffix=".json", prefix="action_camera_")
        try:
            data = "not a dict"
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)

            loaded = adapter.load(path)
            try:
                adapter.validate(loaded)
                assert False, "Expected AdapterError for non-dict data"
            except AdapterError as e:
                assert "dict" in str(e).lower()
        finally:
            os.unlink(path)
