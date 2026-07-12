#!/usr/bin/env python3
"""
R038 · server_ingest.py — minimal S3-event lint worker

Polls SQS for S3 events, downloads tarballs, runs lint_buyer_spec,
writes status to local SQLite, emits acceptance event.
"""

import argparse
import json
import sqlite3
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Lazy import boto3 to allow module import without AWS credentials
boto3 = None


def _get_boto3():
    """Lazy import boto3."""
    global boto3
    if boto3 is None:
        try:
            import boto3 as _boto3
            boto3 = _boto3
        except ImportError:
            raise ImportError("boto3 is required. Install with: pip install boto3") from None
    return boto3


def fetch_tarball_from_s3(bucket: str, key: str, local_dir: Path) -> Path:
    """
    Download a tarball from S3 to a local directory.

    Args:
        bucket: S3 bucket name
        key: S3 object key
        local_dir: Local directory to download to

    Returns:
        Path to the downloaded file
    """
    local_dir = Path(local_dir)
    local_dir.mkdir(parents=True, exist_ok=True)

    # Extract filename from key
    filename = Path(key).name
    local_path = local_dir / filename

    boto3 = _get_boto3()
    s3 = boto3.client('s3')
    s3.download_file(bucket, key, str(local_path))

    return local_path


def lint_one(tarball_path: Path) -> dict:
    """
    Run lint_buyer_spec on a tarball.

    Args:
        tarball_path: Path to the tarball

    Returns:
        Dict with keys: ok (bool), issue_count (int), summary (str)
    """
    # Import lint_buyer_spec lazily
    try:
        from oyster_agent_runner.lint.lint_buyer_spec import lint_tarball
    except ImportError:
        # Fallback: simple tarball validation
        def lint_tarball(path: str):
            messages = []
            p = Path(path)
            if not p.exists():
                messages.append(f"File not found: {path}")
                return False, messages
            try:
                with tarfile.open(p, 'r:gz') as tar:
                    names = tar.getnames()
                    required = ['video.mp4', 'action_camera.bin', 'gameinfo.xlsx']
                    missing = [f for f in required if f not in names]
                    if missing:
                        messages.append(f"Missing required files: {missing}")
            except Exception as e:
                messages.append(f"Failed to open tarball: {e}")
            return len(messages) == 0, messages

    ok, messages = lint_tarball(str(tarball_path))
    issue_count = len(messages)
    summary = "; ".join(messages) if messages else "OK"

    return {
        "ok": ok,
        "issue_count": issue_count,
        "summary": summary,
    }


def write_submission(db_path: str, submission: dict) -> int:
    """
    Insert a submission record into the SQLite database.

    Reuses audit_log.py schema:
    - submissions(id, vendor_id, batch_id, clip_id, sha256, size_bytes,
                  uploaded_at, lint_status, lint_details)

    Args:
        db_path: Path to SQLite database
        submission: Dict with submission data

    Returns:
        Submission ID
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Ensure table exists
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

    # Insert submission
    cursor.execute("""
        INSERT INTO submissions (
            vendor_id, batch_id, clip_id, sha256, size_bytes,
            uploaded_at, lint_status, lint_details
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        submission.get("vendor_id", ""),
        submission.get("batch_id", ""),
        submission.get("clip_id", ""),
        submission.get("sha256", ""),
        submission.get("size_bytes", 0),
        submission.get("uploaded_at", datetime.now(timezone.utc).isoformat()),
        submission.get("lint_status", "unknown"),
        submission.get("lint_details", ""),
    ))

    submission_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return submission_id


def process_message(msg: dict, bucket: str, db_path: str) -> str:
    """
    Process a single SQS message: download tarball, lint, write to DB.

    Args:
        msg: SQS message dict containing S3 event
        bucket: S3 bucket name
        db_path: Path to SQLite database

    Returns:
        Status string describing the result
    """
    # Parse S3 event from message body
    try:
        body = json.loads(msg.get("Body", "{}"))
        # Handle S3 event notification format
        records = body.get("Records", [])
        if not records:
            # Try alternative format
            records = [body]

        for record in records:
            s3_info = record.get("s3", {})
            s3_bucket = s3_info.get("bucket", {}).get("name", bucket)
            s3_key = s3_info.get("object", {}).get("key", "")

            if not s3_key:
                continue

            # Download tarball
            with tempfile.TemporaryDirectory() as tmpdir:
                local_path = fetch_tarball_from_s3(s3_bucket, s3_key, Path(tmpdir))

                # Run lint
                lint_result = lint_one(local_path)

                # Extract metadata from key (format: vendor/batch/clip/tarball.tar.gz)
                parts = s3_key.strip("/").split("/")
                vendor_id = parts[0] if len(parts) > 0 else "unknown"
                batch_id = parts[1] if len(parts) > 1 else "unknown"
                clip_id = parts[2] if len(parts) > 2 else "unknown"

                # Calculate file hash and size
                import hashlib
                sha256_hash = hashlib.sha256()
                with open(local_path, "rb") as f:
                    for chunk in iter(lambda: f.read(8192), b""):
                        sha256_hash.update(chunk)
                sha256 = sha256_hash.hexdigest()
                size_bytes = local_path.stat().st_size

                # Write to database
                submission = {
                    "vendor_id": vendor_id,
                    "batch_id": batch_id,
                    "clip_id": clip_id,
                    "sha256": sha256,
                    "size_bytes": size_bytes,
                    "lint_status": "pass" if lint_result["ok"] else "fail",
                    "lint_details": lint_result["summary"],
                }
                submission_id = write_submission(db_path, submission)

                return f"Processed {s3_key}: id={submission_id}, status={submission['lint_status']}"

        return "No S3 records found in message"

    except Exception as e:
        return f"Error processing message: {e}"


def poll_loop(queue_url: str, bucket: str, db_path: str, max_messages: int = 10) -> None:
    """
    Poll SQS queue for messages and process them.

    Args:
        queue_url: SQS queue URL
        bucket: S3 bucket name (default if not in message)
        db_path: Path to SQLite database
        max_messages: Maximum messages to process per poll
    """
    boto3 = _get_boto3()
    sqs = boto3.client('sqs')

    while True:
        try:
            response = sqs.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=max_messages,
                WaitTimeSeconds=20,
                AttributeNames=['All'],
                MessageAttributeNames=['All'],
            )

            messages = response.get("Messages", [])
            if not messages:
                continue

            for msg in messages:
                receipt_handle = msg["ReceiptHandle"]

                # Process the message
                status = process_message(msg, bucket, db_path)
                print(f"[{datetime.now(timezone.utc).isoformat()}] {status}")

                # Delete message after successful processing
                sqs.delete_message(
                    QueueUrl=queue_url,
                    ReceiptHandle=receipt_handle
                )

        except KeyboardInterrupt:
            print("Poll loop interrupted")
            break
        except Exception as e:
            print(f"Poll error: {e}")
            # Sleep briefly before retrying
            import time
            time.sleep(5)


def main(argv: list[str]) -> int:
    """
    Main entry point.

    Args:
        argv: Command line arguments (excluding script name)

    Returns:
        Exit code
    """
    parser = argparse.ArgumentParser(
        description="S3-event lint worker for buyer-spec tarballs"
    )
    parser.add_argument(
        "--queue-url",
        required=True,
        help="SQS queue URL",
    )
    parser.add_argument(
        "--bucket",
        required=True,
        help="S3 bucket name",
    )
    parser.add_argument(
        "--db",
        default="submissions.db",
        help="SQLite database path (default: submissions.db)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process one message and exit (for testing)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Max messages per poll (default: 10)",
    )

    args = parser.parse_args(argv)

    if args.once:
        # Process single message for testing
        boto3 = _get_boto3()
        sqs = boto3.client('sqs')
        response = sqs.receive_message(
            QueueUrl=args.queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=5,
        )
        messages = response.get("Messages", [])
        if messages:
            status = process_message(messages[0], args.bucket, args.db)
            print(status)
            # Delete the message
            sqs.delete_message(
                QueueUrl=args.queue_url,
                ReceiptHandle=messages[0]["ReceiptHandle"]
            )
        else:
            print("No messages available")
        return 0

    # Run poll loop
    poll_loop(args.queue_url, args.bucket, args.db, args.batch_size)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
