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
import time
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
    required = ["SQS_QUEUE_URL", "S3_BUCKET", "DB_HOST", "DB_PORT",
                "DB_NAME", "DB_USER", "DB_PASSWORD", "LINT_SERVICE_URL"]
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
        try:
            response = self.client.receive_message(
                QueueUrl=self.queue_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=wait_seconds,
                AttributeNames=["All"],
                MessageAttributeNames=["All"],
            )
            messages = response.get("Messages", [])
            if messages:
                msg = messages[0]
                logger.info("Received message ID: %s", msg["MessageId"])
                return {
                    "receipt_handle": msg["ReceiptHandle"],
                    "message_id": msg["MessageId"],
                    "body": json.loads(msg["Body"]),
                    "attributes": msg.get("Attributes", {}),
                    "message_attributes": msg.get("MessageAttributes", {}),
                }
        except Exception as e:
            logger.error("Failed to receive SQS message: %s", e)
        return None

    def delete(self, receipt_handle: str) -> None:
        """Delete a message from the queue."""
        try:
            self.client.delete_message(
                QueueUrl=self.queue_url,
                ReceiptHandle=receipt_handle,
            )
            logger.debug("Deleted message from queue")
        except Exception as e:
            logger.error("Failed to delete SQS message: %s", e)


class S3Client:
    """Thin wrapper around boto3 S3 for file downloads."""

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

    def download(self, key: str, dest_path: Path) -> bool:
        """Download a file from S3 to local path."""
        try:
            logger.info("Downloading s3://%s/%s to %s", self.bucket, key, dest_path)
            self.client.download_file(self.bucket, key, str(dest_path))
            return True
        except Exception as e:
            logger.error("Failed to download S3 object %s: %s", key, e)
            return False


class PostgresClient:
    """Thin wrapper around psycopg2 for database operations."""

    def __init__(self, host: str, port: int, database: str,
                 user: str, password: str, schema: str = "public") -> None:
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self.schema = schema
        self._conn: Any = None

    @property
    def conn(self) -> Any:
        """Lazily initialise the psycopg2 connection."""
        if self._conn is None or self._conn.closed:
            import psycopg2
            self._conn = psycopg2.connect(
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.user,
                password=self.password,
            )
            self._conn.autocommit = False
        return self._conn

    def write_result(self, message_id: str, s3_key: str, lint_result: dict[str, Any]) -> bool:
        """Write lint result to database."""
        try:
            with self.conn.cursor() as cur:
                # Create table if not exists
                cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS {self.schema}.lint_results (
                        id SERIAL PRIMARY KEY,
                        message_id VARCHAR(255) NOT NULL,
                        s3_key VARCHAR(1024) NOT NULL,
                        lint_status VARCHAR(50) NOT NULL,
                        lint_output JSONB,
                        error_message TEXT,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                        UNIQUE(message_id)
                    )
                """)
                
                # Insert result
                cur.execute(f"""
                    INSERT INTO {self.schema}.lint_results 
                    (message_id, s3_key, lint_status, lint_output, error_message)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (message_id) DO UPDATE SET
                        s3_key = EXCLUDED.s3_key,
                        lint_status = EXCLUDED.lint_status,
                        lint_output = EXCLUDED.lint_output,
                        error_message = EXCLUDED.error_message,
                        created_at = NOW()
                """, (
                    message_id,
                    s3_key,
                    lint_result.get("status", "unknown"),
                    json.dumps(lint_result.get("output", {})),
                    lint_result.get("error"),
                ))
                
                self.conn.commit()
                logger.info("Wrote result to database for message %s", message_id)
                return True
        except Exception as e:
            logger.error("Failed to write to database: %s", e)
            try:
                self.conn.rollback()
            except Exception:
                pass
            return False

    def close(self) -> None:
        """Close database connection."""
        if self._conn and not self._conn.closed:
            self._conn.close()


def call_lint_service(url: str, file_path: Path) -> dict[str, Any]:
    """Call lint service with file upload."""
    try:
        import mimetypes
        mime_type, _ = mimetypes.guess_type(str(file_path))
        if not mime_type:
            mime_type = "application/octet-stream"
        
        with open(file_path, "rb") as f:
            import requests
            files = {"file": (file_path.name, f, mime_type)}
            response = requests.post(url, files=files, timeout=30)
            response.raise_for_status()
            return {"status": "success", "output": response.json()}
    except requests.exceptions.RequestException as e:
        logger.error("Lint service request failed: %s", e)
        return {"status": "error", "error": str(e)}
    except Exception as e:
        logger.error("Unexpected error calling lint service: %s", e)
        return {"status": "error", "error": str(e)}


def process_message(
    message: dict[str, Any],
    s3_client: S3Client,
    pg_client: PostgresClient,
    lint_service_url: str,
    max_retries: int = 3,
) -> bool:
    """Process a single SQS message."""
    message_id = message["message_id"]
    receipt_handle = message["receipt_handle"]
    body = message["body"]
    
    # Extract S3 key from message
    s3_key = body.get("s3_key") or body.get("key") or body.get("object_key")
    if not s3_key:
        logger.error("Message %s missing s3_key", message_id)
        return False
    
    # Create temporary directory for downloads
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        dest_path = tmp_path / Path(s3_key).name
        
        # Download from S3
        if not s3_client.download(s3_key, dest_path):
            return False
        
        # Calculate file hash
        file_hash = hashlib.sha256()
        with open(dest_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                file_hash.update(chunk)
        
        logger.info("Downloaded %s (SHA256: %s)", s3_key, file_hash.hexdigest())
        
        # Call lint service
        lint_result = call_lint_service(lint_service_url, dest_path)
        
        # Write result to database
        success = pg_client.write_result(message_id, s3_key, lint_result)
        
        if success:
            logger.info("Successfully processed message %s", message_id)
        else:
            logger.error("Failed to write result for message %s", message_id)
        
        return success


def main_loop(config: dict[str, Any], run_once: bool = False) -> None:
    """Main processing loop."""
    sqs_client = SQSClient(config["sqs_queue_url"], config["region"])
    s3_client = S3Client(config["s3_bucket"], config["region"])
    pg_client = PostgresClient(
        config["db_host"],
        config["db_port"],
        config["db_name"],
        config["db_user"],
        config["db_password"],
        config["db_schema"],
    )
    
    logger.info("Starting worker (run_once=%s)", run_once)
    
    try:
        while not _SHUTDOWN:
            # Receive message
            message = sqs_client.receive(wait_seconds=config["poll_interval"])
            
            if message:
                # Process message
                success = process_message(
                    message,
                    s3_client,
                    pg_client,
                    config["lint_service_url"],
                    config["max_retries"],
                )
                
                # Delete message if processed successfully
                if success:
                    sqs_client.delete(message["receipt_handle"])
                else:
                    logger.warning("Message %s processing failed, leaving in queue", 
                                 message["message_id"])
            
            # Break if run_once mode
            if run_once:
                break
            
            # Sleep briefly if no message
            if not message and not _SHUTDOWN:
                time.sleep(1)
    
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    except Exception as e:
        logger.error("Unexpected error in main loop: %s", e)
        raise
    finally:
        pg_client.close()
        logger.info("Worker stopped")


def main(argv: list[str]) -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="SQS consumer + S3 download + lint dispatch + Postgres write"
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process a single message and exit",
    )
    
    args = parser.parse_args(argv[1:])
    
    # Setup logging
    setup_logging(args.log_level)
    
    # Setup signal handlers
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    
    try:
        # Load configuration
        config = load_config()
        logger.debug("Configuration loaded: %s", {k: v for k, v in config.items() 
                                                 if k != "db_password"})
        
        # Run main loop
        main_loop(config, run_once=args.once)
        return 0
        
    except ValueError as e:
        logger.error("Configuration error: %s", e)
        return 1
    except Exception as e:
        logger.error("Fatal error: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))