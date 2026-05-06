#!/usr/bin/env python3
"""
cn_vendor_mirror.py — CN-side mirror: Aliyun OSS bucket + presigned URL issuer.

Provides vendors behind the GFW with low-latency access to assets mirrored
from an upstream S3 bucket into Aliyun OSS.  Generates presigned URLs so
vendors can download without a VPN.

Usage::

    python bin/cn_vendor_mirror.py issue --object assets/report.pdf
    python bin/cn_vendor_mirror.py sync --key assets/report.pdf
    python bin/cn_vendor_mirror.py sync --prefix assets/ --recursive
    python bin/cn_vendor_mirror.py list --prefix assets/

Required env vars:
    OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET, OSS_BUCKET,
    OSS_ENDPOINT, OSS_REGION
    S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY, S3_BUCKET, S3_REGION  (for sync)
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import logging
import os
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Optional, Sequence

logger = logging.getLogger("cn_vendor_mirror")


def _env(name: str, *, default: Optional[str] = None) -> str:
    """Read an environment variable or raise / return default."""
    value = os.environ.get(name)
    if value is None:
        if default is not None:
            return default
        raise EnvironmentError(f"Required env var {name} is not set")
    return value


class OSSConfig:
    """Aliyun OSS connection parameters from environment."""
    def __init__(self) -> None:
        self.access_key_id: str = _env("OSS_ACCESS_KEY_ID")
        self.access_key_secret: str = _env("OSS_ACCESS_KEY_SECRET")
        self.bucket: str = _env("OSS_BUCKET")
        self.endpoint: str = _env("OSS_ENDPOINT")
        self.region: str = _env("OSS_REGION", default="cn-shanghai")


class S3Config:
    """Upstream S3 connection parameters from environment."""
    def __init__(self) -> None:
        self.access_key_id: str = _env("S3_ACCESS_KEY_ID")
        self.secret_access_key: str = _env("S3_SECRET_ACCESS_KEY")
        self.bucket: str = _env("S3_BUCKET")
        self.region: str = _env("S3_REGION", default="us-east-1")


# ---------------------------------------------------------------------------
# Aliyun OSS presigned URL (stdlib-only, no oss2 dependency)
# ---------------------------------------------------------------------------

def _hmac_sha1(key: bytes, msg: bytes) -> bytes:
    """Compute HMAC-SHA1 digest."""
    return hmac.new(key, msg, hashlib.sha1).digest()


def generate_presigned_url(
    cfg: OSSConfig,
    object_key: str,
    expires_seconds: int = 3600,
    http_method: str = "GET",
) -> str:
    """
    Generate an Aliyun OSS presigned URL for *object_key*.

    Parameters
    ----------
    cfg : OSSConfig
        OSS connection configuration.
    object_key : str
        The OSS object key (path within the bucket).
    expires_seconds : int
        URL validity duration in seconds (default 3600).
    http_method : str
        HTTP verb (default ``GET``).

    Returns
    -------
    str
        Fully-formed presigned URL.
    """
    expires = int(time.time()) + expires_seconds
    canonical = f"/{cfg.bucket}/{object_key}"
    string_to_sign = (
        f"{http_method}\n\n\n{expires}\n{canonical}"
    )
    signature = base64.b64encode(
        _hmac_sha1(cfg.access_key_secret.encode("utf-8"),
                    string_to_sign.encode("utf-8"))
    ).decode("utf-8")
    encoded_key = urllib.parse.quote(object_key, safe="")
    return (
        f"https://{cfg.bucket}.{cfg.endpoint}/{encoded_key}"
        f"?OSSAccessKeyId={cfg.access_key_id}"
        f"&Expires={expires}"
        f"&Signature={urllib.parse.quote(signature, safe='')}"
    )


# ---------------------------------------------------------------------------
# S3 download / OSS upload helpers (lazy import of boto3 / oss2)
# ---------------------------------------------------------------------------

def _download_from_s3(s3_cfg: S3Config, key: str, dest: Path) -> None:
    """Download a single object from S3 to *dest*."""
    import boto3  # noqa: PLC0415
    s3 = boto3.client(
        "s3",
        aws_access_key_id=s3_cfg.access_key_id,
        aws_secret_access_key=s3_cfg.secret_access_key,
        region_name=s3_cfg.region,
    )
    s3.download_file(s3_cfg.bucket, key, str(dest))
    logger.info("Downloaded s3://%s/%s → %s", s3_cfg.bucket, key, dest)


def _upload_to_oss(oss_cfg: OSSConfig, key: str, src: Path) -> None:
    """Upload a local file to Aliyun OSS."""
    import oss2  # noqa: PLC0415
    auth = oss2.Auth(oss_cfg.access_key_id, oss_cfg.access_key_secret)
    bucket = oss2.Bucket(auth, f"https://{oss_cfg.endpoint}", oss_cfg.bucket)
    with open(src, "rb") as fh:
        bucket.put_object(key, fh)
    logger.info("Uploaded %s → oss://%s/%s", src, oss_cfg.bucket, key)


# ---------------------------------------------------------------------------
# Sync logic
# ---------------------------------------------------------------------------

def sync_object(s3_cfg: S3Config, oss_cfg: OSSConfig, key: str) -> str:
    """
    Mirror a single object from S3 to OSS and return its presigned URL.

    Parameters
    ----------
    s3_cfg : S3Config
        Upstream S3 configuration.
    oss_cfg : OSSConfig
        Destination OSS configuration.
    key : str
        Object key to mirror.

    Returns
    -------
    str
        Presigned download URL for the mirrored object.
    """
    tmpdir = tempfile.mkdtemp(prefix="cn_mirror_")
    local_path = Path(tmpdir) / Path(key).name
    try:
        _download_from_s3(s3_cfg, key, local_path)
        _upload_to_oss(oss_cfg, key, local_path)
    finally:
        local_path.unlink(missing_ok=True)
        Path(tmpdir).rmdir()
    return generate_presigned_url(oss_cfg, key)


def sync_prefix(s3_cfg: S3Config, oss_cfg: OSSConfig, prefix: str) -> list[str]:
    """Mirror all objects under *prefix* from S3 to OSS. Returns presigned URLs."""
    import boto3  # noqa: PLC0415
    s3 = boto3.client(
        "s3",
        aws_access_key_id=s3_cfg.access_key_id,
        aws_secret_access_key=s3_cfg.secret_access_key,
        region_name=s3_cfg.region,
    )
    paginator = s3.get_paginator("list_objects_v2")
    urls: list[str] = []
    for page in paginator.paginate(Bucket=s3_cfg.bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            logger.info("Syncing %s", obj["Key"])
            urls.append(sync_object(s3_cfg, oss_cfg, obj["Key"]))
    return urls


# ---------------------------------------------------------------------------
# List helper
# ---------------------------------------------------------------------------

def list_objects(oss_cfg: OSSConfig, prefix: str = "") -> list[dict]:
    """
    List objects in the OSS bucket under *prefix*.

    Returns a list of dicts with keys ``Key``, ``Size``, ``LastModified``.
    """
    import oss2  # noqa: PLC0415
    auth = oss2.Auth(oss_cfg.access_key_id, oss_cfg.access_key_secret)
    bucket = oss2.Bucket(auth, f"https://{oss_cfg.endpoint}", oss_cfg.bucket)
    return [
        {"Key": o.key, "Size": o.size, "LastModified": o.last_modified}
        for o in oss2.ObjectIterator(bucket, prefix=prefix)
    ]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser."""
    parser = argparse.ArgumentParser(
        prog="cn_vendor_mirror",
        description="CN-side mirror: Aliyun OSS + presigned URL issuer",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_issue = sub.add_parser("issue", help="Generate a presigned download URL")
    p_issue.add_argument("--object", dest="object_key", required=True,
                         help="OSS object key")
    p_issue.add_argument("--expires", type=int, default=3600,
                         help="URL validity in seconds (default 3600)")
    p_issue.add_argument("--method", default="GET", choices=["GET", "HEAD"])

    p_sync = sub.add_parser("sync", help="Mirror object(s) from S3 → OSS")
    p_sync.add_argument("--key", help="Single object key to sync")
    p_sync.add_argument("--prefix", default="", help="Object prefix to sync")
    p_sync.add_argument("--recursive", action="store_true")

    p_list = sub.add_parser("list", help="List mirrored objects in OSS")
    p_list.add_argument("--prefix", default="", help="Filter by prefix")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """
    Entry-point for the CLI.

    Parameters
    ----------
    argv : sequence of str or None
        Command-line arguments (defaults to ``sys.argv[1:]``).

    Returns
    -------
    int
        Exit code (0 = success, non-zero = error).
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
    )

    try:
        oss_cfg = OSSConfig()
    except EnvironmentError as exc:
        logger.error("OSS config error: %s", exc)
        return 1

    if args.command == "issue":
        url = generate_presigned_url(
            oss_cfg, args.object_key,
            expires_seconds=args.expires, http_method=args.method,
        )
        print(url)
        return 0

    if args.command == "sync":
        try:
            s3_cfg = S3Config()
        except EnvironmentError as exc:
            logger.error("S3 config error: %s", exc)
            return 1
        if args.key:
            url = sync_object(s3_cfg, oss_cfg, args.key)
            print(f"Synced: {args.key}\nURL:    {url}")
        elif args.prefix:
            urls = sync_prefix(s3_cfg, oss_cfg, args.prefix)
            print(f"Synced {len(urls)} object(s)")
            for u in urls:
                print(u)
        else:
            parser.error("Provide --key or --prefix for sync")
        return 0

    if args.command == "list":
        objects = list_objects(oss_cfg, prefix=args.prefix)
        for obj in objects:
            print(f"{obj['Key']:<60s}  {obj['Size']:>10d}  {obj['LastModified']}")
        print(f"\nTotal: {len(objects)} object(s)")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
