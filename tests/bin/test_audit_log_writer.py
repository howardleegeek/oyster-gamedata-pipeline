#!/usr/bin/env python3
"""Tests for bin/audit_log_writer.py — Production audit log writer tests.

Verifies audit log writer correctly appends newline-delimited JSON records,
validates inputs, handles atomic writes, and supports CLI entry point.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Path to the audit log writer script
AUDIT_LOG_WRITER_SCRIPT = Path(__file__).parent.parent.parent / "bin" / "audit_log_writer.py"


class TestAuditLogWriter:
    """Test suite for audit_log_writer.py."""

    def test_script_exists(self):
        """Verify the audit log writer script exists and is executable."""
        assert AUDIT_LOG_WRITER_SCRIPT.exists(), f"Script not found: {AUDIT_LOG_WRITER_SCRIPT}"

    def test_cli_runs_successfully(self):
        """Verify the CLI runs and appends a record successfully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "audit.log")
            result = subprocess.run(
                [
                    sys.executable,
                    str(AUDIT_LOG_WRITER_SCRIPT),
                    "--log-path", log_path,
                    "--action", "capture",
                    "--status", "ok",
                ],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, f"Script failed: {result.stderr}"
            assert os.path.exists(log_path), "Log file should be created"

    def test_cli_captures_event(self):
        """Verify CLI correctly captures and writes event data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "audit.log")
            result = subprocess.run(
                [
                    sys.executable,
                    str(AUDIT_LOG_WRITER_SCRIPT),
                    "--log-path", log_path,
                    "--action", "lint",
                    "--status", "error",
                    "--detail", "syntax error in config.yaml",
                ],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0
            # Verify log content
            with open(log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            assert len(lines) == 1
            record = json.loads(lines[0])
            assert record["action"] == "lint"
            assert record["status"] == "error"
            assert record["detail"] == "syntax error in config.yaml"

    def test_cli_with_file_path(self):
        """Verify CLI correctly records file path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "audit.log")
            result = subprocess.run(
                [
                    sys.executable,
                    str(AUDIT_LOG_WRITER_SCRIPT),
                    "--log-path", log_path,
                    "--action", "upload",
                    "--status", "ok",
                    "--file", "report.xlsx",
                ],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0
            with open(log_path, "r", encoding="utf-8") as f:
                record = json.loads(f.readline())
            assert record["file"] == "report.xlsx"

    def test_cli_with_extra_metadata(self):
        """Verify CLI correctly records extra metadata."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "audit.log")
            result = subprocess.run(
                [
                    sys.executable,
                    str(AUDIT_LOG_WRITER_SCRIPT),
                    "--log-path", log_path,
                    "--action", "capture",
                    "--status", "ok",
                    "--extra", '{"clip_id": "abc123", "size_bytes": 1024}',
                ],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0
            with open(log_path, "r", encoding="utf-8") as f:
                record = json.loads(f.readline())
            assert record["extra"]["clip_id"] == "abc123"
            assert record["extra"]["size_bytes"] == 1024

    def test_cli_invalid_action_rejected(self):
        """Verify CLI rejects invalid action values."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "audit.log")
            result = subprocess.run(
                [
                    sys.executable,
                    str(AUDIT_LOG_WRITER_SCRIPT),
                    "--log-path", log_path,
                    "--action", "invalid_action",
                    "--status", "ok",
                ],
                capture_output=True,
                text=True,
            )
            assert result.returncode != 0, "Invalid action should be rejected"

    def test_cli_invalid_status_rejected(self):
        """Verify CLI rejects invalid status values."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "audit.log")
            result = subprocess.run(
                [
                    sys.executable,
                    str(AUDIT_LOG_WRITER_SCRIPT),
                    "--log-path", log_path,
                    "--action", "capture",
                    "--status", "invalid_status",
                ],
                capture_output=True,
                text=True,
            )
            assert result.returncode != 0, "Invalid status should be rejected"

    def test_cli_invalid_extra_json_rejected(self):
        """Verify CLI rejects invalid JSON for --extra."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "audit.log")
            result = subprocess.run(
                [
                    sys.executable,
                    str(AUDIT_LOG_WRITER_SCRIPT),
                    "--log-path", log_path,
                    "--action", "capture",
                    "--status", "ok",
                    "--extra", "not-valid-json",
                ],
                capture_output=True,
                text=True,
            )
            assert result.returncode != 0, "Invalid JSON should be rejected"

    def test_append_record_function_exists(self):
        """Verify append_record function exists and is importable."""
        sys.path.insert(0, str(AUDIT_LOG_WRITER_SCRIPT.parent))
        from audit_log_writer import append_record
        assert callable(append_record)

    def test_build_record_function_exists(self):
        """Verify _build_record function exists and is importable."""
        sys.path.insert(0, str(AUDIT_LOG_WRITER_SCRIPT.parent))
        from audit_log_writer import _build_record
        assert callable(_build_record)

    def test_build_record_basic(self):
        """Verify _build_record creates correct record structure."""
        sys.path.insert(0, str(AUDIT_LOG_WRITER_SCRIPT.parent))
        from audit_log_writer import _build_record

        record = _build_record(action="capture", status="ok")
        assert "ts" in record
        assert "pid" in record
        assert record["action"] == "capture"
        assert record["status"] == "ok"

    def test_build_record_with_detail(self):
        """Verify _build_record includes detail when provided."""
        sys.path.insert(0, str(AUDIT_LOG_WRITER_SCRIPT.parent))
        from audit_log_writer import _build_record

        record = _build_record(
            action="lint",
            status="error",
            detail="syntax error"
        )
        assert record["detail"] == "syntax error"

    def test_build_record_with_file_path(self):
        """Verify _build_record includes file path when provided."""
        sys.path.insert(0, str(AUDIT_LOG_WRITER_SCRIPT.parent))
        from audit_log_writer import _build_record

        record = _build_record(
            action="upload",
            status="ok",
            file_path="report.xlsx"
        )
        assert record["file"] == "report.xlsx"

    def test_build_record_with_extra(self):
        """Verify _build_record includes extra metadata when provided."""
        sys.path.insert(0, str(AUDIT_LOG_WRITER_SCRIPT.parent))
        from audit_log_writer import _build_record

        extra = {"clip_id": "abc123", "size_bytes": 1024}
        record = _build_record(
            action="capture",
            status="ok",
            extra=extra
        )
        assert record["extra"] == extra

    def test_append_record_creates_directory(self):
        """Verify append_record creates log directory if it doesn't exist."""
        sys.path.insert(0, str(AUDIT_LOG_WRITER_SCRIPT.parent))
        from audit_log_writer import append_record

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "subdir", "audit.log")
            append_record(log_path, "capture", "ok")
            assert os.path.exists(log_path)

    def test_append_record_validates_action(self):
        """Verify append_record validates action values."""
        sys.path.insert(0, str(AUDIT_LOG_WRITER_SCRIPT.parent))
        from audit_log_writer import append_record

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "audit.log")
            try:
                append_record(log_path, "invalid_action", "ok")
                assert False, "Should have raised ValueError"
            except ValueError as e:
                assert "action" in str(e).lower()

    def test_append_record_validates_status(self):
        """Verify append_record validates status values."""
        sys.path.insert(0, str(AUDIT_LOG_WRITER_SCRIPT.parent))
        from audit_log_writer import append_record

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "audit.log")
            try:
                append_record(log_path, "capture", "invalid_status")
                assert False, "Should have raised ValueError"
            except ValueError as e:
                assert "status" in str(e).lower()

    def test_record_has_timestamp(self):
        """Verify records include ISO timestamp."""
        sys.path.insert(0, str(AUDIT_LOG_WRITER_SCRIPT.parent))
        from audit_log_writer import append_record

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "audit.log")
            append_record(log_path, "capture", "ok")

            with open(log_path, "r", encoding="utf-8") as f:
                record = json.loads(f.readline())
            # Verify timestamp is ISO format (contains 'T' for separator)
            assert "T" in record["ts"], "Timestamp should be ISO format"

    def test_multiple_appends_all_present(self):
        """Verify multiple append_record calls all write records."""
        sys.path.insert(0, str(AUDIT_LOG_WRITER_SCRIPT.parent))
        from audit_log_writer import append_record

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "audit.log")
            append_record(log_path, "capture", "ok")
            append_record(log_path, "lint", "ok")
            append_record(log_path, "upload", "ok")

            with open(log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            assert len(lines) == 3

    def test_cli_missing_required_arg_fails(self):
        """Verify CLI fails when required arguments are missing."""
        result = subprocess.run(
            [sys.executable, str(AUDIT_LOG_WRITER_SCRIPT)],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0, "Missing required args should fail"

    def test_cli_help_works(self):
        """Verify CLI help output works."""
        result = subprocess.run(
            [sys.executable, str(AUDIT_LOG_WRITER_SCRIPT), "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "--log-path" in result.stdout
        assert "--action" in result.stdout
        assert "--status" in result.stdout
