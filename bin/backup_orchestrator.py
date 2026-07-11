#!/usr/bin/env python3
"""
backup_orchestrator.py — Daily backup orchestrator for G205.

Pipeline: pg_dump → S3 upload → Glacier lifecycle (90-day transition).
Reports last-success timestamp.

Usage:
    python3 bin/backup_orchestrator.py --db-host db.example.com \
        --db-name myapp --db-user backup_user \
        --s3-bucket my-backup-bucket --s3-region us-east-1

Environment: PGPASSWORD, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("backup_orchestrator")
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

DEFAULT_STATE_DIR = Path.home() / ".g205" / "backup_state"
STATE_FILE = DEFAULT_STATE_DIR / "last_success.json"


def _configure_logging(verbose: bool = False) -> None:
    """Configure root logger with appropriate level and format."""
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logger.setLevel(level)
    logger.addHandler(handler)


def _read_state() -> Dict[str, Any]:
    """Read the last-success state file; return empty dict if missing."""
    if STATE_FILE.is_file():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Corrupt state file: %s", exc)
    return {}


def _write_state(timestamp: str, details: Dict[str, Any]) -> None:
    """Persist the last-success timestamp and metadata."""
    DEFAULT_STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump({"last_success": timestamp, "details": details}, fh, indent=2, default=str)
    logger.info("State written to %s", STATE_FILE)


def report_last_success() -> int:
    """Print the last-success timestamp and exit 0."""
    state = _read_state()
    ts = state.get("last_success")
    if ts:
        print(f"Last successful backup: {ts}")
        d = state.get("details", {})
        print(f"  Database : {d.get('db_name', 'N/A')}")
        print(f"  S3 object: {d.get('s3_key', 'N/A')}")
        print(f"  Size     : {d.get('dump_size_bytes', 'N/A')} bytes")
    else:
        print("No successful backup recorded yet.")
    return 0


def run_pg_dump(
    db_host: str, db_port: int, db_name: str, db_user: str,
    dump_path: Path, extra_args: Optional[List[str]] = None,
) -> Path:
    """Execute pg_dump writing compressed custom-format dump to *dump_path*."""
    env = os.environ.copy()
    cmd: List[str] = [
        "pg_dump", "--host", db_host, "--port", str(db_port),
        "--username", db_user, "--format", "custom",
        "--verbose", "--file", str(dump_path), db_name,
    ]
    if extra_args:
        cmd.extend(extra_args)
    logger.info("Running pg_dump: %s", " ".join(cmd))
    result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=3600)
    if result.returncode != 0:
        logger.error("pg_dump failed (rc=%d): %s", result.returncode, result.stderr)
        raise RuntimeError(f"pg_dump exited with code {result.returncode}")
    logger.info("pg_dump completed (%d bytes)", dump_path.stat().st_size)
    return dump_path


def upload_to_s3(bucket: str, key: str, local_path: Path, region: str) -> str:
    """Upload *local_path* to S3 *bucket*/*key* via AWS CLI. Returns S3 URI."""
    env = os.environ.copy()
    env["AWS_DEFAULT_REGION"] = region
    cmd: List[str] = ["aws", "s3", "cp", str(local_path), f"s3://{bucket}/{key}"]
    logger.info("Uploading to S3: %s", " ".join(cmd))
    result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=7200)
    if result.returncode != 0:
        logger.error("S3 upload failed (rc=%d): %s", result.returncode, result.stderr)
        raise RuntimeError(f"aws s3 cp exited with code {result.returncode}")
    s3_uri = f"s3://{bucket}/{key}"
    logger.info("Upload complete: %s", s3_uri)
    return s3_uri


def ensure_glacier_lifecycle(
    bucket: str, region: str, prefix: str = "backups/",
    transition_days: int = 90, expiration_days: int = 365,
) -> None:
    """Apply idempotent S3 lifecycle rule: GLACIER after *transition_days*,
    expire after *expiration_days*."""
    env = os.environ.copy()
    env["AWS_DEFAULT_REGION"] = region
    policy = {
        "Rules": [{
            "ID": "g205-backup-glacier-retention",
            "Status": "Enabled",
            "Filter": {"Prefix": prefix},
            "Transitions": [{"Days": transition_days, "StorageClass": "GLACIER"}],
            "Expiration": {"Days": expiration_days},
        }]
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
        json.dump(policy, tmp)
        tmp.flush()
        policy_path = tmp.name
    try:
        cmd: List[str] = [
            "aws", "s3api", "put-bucket-lifecycle-configuration",
            "--bucket", bucket, "--lifecycle-configuration", f"file://{policy_path}",
        ]
        logger.info("Applying lifecycle policy to bucket %s", bucket)
        result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            logger.warning("Lifecycle policy rc=%d: %s", result.returncode, result.stderr)
        else:
            logger.info(
                "Lifecycle rule set: transition=%dd, expire=%dd",
                transition_days,
                expiration_days,
            )
    finally:
        os.unlink(policy_path)


def run_backup(
    db_host: str, db_port: int, db_name: str, db_user: str,
    s3_bucket: str, s3_region: str, s3_prefix: str = "backups",
    extra_pg_args: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Execute the full backup pipeline. Returns metadata dict."""
    now = datetime.datetime.now(datetime.timezone.utc)
    date_stamp = now.strftime("%Y%m%d")
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    key = f"{s3_prefix}/{db_name}-{date_stamp}-{int(now.timestamp())}.dump"
    details: Dict[str, Any] = {
        "db_host": db_host, "db_name": db_name,
        "s3_bucket": s3_bucket, "s3_key": key, "started_at": ts,
    }
    with tempfile.TemporaryDirectory(prefix="g205_backup_") as tmpdir:
        dump_path = Path(tmpdir) / f"{db_name}.dump"
        logger.info("Step 1/3: pg_dump → %s", dump_path)
        run_pg_dump(db_host, db_port, db_name, db_user, dump_path, extra_pg_args)
        details["dump_size_bytes"] = dump_path.stat().st_size
        logger.info("Step 2/3: Upload to S3")
        details["s3_uri"] = upload_to_s3(s3_bucket, key, dump_path, s3_region)
    logger.info("Step 3/3: Ensure Glacier lifecycle rule")
    ensure_glacier_lifecycle(s3_bucket, s3_region, prefix=f"{s3_prefix}/")
    completed_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    details["completed_at"] = completed_at
    start_dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    end_dt = datetime.datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
    details["duration_seconds"] = int((end_dt - start_dt).total_seconds())
    _write_state(completed_at, details)
    logger.info("Backup completed in %ds", details["duration_seconds"])
    return details


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    p = argparse.ArgumentParser(
        prog="backup_orchestrator",
        description="Daily backup: pg_dump → S3 → Glacier.",
    )
    p.add_argument("--db-host", required=True, help="PostgreSQL host.")
    p.add_argument("--db-port", type=int, default=5432, help="PostgreSQL port (default: 5432).")
    p.add_argument("--db-name", required=True, help="Database name.")
    p.add_argument("--db-user", required=True, help="PostgreSQL user.")
    p.add_argument("--s3-bucket", required=True, help="Target S3 bucket.")
    p.add_argument("--s3-region", required=True, help="AWS region for S3 bucket.")
    p.add_argument("--s3-prefix", default="backups", help="S3 key prefix (default: 'backups').")
    p.add_argument("--extra-pg-args", nargs="*", default=None, help="Extra pg_dump arguments.")
    p.add_argument("--report", action="store_true", help="Report last-success timestamp and exit.")
    p.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging.")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point. Returns 0 on success, non-zero on failure."""
    args = build_parser().parse_args(argv)
    _configure_logging(verbose=args.verbose)
    if args.report:
        return report_last_success()
    try:
        details = run_backup(
            db_host=args.db_host, db_port=args.db_port, db_name=args.db_name,
            db_user=args.db_user, s3_bucket=args.s3_bucket, s3_region=args.s3_region,
            s3_prefix=args.s3_prefix, extra_pg_args=args.extra_pg_args,
        )
        print(f"Backup completed at {details['completed_at']}")
        print(f"  S3 URI: {details['s3_uri']}")
        print(f"  Size  : {details['dump_size_bytes']} bytes")
        return 0
    except Exception as exc:
        logger.exception("Backup pipeline failed")
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
