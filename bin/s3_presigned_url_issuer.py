#!/usr/bin/env python3
"""
S3 Presigned URL Issuer.

Issues 24-hour presigned S3 PUT URLs to vendors (one URL per clip).
Rate-limits per vendor key and writes upload-intent rows in Postgres for audit.

Usage:
    python bin/s3_presigned_url_issuer.py --vendor-key VENDOR_KEY --clip-id CLIP_ID
    python bin/s3_presigned_url_issuer.py --batch-file batch.csv

Environment Variables:
    AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, S3_BUCKET
    PG_HOST, PG_PORT, PG_USER, PG_PASSWORD, PG_DATABASE
    RATE_LIMIT_PER_SECOND (default: 10)
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Any

# Lazy imports for optional dependencies
boto3: Any = None
psycopg2: Any = None


def _lazy_import(name: str):
    """Lazily import an optional module."""
    global boto3, psycopg2
    if name == "boto3":
        if boto3 is None:
            import boto3
        return boto3
    if name == "psycopg2":
        if psycopg2 is None:
            import psycopg2
        return psycopg2
    raise ValueError(f"Unknown module: {name}")


def get_env(key: str, required: bool = False, default: str = "") -> str:
    """Get environment variable."""
    val = os.environ.get(key, default)
    if required and not val:
        print(f"ERROR: Required env var {key} not set", file=sys.stderr)
        sys.exit(1)
    return val


class RateLimiter:
    """Token bucket rate limiter per vendor key."""

    def __init__(self, rate_per_second: int = 10) -> None:
        self.rate = rate_per_second
        self.buckets: dict[str, dict[str, float]] = {}

    def acquire(self, vendor_key: str, tokens: int = 1) -> None:
        """Acquire tokens, blocking if needed."""
        if vendor_key not in self.buckets:
            self.buckets[vendor_key] = {"last": time.time(), "avail": float(self.rate)}
        b = self.buckets[vendor_key]
        now = time.time()
        b["avail"] = min(self.rate, b["avail"] + (now - b["last"]) * self.rate)
        b["last"] = now
        if b["avail"] < tokens:
            time.sleep((tokens - b["avail"]) / self.rate)
            b["avail"] = 0
        else:
            b["avail"] -= tokens


class S3Client:
    """S3 client for presigned URL generation."""

    def __init__(self, bucket: str, region: str = "us-east-1",
                 access_key: str = "", secret_key: str = "") -> None:
        self.bucket = bucket
        self.region = region
        self._client = None
        self._access_key = access_key
        self._secret_key = secret_key

    def _get_client(self) -> Any:
        if self._client is None:
            b = _lazy_import("boto3")
            kw = {"region_name": self.region}
            if self._access_key and self._secret_key:
                kw["aws_access_key_id"] = self._access_key
                kw["aws_secret_access_key"] = self._secret_key
            self._client = b.client("s3", **kw)
        return self._client

    def generate_presigned_put_url(self, object_key: str, expiration_hours: int = 24) -> str:
        """Generate presigned PUT URL."""
        client = self._get_client()
        exp = timedelta(hours=expiration_hours)
        return client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": object_key},
            ExpiresIn=int(exp.total_seconds()),
        )


class PostgresAuditor:
    """Postgres auditor for upload-intent rows."""

    def __init__(self, host: str, port: int, user: str, password: str, database: str) -> None:
        self.params = {"host": host, "port": port, "user": user, "password": password, "database": database}
        self._conn = None

    def _conn_get(self) -> Any:
        if self._conn is None or self._conn.closed:
            self._conn = _lazy_import("psycopg2").connect(**self.params)
        return self._conn

    def write_upload_intent(self, vendor_key: str, clip_id: str,
                            s3_key: str, url: str) -> int | None:
        """Write upload-intent row, return row ID."""
        conn = self._conn_get()
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO upload_intents (vendor_key, clip_id, s3_object_key, "
                "presigned_url, created_at, status) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                (vendor_key, clip_id, s3_key, url, datetime.utcnow(), "pending")
            )
            row_id = cur.fetchone()[0]
            conn.commit()
            return row_id
        except Exception as e:
            conn.rollback()
            raise RuntimeError(f"Failed to write upload intent: {e}") from e
        finally:
            cur.close()

    def close(self) -> None:
        """Close the database connection if open.

        Idempotent: safely does nothing if connection is already closed
        or was never opened.
        """
        if self._conn and not self._conn.closed:
            self._conn.close()


def build_s3_key(vendor_key: str, clip_id: str) -> str:
    """Build S3 object key."""
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    return f"uploads/{vendor_key}/{clip_id}/{ts}"


def process_clip(s3: S3Client, auditor: PostgresAuditor | None,
                 limiter: RateLimiter, vendor_key: str, clip_id: str) -> dict[str, Any]:
    """Process one clip: rate-limit, generate URL, write audit."""
    limiter.acquire(vendor_key)
    s3_key = build_s3_key(vendor_key, clip_id)
    url = s3.generate_presigned_put_url(s3_key)
    row_id = auditor.write_upload_intent(vendor_key, clip_id, s3_key, url) if auditor else None
    return {"vendor_key": vendor_key, "clip_id": clip_id, "s3_key": s3_key, "url": url, "id": row_id}


def read_batch(path: str) -> list[tuple[str, str]]:
    """Read batch CSV, return list of (vendor_key, clip_id)."""
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            vk = row.get("vendor_key", "").strip()
            cid = row.get("clip_id", "").strip()
            if vk and cid:
                rows.append((vk, cid))
    return rows


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    p = argparse.ArgumentParser(description="Issue 24h presigned S3 PUT URLs with Postgres audit.")
    p.add_argument("--vendor-key", help="Vendor key for single issuance")
    p.add_argument("--clip-id", help="Clip ID for single issuance")
    p.add_argument("--batch-file", help="CSV with vendor_key,clip_id columns")
    p.add_argument("--rate-limit", type=int, default=10, help="Rate limit per vendor/sec (default: 10)")
    p.add_argument("--expiration-hours", type=int, default=24, help="URL expiration hours (default: 24)")
    p.add_argument("--dry-run", action="store_true", help="Generate URLs without writing to Postgres")
    args = p.parse_args(argv)

    # Validate
    if args.batch_file and (args.vendor_key or args.clip_id):
        p.error("Cannot use --batch-file with --vendor-key or --clip-id")
    if not args.batch_file and not (args.vendor_key and args.clip_id):
        p.error("Must specify --batch-file or both --vendor-key and --clip-id")

    # Get config
    aws_key = get_env("AWS_ACCESS_KEY_ID")
    aws_secret = get_env("AWS_SECRET_ACCESS_KEY")
    aws_region = get_env("AWS_REGION", default="us-east-1")
    s3_bucket = get_env("S3_BUCKET", required=True)

    pg_host = get_env("PG_HOST", required=True)
    pg_port = int(get_env("PG_PORT", default="5432"))
    pg_user = get_env("PG_USER", required=True)
    pg_password = get_env("PG_PASSWORD", required=True)
    pg_database = get_env("PG_DATABASE", required=True)

    # Init clients
    s3 = S3Client(s3_bucket, aws_region, aws_key, aws_secret)
    auditor = PostgresAuditor(pg_host, pg_port, pg_user, pg_password, pg_database) if not args.dry_run else None
    limiter = RateLimiter(args.rate_limit)

    try:
        if args.batch_file:
            for vk, cid in read_batch(args.batch_file):
                result = process_clip(s3, auditor, limiter, vk, cid)
                print(f"Issued URL for clip {cid} (vendor: {vk})")
        else:
            result = process_clip(s3, auditor, limiter, args.vendor_key, args.clip_id)
            print(f"Presigned URL: {result['url']}")
            print(f"Upload intent ID: {result['id']}")
    finally:
        if auditor:
            auditor.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
