#!/usr/bin/env python3
"""
FastAPI endpoint for vendor tarball ingestion.

This module provides a FastAPI endpoint /v1/ingest that accepts vendor tarball
uploads, validates them via G165 lint, stores to S3, and writes a Postgres
clip row with vendor_id, duration, and sha256.

G190 Implementation - Backend Ingest Handler
"""

import argparse
import hashlib
import json
import logging
import os
import shutil
import sys
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Environment variables for configuration (no hardcoded credentials)
S3_BUCKET = os.environ.get("S3_BUCKET", "vendor-tarballs")
S3_REGION = os.environ.get("S3_REGION", "us-east-1")
S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL", None)
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ.get("DB_NAME", "vendor_db")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_SSL_MODE = os.environ.get("DB_SSL_MODE", "prefer")

# Maximum file size: 500MB
MAX_FILE_SIZE = 500 * 1024 * 1024

# G165 lint constants
G165_MAX_SINGLE_FILE_SIZE = 100 * 1024 * 1024  # 100MB per file
G165_MAX_TOTAL_SIZE = 500 * 1024 * 1024  # 500MB total


class G165LintValidator:
    """Validator for G165 lint compliance."""

    def __init__(self) -> None:
        """Initialize the G165 lint validator."""
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def validate_tarball(self, tarball_path: Path) -> Tuple[bool, str]:
        """
        Validate tarball against G165 lint rules.

        Args:
            tarball_path: Path to the tarball file.

        Returns:
            Tuple of (is_valid, error_message).
        """
        self.errors = []
        self.warnings = []

        if not tarball_path.exists():
            return False, f"Tarball not found: {tarball_path}"

        if tarball_path.stat().st_size == 0:
            return False, "Tarball is empty"

        if tarball_path.stat().st_size > G165_MAX_TOTAL_SIZE:
            return False, f"Tarball exceeds max size ({G165_MAX_TOTAL_SIZE} bytes)"

        try:
            with tarfile.open(tarball_path, "r:*") as tar:
                members = tar.getnames()
                if not members:
                    return False, "Tarball contains no files"

                # Check for manifest file
                manifest_found = any(
                    "manifest" in name.lower() and name.endswith(".json")
                    for name in members
                )
                if not manifest_found:
                    self.errors.append("No manifest.json file found")

                # Validate individual file sizes and check for unsafe paths
                for member in tar.getmembers():
                    if member.size > G165_MAX_SINGLE_FILE_SIZE:
                        self.errors.append(
                            f"File {member.name} exceeds {G165_MAX_SINGLE_FILE_SIZE} bytes"
                        )
                    if member.name.startswith("/") or ".." in member.name:
                        self.errors.append(f"Unsafe path in tarball: {member.name}")

        except tarfile.TarError as e:
            return False, f"Invalid tarball format: {e}"

        is_valid = len(self.errors) == 0
        error_msg = "; ".join(self.errors) if self.errors else "Validation passed"
        return is_valid, error_msg

    def extract_duration(self, tarball_path: Path, extract_dir: Path) -> Optional[float]:
        """
        Extract duration from tarball manifest.

        Args:
            tarball_path: Path to the tarball file.
            extract_dir: Directory to extract files to.

        Returns:
            Duration in seconds, or None if not found.
        """
        try:
            with tarfile.open(tarball_path, "r:*") as tar:
                for member in tar.getmembers():
                    if "manifest" in member.name.lower() and member.name.endswith(".json"):
                        tar.extract(member, extract_dir)
                        manifest_path = extract_dir / member.name
                        with open(manifest_path, "r") as f:
                            manifest = json.load(f)
                        return float(manifest.get("duration", 0))
        except (json.JSONDecodeError, KeyError, ValueError, tarfile.TarError) as e:
            logger.warning(f"Could not extract duration: {e}")
        return None


def compute_sha256(file_path: Path) -> str:
    """
    Compute SHA256 hash of a file.

    Args:
        file_path: Path to the file.

    Returns:
        Hexadecimal SHA256 hash string.
    """
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()


def get_s3_client() -> Any:
    """
    Get S3 client using environment configuration.

    Returns:
        Boto3 S3 client instance.
    """
    import boto3
    from botocore.config import Config

    config = Config(region_name=S3_REGION, retries={"max_attempts": 3, "mode": "standard"})
    client_kwargs: Dict[str, Any] = {"config": config}
    if S3_ENDPOINT_URL:
        client_kwargs["endpoint_url"] = S3_ENDPOINT_URL
    return boto3.client("s3", **client_kwargs)


def upload_to_s3(file_path: Path, object_key: str) -> Tuple[bool, str]:
    """
    Upload file to S3.

    Args:
        file_path: Path to the file to upload.
        object_key: S3 object key.

    Returns:
        Tuple of (success, error_message_or_s3_uri).
    """
    try:
        s3_client = get_s3_client()
        s3_client.upload_file(str(file_path), S3_BUCKET, object_key)
        s3_uri = f"s3://{S3_BUCKET}/{object_key}"
        logger.info(f"Uploaded to {s3_uri}")
        return True, s3_uri
    except Exception as e:
        logger.error(f"S3 upload failed: {e}")
        return False, str(e)


def get_db_connection() -> Any:
    """
    Get PostgreSQL database connection.

    Returns:
        Database connection object.
    """
    import psycopg2
    from psycopg2.extras import RealDictCursor

    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER,
        password=DB_PASSWORD, sslmode=DB_SSL_MODE, cursor_factory=RealDictCursor
    )


def write_clip_row(
    vendor_id: str, duration: float, sha256: str, s3_uri: str,
    metadata: Optional[Dict[str, Any]] = None
) -> Tuple[bool, str]:
    """
    Write clip row to Postgres database.

    Args:
        vendor_id: Vendor identifier.
        duration: Clip duration in seconds.
        sha256: SHA256 hash of the tarball.
        s3_uri: S3 URI where tarball is stored.
        metadata: Optional additional metadata.

    Returns:
        Tuple of (success, error_message_or_clip_id).
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        query = """
            INSERT INTO clips (vendor_id, duration, sha256, s3_uri, metadata, created_at)
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
        """
        cursor.execute(
            query,
            (vendor_id, duration, sha256, s3_uri, json.dumps(metadata or {}), datetime.utcnow())
        )
        result = cursor.fetchone()
        conn.commit()
        cursor.close()
        conn.close()
        clip_id = result["id"]
        logger.info(f"Created clip row with id={clip_id}")
        return True, str(clip_id)
    except Exception as e:
        logger.error(f"Database write failed: {e}")
        return False, str(e)


def process_tarball(tarball_path: Path, vendor_id: str, cleanup: bool = True) -> Dict[str, Any]:
    """
    Process a vendor tarball: validate, upload to S3, write DB row.

    Args:
        tarball_path: Path to the tarball file.
        vendor_id: Vendor identifier.
        cleanup: Whether to cleanup temp files.

    Returns:
        Dictionary with processing results.
    """
    result: Dict[str, Any] = {
        "success": False, "vendor_id": vendor_id, "sha256": None,
        "duration": None, "s3_uri": None, "clip_id": None, "error": None
    }

    extract_dir = None
    try:
        extract_dir = Path(tempfile.mkdtemp(prefix="ingest_"))

        # Compute SHA256
        sha256 = compute_sha256(tarball_path)
        result["sha256"] = sha256

        # Validate with G165 lint
        validator = G165LintValidator()
        is_valid, error_msg = validator.validate_tarball(tarball_path)
        if not is_valid:
            result["error"] = f"G165 validation failed: {error_msg}"
            return result

        # Extract duration from manifest
        duration = validator.extract_duration(tarball_path, extract_dir)
        result["duration"] = duration or 0.0

        # Generate S3 object key
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        object_key = f"vendor_tarballs/{vendor_id}/{timestamp}_{tarball_path.name}"

        # Upload to S3
        upload_success, upload_result = upload_to_s3(tarball_path, object_key)
        if not upload_success:
            result["error"] = f"S3 upload failed: {upload_result}"
            return result
        result["s3_uri"] = upload_result

        # Write to database
        db_success, db_result = write_clip_row(
            vendor_id=vendor_id, duration=result["duration"],
            sha256=sha256, s3_uri=upload_result,
            metadata={"original_filename": tarball_path.name}
        )
        if not db_success:
            result["error"] = f"Database write failed: {db_result}"
            return result
        result["clip_id"] = db_result

        result["success"] = True
        logger.info(f"Successfully processed tarball for vendor {vendor_id}")

    except Exception as e:
        result["error"] = f"Processing error: {e}"
        logger.error(f"Error processing tarball: {e}")

    finally:
        if cleanup and extract_dir and extract_dir.exists():
            shutil.rmtree(extract_dir, ignore_errors=True)

    return result


def create_app() -> Any:
    """
    Create and configure FastAPI application.

    Returns:
        Configured FastAPI app instance.
    """
    from fastapi import FastAPI, File, HTTPException, UploadFile
    from pydantic import BaseModel

    class IngestResponse(BaseModel):
        """Response model for ingest endpoint."""
        success: bool
        vendor_id: str
        sha256: Optional[str] = None
        duration: Optional[float] = None
        s3_uri: Optional[str] = None
        clip_id: Optional[str] = None
        error: Optional[str] = None

    app = FastAPI(
        title="Vendor Tarball Ingest Handler",
        description="Endpoint for uploading and processing vendor tarballs",
        version="1.0.0"
    )

    @app.post("/v1/ingest", response_model=IngestResponse)
    async def ingest_tarball(vendor_id: str, file: UploadFile = File(...)) -> IngestResponse:
        """
        Accept vendor tarball upload, validate, store to S3, write DB row.

        Args:
            vendor_id: Vendor identifier string.
            file: Uploaded tarball file.

        Returns:
            IngestResponse with processing results.

        Raises:
            HTTPException: On validation or processing errors.
        """
        if file.size and file.size > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail=f"File too large. Max: {MAX_FILE_SIZE}")

        temp_dir = Path(tempfile.mkdtemp(prefix="upload_"))
        temp_file = temp_dir / (file.filename or "upload.tar.gz")

        try:
            content = await file.read()
            with open(temp_file, "wb") as f:
                f.write(content)

            if temp_file.stat().st_size > MAX_FILE_SIZE:
                raise HTTPException(status_code=413, detail=f"File too large. Max: {MAX_FILE_SIZE}")

            result = process_tarball(temp_file, vendor_id)
            return IngestResponse(**result)

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Ingest error: {e}")
            raise HTTPException(status_code=500, detail=str(e)) from None
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    @app.get("/health")
    async def health_check() -> Dict[str, str]:
        """Health check endpoint."""
        return {"status": "healthy"}

    return app


def main(argv: Optional[list[str]] = None) -> int:
    """
    Main entry point with argparse CLI.

    Args:
        argv: Command line arguments (defaults to sys.argv[1:]).

    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    parser = argparse.ArgumentParser(description="Vendor tarball ingest handler")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Process command
    process_parser = subparsers.add_parser("process", help="Process a tarball")
    process_parser.add_argument("--file", "-f", required=True, help="Path to tarball file")
    process_parser.add_argument("--vendor-id", "-v", required=True, help="Vendor identifier")

    # Serve command
    serve_parser = subparsers.add_parser("serve", help="Start FastAPI server")
    serve_parser.add_argument("--host", default="0.0.0.0", help="Server host")
    serve_parser.add_argument("--port", type=int, default=8000, help="Server port")

    # Validate command
    validate_parser = subparsers.add_parser("validate", help="Validate a tarball")
    validate_parser.add_argument("--file", "-f", required=True, help="Path to tarball file")

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 1

    if args.command == "validate":
        tarball_path = Path(args.file)
        if not tarball_path.exists():
            print(f"Error: File not found: {tarball_path}", file=sys.stderr)
            return 1
        validator = G165LintValidator()
        is_valid, message = validator.validate_tarball(tarball_path)
        if is_valid:
            print(f"✓ Validation passed: {tarball_path}")
            return 0
        else:
            print(f"✗ Validation failed: {message}", file=sys.stderr)
            return 1

    elif args.command == "process":
        tarball_path = Path(args.file)
        if not tarball_path.exists():
            print(f"Error: File not found: {tarball_path}", file=sys.stderr)
            return 1
        result = process_tarball(tarball_path, args.vendor_id)
        if result["success"]:
            print("✓ Successfully processed tarball")
            print(f"  Vendor ID: {result['vendor_id']}")
            print(f"  SHA256: {result['sha256']}")
            print(f"  Duration: {result['duration']}s")
            print(f"  S3 URI: {result['s3_uri']}")
            print(f"  Clip ID: {result['clip_id']}")
            return 0
        else:
            print(f"✗ Processing failed: {result['error']}", file=sys.stderr)
            return 1

    elif args.command == "serve":
        import uvicorn
        print(f"Starting server on {args.host}:{args.port}")
        app = create_app()
        uvicorn.run(app, host=args.host, port=args.port)
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
