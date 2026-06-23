#!/usr/bin/env python3
"""
server_ingest_worker.py - SQS consumer + S3 download + lint dispatch + Postgres write.

Consumes messages from SQS, downloads files from S3, dispatches to a lint service,
and writes results to Postgres.

Usage:
    python3 bin/server_ingest_worker.py [--log-level LEVEL] [--once]

Required env vars: SQS_QUEUE_URL, S3_BUCKET, DB_HOST, DB_PORT, DB_NAME,
    DB_USER, DB_PASSWORD, LINT_SERVICE_URL
Optional: AWS_REGION, POLL_INTERVAL, MAX_RETRIES, DB_SCHEMA
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import signal
import sys
import tempfile
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)
_SHUTDOWN = False


def _handle_signal(signum: int, _frame: Any) -> None:
    """Set shutdown flag for graceful termination."""
    global _SHUTDOWN
    logger.info("Signal %s received, shutting down...", signum)
    _SHUTDOWN = True


def load_config() -> dict[str, Any]:
    """Load configuration from environment variables."""
    required = [
        "SQS_QUEUE_URL", "S3_BUCKET", "DB_HOST", "DB_PORT",
        "DB_NAME", "DB_USER", "DB_PASSWORD", "LINT_SERVICE_URL",
    ]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise ValueError(f"Missing required env vars: {', '.join(missing)}")
    return {
        "sqs_queue_url": os.environ["SQS_QUEUE_URL"],
        "s3_bucket": os.environ["S3_BUCKET"],
        "db_host": os.environ["DB_HOST"],
        "db_port": int(os.environ.get("DB_PORT", "5432")),
        "db_name": os.environ["DB_NAME"],
        "db_user": os.environ["DB_USER"],
        "db_password": os.environ["DB_PASSWORD"],
        "lint_service_url": os.environ["LINT_SERVICE_URL"].rstrip("/"),
        "poll_interval": int(os.environ.get("POLL_INTERVAL", "5")),
        "max_retries": int(os.environ.get("MAX_RETRIES", "3")),
        "region": os.environ.get("AWS_REGION", "us-east-1"),
        "db_schema": os.environ.get("DB_SCHEMA", "public"),
    }


def setup_logging(level: str = "INFO") -> None:
    """Configure root logger."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


class SQSClient:
    """Thin wrapper around boto3 SQS for long-poll message consumption."""

    def __init__(self, queue_url: str, region: str) -> None:
        self.queue_url = queue_url
        self.region = region
        self._client: Any = None

    @property
    def client(self) -> Any:
        """Lazily initialise the boto3 SQS client."""
        if self._client is None:
            import boto3
            self._client = boto3.client("sqs", region_name=self.region)
        return self._client

    def receive(self, wait_seconds: int = 10) -> Optional[dict[str, Any]]:
        """Receive a single message with long-polling."""
        resp = self.client.receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=wait_seconds,
            VisibilityTimeout=60,
        )
        messages = resp.get("Messages", [])
        if not messages:
            return None
        msg = messages[0]
        return {
            "receipt_handle": msg["ReceiptHandle"],
            "body": msg["Body"],
            "message_id": msg["MessageId"],
        }

    def delete(self, receipt_handle: str) -> None:
        """Delete a processed message from the queue."""
        self.client.delete_message(
            QueueUrl=self.queue_url,
            ReceiptHandle=receipt_handle,
        )


class S3Downloader:
    """Download objects from S3 to a local temp directory."""

    def __init__(self, bucket: str, region: str) -> None:
        self.bucket = bucket
        self.region = region
        self._client: Any = None

    @property
    def client(self) -> Any:
        """Lazily initialise the boto3 S3 client."""
        if self._client is None:
            import boto3
            self._client = boto3.client("s3", region_name=self.region)
        return self._client

    def download(self, key: str, dest_dir: str) -> Path:
        """Download an S3 object to *dest_dir* and return the local path."""
        dest = Path(dest_dir) / Path(key).name
        self.client.download_file(self.bucket, key, str(dest))
        return dest


def compute_sha256(path: Path) -> str:
    """Return the hex SHA-256 digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def dispatch_lint(lint_url: str, file_path: Path, file_hash: str) -> dict[str, Any]:
    """POST a file to the lint service and return the JSON result."""
    url = f"{lint_url}/lint"
    boundary = "----FormBoundary" + hashlib.md5(os.urandom(8)).hexdigest()
    body_parts: list[bytes] = []
    body_parts.append(f"--{boundary}\r\n".encode())
    body_parts.append(
        b'Content-Disposition: form-data; name="file"; '
        + f'filename="{file_path.name}"\r\n'.encode()
    )
    body_parts.append(b"Content-Type: application/octet-stream\r\n\r\n")
    body_parts.append(file_path.read_bytes())
    body_parts.append(f"\r\n--{boundary}\r\n".encode())
    body_parts.append(
        f'Content-Disposition: form-data; name="file_hash"\r\n\r\n'
        f"{file_hash}\r\n--{boundary}--\r\n".encode()
    )
    body = b"".join(body_parts)
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode())


def write_result_to_db(
    config: dict[str, Any],
    message_id: str,
    s3_key: str,
    file_hash: str,
    lint_result: dict[str, Any],
) -> None:
    """Insert a lint result row into Postgres using psycopg2."""
    import psycopg2

    status = lint_result.get("status", "unknown")
    issues = json.dumps(lint_result.get("issues", []))
    created_at = datetime.now(timezone.utc).isoformat()

    conn = psycopg2.connect(
        host=config["db_host"],
        port=config["db_port"],
        dbname=config["db_name"],
        user=config["db_user"],
        password=config["db_password"],
    )
    try:
        cur = conn.cursor()
        schema = config["db_schema"]
        cur.execute(
            f"""
            INSERT INTO {schema}.ingest_results
                (message_id, s3_key, file_hash, status, issues, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (message_id, s3_key, file_hash, status, issues, created_at),
        )
        conn.commit()
        logger.info("Wrote result for message_id=%s", message_id)
    finally:
        conn.close()


def process_message(
    config: dict[str, Any],
    sqs: SQSClient,
    s3: S3Downloader,
    message: dict[str, Any],
) -> bool:
    """Download, lint, and persist a single SQS message. Returns True on success."""
    body = json.loads(message["body"])
    s3_key = body.get("s3_key") or body.get("key")
    if not s3_key:
        logger.error("No s3_key in message %s", message["message_id"])
        return False

    tmpdir = tempfile.mkdtemp(prefix="ingest_")
    try:
        local_path = s3.download(s3_key, tmpdir)
        file_hash = compute_sha256(local_path)
        lint_result = dispatch_lint(config["lint_service_url"], local_path, file_hash)
        write_result_to_db(
            config, message["message_id"], s3_key, file_hash, lint_result,
        )
        sqs.delete(message["receipt_handle"])
        logger.info("Successfully processed %s", s3_key)
        return True
    except Exception:
        logger.exception("Failed to process message %s", message["message_id"])
        return False
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    """Entry point: parse args, configure, and run the SQS consumer loop."""
    parser = argparse.ArgumentParser(description="SQS ingest worker")
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Process a single message and exit",
    )
    args = parser.parse_args(argv)

    setup_logging(args.log_level)

    try:
        config = load_config()
    except ValueError as exc:
        logger.error(str(exc))
        return 1

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    sqs = SQSClient(config["sqs_queue_url"], config["region"])
    s3 = S3Downloader(config["s3_bucket"], config["region"])

    logger.info("Worker started (once=%s)", args.once)
    while not _SHUTDOWN:
        msg = sqs.receive(wait_seconds=min(config["poll_interval"], 20))
        if msg is None:
            if args.once:
                logger.info("No messages; exiting (--once)")
                return 0
            continue

        success = process_message(config, sqs, s3, msg)
        if args.once:
            return 0 if success else 1

    logger.info("Shutdown complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
