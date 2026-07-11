#!/usr/bin/env python3
"""SQLite-backed audit log for vendor submissions."""

import argparse
import csv
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class AuditLog:
    """SQLite-backed audit log for vendor submissions."""

    def __init__(self, db_path: str = "audit.db"):
        """Initialize audit log with database path."""
        self.db_path = db_path
        self.conn: sqlite3.Connection | None = None

    def __enter__(self) -> "AuditLog":
        """Enter context manager, open connection."""
        self.conn = sqlite3.connect(self.db_path)
        # Enable WAL mode for concurrent reads
        self.conn.execute("PRAGMA journal_mode=WAL")
        # Enable foreign keys
        self.conn.execute("PRAGMA foreign_keys=ON")
        return self

    def __exit__(self, *exc):
        """Exit context manager, close connection."""
        if self.conn:
            self.conn.close()
            self.conn = None

    def init_schema(self) -> None:
        """Create tables if not exist."""
        if not self.conn:
            raise RuntimeError("Database not connected. Use with context manager.")

        cursor = self.conn.cursor()

        # Create submissions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vendor_id TEXT NOT NULL,
                batch_id TEXT NOT NULL,
                clip_id TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                uploaded_at TEXT NOT NULL,
                lint_status TEXT NOT NULL,
                lint_details TEXT DEFAULT ''
            )
        """)

        # Create events table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                submission_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                message TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                FOREIGN KEY (submission_id) REFERENCES submissions(id)
            )
        """)

        # Create indexes
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_submissions_vendor_uploaded
            ON submissions(vendor_id, uploaded_at)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_submissions_batch
            ON submissions(batch_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_submissions_sha256
            ON submissions(sha256)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_submission
            ON events(submission_id)
        """)

        self.conn.commit()

    def record_submission(
        self,
        vendor_id: str,
        batch_id: str,
        clip_id: str,
        sha256: str,
        size_bytes: int,
        lint_status: str,
        lint_details: str = "",
    ) -> int:
        """Insert submission, return submission_id."""
        if not self.conn:
            raise RuntimeError("Database not connected. Use with context manager.")

        cursor = self.conn.cursor()
        uploaded_at = datetime.now(timezone.utc).isoformat()

        cursor.execute(
            """
            INSERT INTO submissions
            (vendor_id, batch_id, clip_id, sha256, size_bytes,
             uploaded_at, lint_status, lint_details)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (vendor_id, batch_id, clip_id, sha256,
             size_bytes, uploaded_at, lint_status, lint_details),
        )

        self.conn.commit()
        return cursor.lastrowid

    def record_event(
        self,
        submission_id: int,
        event_type: str,
        message: str,
    ) -> None:
        """Insert event.

        event_type in {received, lint_pass, lint_fail,
                       accepted, rejected, billed}.
        """
        if not self.conn:
            raise RuntimeError("Database not connected. Use with context manager.")

        cursor = self.conn.cursor()
        occurred_at = datetime.now(timezone.utc).isoformat()

        cursor.execute("""
            INSERT INTO events (submission_id, event_type, message, occurred_at)
            VALUES (?, ?, ?, ?)
        """, (submission_id, event_type, message, occurred_at))

        self.conn.commit()

    def query_vendor_summary(self, vendor_id: str) -> dict:
        """Return vendor summary statistics."""
        if not self.conn:
            raise RuntimeError("Database not connected. Use with context manager.")

        cursor = self.conn.cursor()

        # Get total submissions and last submission time
        cursor.execute("""
            SELECT COUNT(*), MAX(uploaded_at)
            FROM submissions
            WHERE vendor_id = ?
        """, (vendor_id,))
        row = cursor.fetchone()
        total_submissions = row[0] if row else 0
        last_submission = row[1] if row and row[1] else None

        # Get lint pass rate
        cursor.execute("""
            SELECT
                SUM(CASE WHEN lint_status = 'PASS' THEN 1 ELSE 0 END) * 100.0 / COUNT(*)
            FROM submissions
            WHERE vendor_id = ?
        """, (vendor_id,))
        row = cursor.fetchone()
        lint_pass_rate = round(row[0], 2) if row and row[0] is not None else 0.0

        # Get by_status breakdown
        cursor.execute("""
            SELECT lint_status, COUNT(*)
            FROM submissions
            WHERE vendor_id = ?
            GROUP BY lint_status
        """, (vendor_id,))
        by_status = {row[0]: row[1] for row in cursor.fetchall()}

        return {
            "total_submissions": total_submissions,
            "lint_pass_rate": lint_pass_rate,
            "last_submission": last_submission,
            "by_status": by_status,
        }

    def query_batch_status(self, batch_id: str) -> list[dict]:
        """All submissions for a batch with full event history."""
        if not self.conn:
            raise RuntimeError("Database not connected. Use with context manager.")

        cursor = self.conn.cursor()

        # Get all submissions for the batch
        cursor.execute("""
            SELECT id, vendor_id, batch_id, clip_id, sha256, size_bytes,
                   uploaded_at, lint_status, lint_details
            FROM submissions
            WHERE batch_id = ?
            ORDER BY id
        """, (batch_id,))

        submissions = []
        for row in cursor.fetchall():
            submission_id = row[0]
            submission = {
                "id": submission_id,
                "vendor_id": row[1],
                "batch_id": row[2],
                "clip_id": row[3],
                "sha256": row[4],
                "size_bytes": row[5],
                "uploaded_at": row[6],
                "lint_status": row[7],
                "lint_details": row[8],
                "events": [],
            }

            # Get events for this submission
            cursor.execute("""
                SELECT id, event_type, message, occurred_at
                FROM events
                WHERE submission_id = ?
                ORDER BY id
            """, (submission_id,))

            for event_row in cursor.fetchall():
                submission["events"].append({
                    "id": event_row[0],
                    "event_type": event_row[1],
                    "message": event_row[2],
                    "occurred_at": event_row[3],
                })

            submissions.append(submission)

        return submissions

    def export_csv(self, out_path: str, vendor_id: str | None = None) -> int:
        """Export filtered submissions to CSV. Returns count of exported rows."""
        if not self.conn:
            raise RuntimeError("Database not connected. Use with context manager.")

        cursor = self.conn.cursor()

        if vendor_id:
            cursor.execute("""
                SELECT id, vendor_id, batch_id, clip_id, sha256, size_bytes,
                       uploaded_at, lint_status, lint_details
                FROM submissions
                WHERE vendor_id = ?
                ORDER BY id
            """, (vendor_id,))
        else:
            cursor.execute("""
                SELECT id, vendor_id, batch_id, clip_id, sha256, size_bytes,
                       uploaded_at, lint_status, lint_details
                FROM submissions
                ORDER BY id
            """)

        rows = cursor.fetchall()

        # Ensure parent directory exists
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)

        with open(out_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "id", "vendor_id", "batch_id", "clip_id", "sha256",
                "size_bytes", "uploaded_at", "lint_status", "lint_details"
            ])
            for row in rows:
                writer.writerow(row)

        return len(rows)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Vendor submission audit log")
    parser.add_argument("--db", default="audit.db", help="Database path")
    parser.add_argument("--init", action="store_true", help="Create tables")
    parser.add_argument("--record-submission", action="store_true",
                        help="Record a submission")
    parser.add_argument("--vendor-id", help="Vendor ID")
    parser.add_argument("--batch-id", help="Batch ID")
    parser.add_argument("--clip-id", help="Clip ID")
    parser.add_argument("--sha256", help="SHA256 hash")
    parser.add_argument("--size", type=int, help="Size in bytes")
    parser.add_argument("--lint-status", help="Lint status (PASS/FAIL)")
    parser.add_argument("--lint-details", default="", help="Lint details")
    parser.add_argument("--vendor-summary", metavar="ID",
                        help="Print vendor summary JSON")
    parser.add_argument("--batch-status", metavar="ID",
                        help="Print batch all submissions")
    parser.add_argument("--export-csv", metavar="PATH",
                        help="Export submissions to CSV")

    args = parser.parse_args(argv)

    with AuditLog(args.db) as audit:
        if args.init:
            audit.init_schema()
            print("Database initialized.")
            return 0

        if args.record_submission:
            if not all([args.vendor_id, args.batch_id, args.clip_id,
                        args.sha256, args.size is not None, args.lint_status]):
                print("Error: Missing required fields for record-submission")
                return 1

            submission_id = audit.record_submission(
                vendor_id=args.vendor_id,
                batch_id=args.batch_id,
                clip_id=args.clip_id,
                sha256=args.sha256,
                size_bytes=args.size,
                lint_status=args.lint_status,
                lint_details=args.lint_details or "",
            )
            print(f"Recorded submission id={submission_id}")
            return 0

        if args.vendor_summary:
            summary = audit.query_vendor_summary(args.vendor_summary)
            print(json.dumps(summary, indent=2))
            return 0

        if args.batch_status:
            status = audit.query_batch_status(args.batch_status)
            print(json.dumps(status, indent=2))
            return 0

        if args.export_csv:
            count = audit.export_csv(args.export_csv, vendor_id=args.vendor_id)
            print(f"Exported {count} submissions to {args.export_csv}")
            return 0

        print("No action specified. Use --help for usage.")
        return 1


if __name__ == "__main__":
    exit(main())
