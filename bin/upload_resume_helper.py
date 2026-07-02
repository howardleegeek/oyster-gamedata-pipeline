#!/usr/bin/env python3
"""
upload_resume_helper.py — Standalone resumable S3 multipart upload helper.

Maintains checkpoint state on disk so interrupted uploads can be resumed
without re-uploading already-completed parts.

Usage:
    python3 upload_resume_helper.py upload --bucket B --key K --file F
    python3 upload_resume_helper.py resume --checkpoint FILE
    python3 upload_resume_helper.py abort --bucket B --key K --upload-id U
    python3 upload_resume_helper.py list-parts --bucket B --key K --upload-id U
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_PART_SIZE = 50 * 1024 * 1024  # 50 MiB
MIN_PART_SIZE = 5 * 1024 * 1024       # 5 MiB (S3 minimum)
MAX_PARTS = 10_000
CHECKPOINT_SUFFIX = ".upload_checkpoint.json"

# Lazy boto3 import
_boto3 = None
_botocore_exceptions = None


def _import_boto3():
    """Lazily import boto3 to avoid dependency issues."""
    global _boto3, _botocore_exceptions
    if _boto3 is None:
        import boto3
        import botocore.exceptions
        _boto3 = boto3
        _botocore_exceptions = botocore.exceptions
    return _boto3, _botocore_exceptions


@dataclass
class PartInfo:
    """Metadata for a single uploaded part."""
    part_number: int
    etag: str
    size: int


@dataclass
class CheckpointState:
    """Serializable checkpoint for a multipart upload session."""
    upload_id: str
    bucket: str
    key: str
    file_path: str
    file_size: int
    part_size: int
    parts: List[PartInfo] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        d = asdict(self)
        d["parts"] = [asdict(p) for p in self.parts]
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CheckpointState":
        """Create instance from dictionary."""
        return cls(
            upload_id=d["upload_id"],
            bucket=d["bucket"],
            key=d["key"],
            file_path=d["file_path"],
            file_size=d["file_size"],
            part_size=d["part_size"],
            parts=[PartInfo(**p) for p in d.get("parts", [])],
            created_at=d.get("created_at", time.time()),
            updated_at=d.get("updated_at", time.time()),
        )

    def save(self, path: Optional[str] = None) -> str:
        """Persist checkpoint atomically. Returns path written."""
        path = path or (self.file_path + CHECKPOINT_SUFFIX)
        tmp_dir = os.path.dirname(path) or tempfile.mkdtemp()
        tmp_path = os.path.join(tmp_dir, ".tmp_" + os.path.basename(path))
        self.updated_at = time.time()
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2)
        os.replace(tmp_path, path)
        logger.info("Checkpoint saved: %s", path)
        return path

    @classmethod
    def load(cls, path: str) -> "CheckpointState":
        """Load checkpoint from file."""
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))


def compute_part_size(file_size: int, desired_size: int = DEFAULT_PART_SIZE) -> int:
    """Compute optimal part size ensuring we don't exceed MAX_PARTS."""
    if file_size <= desired_size:
        return max(MIN_PART_SIZE, file_size)
    parts_needed = (file_size + desired_size - 1) // desired_size
    if parts_needed <= MAX_PARTS:
        return desired_size
    # Increase part size to stay within MAX_PARTS
    return (file_size + MAX_PARTS - 1) // MAX_PARTS


def get_s3_client():
    """Get S3 client using environment credentials."""
    boto3, _ = _import_boto3()
    return boto3.client("s3")


def initiate_upload(bucket: str, key: str, file_path: str,
                    part_size: int) -> CheckpointState:
    """Initiate a new multipart upload and return checkpoint state."""
    s3 = get_s3_client()
    file_size = os.path.getsize(file_path)
    part_size = compute_part_size(file_size, part_size)
    
    resp = s3.create_multipart_upload(Bucket=bucket, Key=key)
    upload_id = resp["UploadId"]
    
    checkpoint = CheckpointState(
        upload_id=upload_id,
        bucket=bucket,
        key=key,
        file_path=os.path.abspath(file_path),
        file_size=file_size,
        part_size=part_size,
    )
    checkpoint.save()
    logger.info("Initiated upload %s for %s/%s", upload_id, bucket, key)
    return checkpoint


def upload_part(checkpoint: CheckpointState, part_number: int,
                data: bytes) -> PartInfo:
    """Upload a single part and return its info."""
    s3 = get_s3_client()
    resp = s3.upload_part(
        Bucket=checkpoint.bucket,
        Key=checkpoint.key,
        UploadId=checkpoint.upload_id,
        PartNumber=part_number,
        Body=data,
    )
    etag = resp["ETag"].strip('"')
    logger.debug("Uploaded part %d, etag=%s", part_number, etag)
    return PartInfo(part_number=part_number, etag=etag, size=len(data))


def complete_upload(checkpoint: CheckpointState) -> None:
    """Complete the multipart upload."""
    s3 = get_s3_client()
    parts = [{"PartNumber": p.part_number, "ETag": p.etag}
             for p in sorted(checkpoint.parts, key=lambda x: x.part_number)]
    s3.complete_multipart_upload(
        Bucket=checkpoint.bucket,
        Key=checkpoint.key,
        UploadId=checkpoint.upload_id,
        MultipartUpload={"Parts": parts},
    )
    # Clean up checkpoint file
    cp_path = checkpoint.file_path + CHECKPOINT_SUFFIX
    if os.path.exists(cp_path):
        os.remove(cp_path)
    logger.info("Upload complete: s3://%s/%s", checkpoint.bucket, checkpoint.key)


def abort_upload(bucket: str, key: str, upload_id: str) -> None:
    """Abort a multipart upload."""
    s3 = get_s3_client()
    s3.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
    logger.info("Aborted upload %s", upload_id)


def list_parts(bucket: str, key: str, upload_id: str) -> List[Dict[str, Any]]:
    """List parts of an in-progress multipart upload."""
    s3 = get_s3_client()
    parts = []
    resp = s3.list_parts(Bucket=bucket, Key=key, UploadId=upload_id)
    for p in resp.get("Parts", []):
        parts.append({
            "PartNumber": p["PartNumber"],
            "ETag": p["ETag"],
            "Size": p["Size"],
        })
    return parts


def do_upload(args) -> int:
    """Execute upload command."""
    checkpoint = initiate_upload(
        args.bucket, args.key, args.file, args.part_size
    )
    return do_resume_internal(checkpoint)


def do_resume(args) -> int:
    """Resume an interrupted upload from checkpoint."""
    checkpoint = CheckpointState.load(args.checkpoint)
    return do_resume_internal(checkpoint)


def do_resume_internal(checkpoint: CheckpointState) -> int:
    """Internal resume logic."""
    if not os.path.exists(checkpoint.file_path):
        logger.error("Source file not found: %s", checkpoint.file_path)
        return 1
    
    completed_parts = {p.part_number for p in checkpoint.parts}
    total_parts = (checkpoint.file_size + checkpoint.part_size - 1)
    total_parts = total_parts // checkpoint.part_size
    
    with open(checkpoint.file_path, "rb") as fh:
        for part_num in range(1, total_parts + 1):
            if part_num in completed_parts:
                logger.info("Skipping completed part %d/%d", part_num, total_parts)
                continue
            
            offset = (part_num - 1) * checkpoint.part_size
            fh.seek(offset)
            data = fh.read(checkpoint.part_size)
            
            logger.info("Uploading part %d/%d (%d bytes)",
                       part_num, total_parts, len(data))
            part_info = upload_part(checkpoint, part_num, data)
            checkpoint.parts.append(part_info)
            checkpoint.save()
    
    complete_upload(checkpoint)
    return 0


def do_abort(args) -> int:
    """Abort an upload."""
    abort_upload(args.bucket, args.key, args.upload_id)
    return 0


def do_list_parts(args) -> int:
    """List parts of an upload."""
    parts = list_parts(args.bucket, args.key, args.upload_id)
    for p in parts:
        print(f"Part {p['PartNumber']}: etag={p['ETag']}, size={p['Size']}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point with argparse CLI."""
    parser = argparse.ArgumentParser(
        description="Resumable S3 multipart upload helper"
    )
    parser.add_argument("-v", "--verbose", action="store_true",
                       help="Enable verbose logging")
    
    sub = parser.add_subparsers(dest="command", required=True)
    
    # upload command
    up = sub.add_parser("upload", help="Start a new multipart upload")
    up.add_argument("--bucket", required=True, help="S3 bucket name")
    up.add_argument("--key", required=True, help="S3 object key")
    up.add_argument("--file", required=True, help="Local file to upload")
    up.add_argument("--part-size", type=int, default=DEFAULT_PART_SIZE,
                   help="Part size in bytes")
    up.set_defaults(func=do_upload)
    
    # resume command
    res = sub.add_parser("resume", help="Resume from checkpoint")
    res.add_argument("--checkpoint", required=True, help="Checkpoint file path")
    res.set_defaults(func=do_resume)
    
    # abort command
    ab = sub.add_parser("abort", help="Abort an upload")
    ab.add_argument("--bucket", required=True)
    ab.add_argument("--key", required=True)
    ab.add_argument("--upload-id", required=True)
    ab.set_defaults(func=do_abort)
    
    # list-parts command
    lp = sub.add_parser("list-parts", help="List uploaded parts")
    lp.add_argument("--bucket", required=True)
    lp.add_argument("--key", required=True)
    lp.add_argument("--upload-id", required=True)
    lp.set_defaults(func=do_list_parts)
    
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s"
    )
    
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
