"""Tests for bin/audit_log.py."""

import csv
import json
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from bin.audit_log import AuditLog, main


@pytest.fixture
def temp_db():
    """Create a temporary database file."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    # Cleanup
    try:
        os.unlink(path)
    except OSError:
        pass


class TestAuditLog:
    """Tests for AuditLog class."""

    def test_init_creates_tables(self, temp_db):
        """Test that init_schema creates the required tables."""
        with AuditLog(temp_db) as audit:
            audit.init_schema()

            # Check tables exist
            cursor = audit.conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' "
                "AND name IN ('submissions', 'events')"
            )
            tables = {row[0] for row in cursor.fetchall()}
            assert "submissions" in tables
            assert "events" in tables

    def test_record_submission_returns_id(self, temp_db):
        """Test that record_submission returns a valid submission ID."""
        with AuditLog(temp_db) as audit:
            audit.init_schema()

            submission_id = audit.record_submission(
                vendor_id="vendor-001",
                batch_id="batch-A",
                clip_id="clip-00042",
                sha256="abc123def456",
                size_bytes=524288000,
                lint_status="PASS",
                lint_details="",
            )

            assert submission_id == 1

            # Verify the record was inserted
            cursor = audit.conn.cursor()
            cursor.execute("SELECT * FROM submissions WHERE id = ?", (submission_id,))
            row = cursor.fetchone()
            assert row is not None

    def test_record_event_links_to_submission(self, temp_db):
        """Test that record_event correctly links to submission."""
        with AuditLog(temp_db) as audit:
            audit.init_schema()

            submission_id = audit.record_submission(
                vendor_id="vendor-001",
                batch_id="batch-A",
                clip_id="clip-00042",
                sha256="abc123def456",
                size_bytes=524288000,
                lint_status="PASS",
            )

            audit.record_event(
                submission_id=submission_id,
                event_type="received",
                message="Submission received successfully",
            )

            cursor = audit.conn.cursor()
            cursor.execute(
                "SELECT submission_id, event_type, message FROM events WHERE submission_id = ?",
                (submission_id,),
            )
            row = cursor.fetchone()
            assert row is not None
            assert row[0] == submission_id
            assert row[1] == "received"
            assert row[2] == "Submission received successfully"

    def test_query_vendor_summary_aggregates(self, temp_db):
        """Test that query_vendor_summary returns correct aggregates."""
        with AuditLog(temp_db) as audit:
            audit.init_schema()

            # Insert multiple submissions
            audit.record_submission(
                vendor_id="vendor-001",
                batch_id="batch-A",
                clip_id="clip-001",
                sha256="hash1",
                size_bytes=1000,
                lint_status="PASS",
            )
            audit.record_submission(
                vendor_id="vendor-001",
                batch_id="batch-A",
                clip_id="clip-002",
                sha256="hash2",
                size_bytes=2000,
                lint_status="PASS",
            )
            audit.record_submission(
                vendor_id="vendor-001",
                batch_id="batch-B",
                clip_id="clip-003",
                sha256="hash3",
                size_bytes=3000,
                lint_status="FAIL",
            )

            summary = audit.query_vendor_summary("vendor-001")

            assert summary["total_submissions"] == 3
            assert summary["lint_pass_rate"] == pytest.approx(66.67, rel=0.01)
            assert summary["last_submission"] is not None
            assert summary["by_status"] == {"PASS": 2, "FAIL": 1}

    def test_query_batch_status_returns_all(self, temp_db):
        """Test that query_batch_status returns all submissions with events."""
        with AuditLog(temp_db) as audit:
            audit.init_schema()

            # Insert submissions for batch-A
            sub_id1 = audit.record_submission(
                vendor_id="vendor-001",
                batch_id="batch-A",
                clip_id="clip-001",
                sha256="hash1",
                size_bytes=1000,
                lint_status="PASS",
            )
            audit.record_event(sub_id1, "received", "Got it")
            audit.record_event(sub_id1, "accepted", "Accepted")

            sub_id2 = audit.record_submission(
                vendor_id="vendor-001",
                batch_id="batch-A",
                clip_id="clip-002",
                sha256="hash2",
                size_bytes=2000,
                lint_status="FAIL",
            )
            audit.record_event(sub_id2, "received", "Got it")
            audit.record_event(sub_id2, "rejected", "Bad format")

            # Insert submission for different batch
            audit.record_submission(
                vendor_id="vendor-001",
                batch_id="batch-B",
                clip_id="clip-003",
                sha256="hash3",
                size_bytes=3000,
                lint_status="PASS",
            )

            batch_status = audit.query_batch_status("batch-A")

            assert len(batch_status) == 2
            assert batch_status[0]["clip_id"] == "clip-001"
            assert len(batch_status[0]["events"]) == 2
            assert batch_status[1]["clip_id"] == "clip-002"
            assert len(batch_status[1]["events"]) == 2

    def test_export_csv_writes_file(self, temp_db):
        """Test that export_csv writes correct CSV file."""
        with AuditLog(temp_db) as audit:
            audit.init_schema()

            audit.record_submission(
                vendor_id="vendor-001",
                batch_id="batch-A",
                clip_id="clip-001",
                sha256="hash1",
                size_bytes=1000,
                lint_status="PASS",
                lint_details="All good",
            )
            audit.record_submission(
                vendor_id="vendor-001",
                batch_id="batch-A",
                clip_id="clip-002",
                sha256="hash2",
                size_bytes=2000,
                lint_status="FAIL",
                lint_details="Bad format",
            )
            audit.record_submission(
                vendor_id="vendor-002",
                batch_id="batch-B",
                clip_id="clip-003",
                sha256="hash3",
                size_bytes=3000,
                lint_status="PASS",
            )

            # Export all
            with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
                csv_path = f.name

            try:
                count = audit.export_csv(csv_path)
                assert count == 3

                with open(csv_path) as f:
                    reader = csv.DictReader(f)
                    rows = list(reader)
                    assert len(rows) == 3

                # Export filtered by vendor
                count = audit.export_csv(csv_path, vendor_id="vendor-001")
                assert count == 2

                with open(csv_path) as f:
                    reader = csv.DictReader(f)
                    rows = list(reader)
                    assert len(rows) == 2
                    assert all(r["vendor_id"] == "vendor-001" for r in rows)
            finally:
                os.unlink(csv_path)

    def test_double_init_idempotent(self, temp_db):
        """Test that calling init_schema twice doesn't cause errors."""
        with AuditLog(temp_db) as audit:
            audit.init_schema()
            audit.init_schema()  # Should not raise

            # Verify tables still work
            cursor = audit.conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='submissions'"
            )
            assert cursor.fetchone() is not None


class TestCLI:
    """Tests for CLI functionality."""

    def test_cli_init(self, temp_db):
        """Test CLI --init flag."""
        result = main(["--init", "--db", temp_db])
        assert result == 0

        # Verify tables exist
        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='submissions'")
        assert cursor.fetchone() is not None
        conn.close()

    def test_cli_record_submission(self, temp_db):
        """Test CLI --record-submission."""
        main(["--init", "--db", temp_db])

        result = main(
            [
                "--record-submission",
                "--db",
                temp_db,
                "--vendor-id",
                "vendor-001",
                "--batch-id",
                "batch-A",
                "--clip-id",
                "clip-00042",
                "--sha256",
                "abc123",
                "--size",
                "524288000",
                "--lint-status",
                "PASS",
            ]
        )
        assert result == 0

    def test_cli_vendor_summary(self, temp_db, capsys):
        """Test CLI --vendor-summary."""
        main(["--init", "--db", temp_db])
        main(
            [
                "--record-submission",
                "--db",
                temp_db,
                "--vendor-id",
                "vendor-001",
                "--batch-id",
                "batch-A",
                "--clip-id",
                "clip-001",
                "--sha256",
                "hash1",
                "--size",
                "1000",
                "--lint-status",
                "PASS",
            ]
        )

        # Clear previous captures
        capsys.readouterr()

        result = main(["--vendor-summary", "vendor-001", "--db", temp_db])
        assert result == 0

        captured = capsys.readouterr()
        summary = json.loads(captured.out)
        assert summary["total_submissions"] == 1

    def test_cli_batch_status(self, temp_db, capsys):
        """Test CLI --batch-status."""
        main(["--init", "--db", temp_db])
        main(
            [
                "--record-submission",
                "--db",
                temp_db,
                "--vendor-id",
                "vendor-001",
                "--batch-id",
                "batch-A",
                "--clip-id",
                "clip-001",
                "--sha256",
                "hash1",
                "--size",
                "1000",
                "--lint-status",
                "PASS",
            ]
        )

        # Clear previous captures
        capsys.readouterr()

        result = main(["--batch-status", "batch-A", "--db", temp_db])
        assert result == 0

        captured = capsys.readouterr()
        status = json.loads(captured.out)
        assert len(status) == 1
        assert status[0]["clip_id"] == "clip-001"

    def test_cli_export_csv(self, temp_db, capsys):
        """Test CLI --export-csv."""
        main(["--init", "--db", temp_db])
        main(
            [
                "--record-submission",
                "--db",
                temp_db,
                "--vendor-id",
                "vendor-001",
                "--batch-id",
                "batch-A",
                "--clip-id",
                "clip-001",
                "--sha256",
                "hash1",
                "--size",
                "1000",
                "--lint-status",
                "PASS",
            ]
        )

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            csv_path = f.name

        try:
            # Clear previous captures
            capsys.readouterr()

            result = main(["--export-csv", csv_path, "--db", temp_db])
            assert result == 0
            assert Path(csv_path).exists()

            captured = capsys.readouterr()
            assert "Exported 1 submissions" in captured.out
        finally:
            os.unlink(csv_path)
