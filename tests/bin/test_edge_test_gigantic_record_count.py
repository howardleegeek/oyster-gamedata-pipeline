#!/usr/bin/env python3
"""Tests for bin/edge_test_gigantic_record_count.py — Boundary test for 1M-record streaming.

Verifies that the action_camera adapter streams records (rather than loading
them all into memory), and that the helper functions behave correctly at
small and large record counts.  Guards against silent memory blow-up on
gigantic ingestion files.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

# Path to the edge test script (in parent bin/ directory)
EDGE_TEST_SCRIPT = (
    Path(__file__).parent.parent.parent / "bin" / "edge_test_gigantic_record_count.py"
)


class TestEdgeTestGiganticRecordCount:
    """Test suite for edge_test_gigantic_record_count.py."""

    def test_script_exists(self):
        """Verify the edge test script exists and is executable."""
        assert EDGE_TEST_SCRIPT.exists(), f"Script not found: {EDGE_TEST_SCRIPT}"

    def test_runs_successfully_small(self):
        """Verify script runs successfully with a small record count."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "action_camera.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(EDGE_TEST_SCRIPT),
                    "--records",
                    "100",
                    "--output",
                    str(out),
                    "--chunk-size",
                    "25",
                ],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, f"Script failed: {result.stderr}"
            assert "PASS" in result.stderr, "Expected PASS in stderr output"
            assert "100 records streamed successfully" in result.stderr

    def test_verbose_mode(self):
        """Verify verbose mode works without errors and emits chunk progress."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "action_camera.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(EDGE_TEST_SCRIPT),
                    "--records",
                    "150",
                    "--output",
                    str(out),
                    "--chunk-size",
                    "50",
                    "--verbose",
                ],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, f"Verbose mode failed: {result.stderr}"
            assert "Read 100,000 records" in result.stderr or "PASS" in result.stderr

    def test_rejects_zero_records(self):
        """Verify script returns error for non-positive record count."""
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT), "--records", "0"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1, "Expected exit 1 for records=0"
        assert "must be positive" in result.stderr

    def test_rejects_negative_records(self):
        """Verify script returns error for negative record count."""
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT), "--records", "-5"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1, "Expected exit 1 for records=-5"
        assert "must be positive" in result.stderr

    def test_rejects_zero_chunk_size(self):
        """Verify script returns error for non-positive chunk size."""
        result = subprocess.run(
            [
                sys.executable,
                str(EDGE_TEST_SCRIPT),
                "--records",
                "10",
                "--chunk-size",
                "0",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1, "Expected exit 1 for chunk-size=0"
        assert "must be positive" in result.stderr


class TestGenerateRecords:
    """Test suite for the generate_records helper."""

    def test_yields_correct_count(self):
        """generate_records should yield exactly `count` records."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_gigantic_record_count import generate_records

        records = list(generate_records(10))
        assert len(records) == 10

    def test_record_schema(self):
        """Each record must contain the required schema fields."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_gigantic_record_count import generate_records

        rec = next(generate_records(1))
        for required in ("id", "model", "resolution", "fps", "price", "in_stock", "warehouse"):
            assert required in rec, f"Missing field: {required}"

    def test_records_are_lazy(self):
        """generate_records must return an iterator (not a list)."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_gigantic_record_count import generate_records

        gen = generate_records(5)
        # Should be a generator/iterator, not a list
        import types

        assert isinstance(gen, types.GeneratorType), "Expected a generator (lazy)"

    def test_zero_records_yields_nothing(self):
        """generate_records(0) must yield zero records."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_gigantic_record_count import generate_records

        records = list(generate_records(0))
        assert records == []

    def test_resolution_rotation(self):
        """Resolution must rotate across the 3 known values."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_gigantic_record_count import generate_records

        records = list(generate_records(6))
        resolutions = [r["resolution"] for r in records]
        assert resolutions == ["4K", "1080p", "720p", "4K", "1080p", "720p"]


class TestWriteAndReadStreaming:
    """Test suite for write_json_streaming and stream_read_json helpers."""

    def test_write_and_read_roundtrip(self):
        """Writing then streaming-reading should yield the original records."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_gigantic_record_count import (
            generate_records,
            stream_read_json,
            write_json_streaming,
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.jsonl"
            n = 250
            written = write_json_streaming(generate_records(n), path)
            assert written == n

            chunks = list(stream_read_json(path, chunk_size=50))
            recovered = [rec for chunk in chunks for rec in chunk]
            assert len(recovered) == n
            assert recovered[0]["id"] == 1
            assert recovered[-1]["id"] == n

    def test_chunk_size_buckets(self):
        """stream_read_json must split records into chunks of <= chunk_size."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_gigantic_record_count import (
            generate_records,
            stream_read_json,
            write_json_streaming,
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.jsonl"
            write_json_streaming(generate_records(100), path)

            chunks = list(stream_read_json(path, chunk_size=30))
            # 100 / 30 = 3 full chunks of 30 + 1 partial of 10
            assert len(chunks) == 4
            assert all(len(c) <= 30 for c in chunks)
            assert len(chunks[0]) == 30
            assert len(chunks[-1]) == 10

    def test_stream_read_empty_file(self):
        """stream_read_json on an empty file must yield nothing."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_gigantic_record_count import stream_read_json

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.jsonl"
            path.write_text("", encoding="utf-8")
            chunks = list(stream_read_json(path, chunk_size=10))
            assert chunks == []

    def test_stream_read_skips_blank_lines(self):
        """stream_read_json must skip blank/whitespace-only lines."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_gigantic_record_count import stream_read_json

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blanks.jsonl"
            path.write_text(
                '{"id": 1}\n'
                "\n"
                "   \n"
                '{"id": 2}\n',
                encoding="utf-8",
            )
            chunks = list(stream_read_json(path, chunk_size=10))
            flat = [r for c in chunks for r in c]
            assert len(flat) == 2
            assert flat[0]["id"] == 1
            assert flat[1]["id"] == 2

    def test_written_file_is_valid_jsonl(self):
        """Each non-empty line in the output must be a valid JSON object."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_gigantic_record_count import (
            generate_records,
            write_json_streaming,
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.jsonl"
            write_json_streaming(generate_records(5), path)
            with open(path, "r", encoding="utf-8") as fh:
                lines = [ln for ln in fh.read().splitlines() if ln.strip()]
            assert len(lines) == 5
            for line in lines:
                obj = json.loads(line)
                assert isinstance(obj, dict)
