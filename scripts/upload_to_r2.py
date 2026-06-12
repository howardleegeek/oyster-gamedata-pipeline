#!/usr/bin/env python3
"""Upload .exe artifacts to Cloudflare R2 bucket via boto3 (S3-compatible).

Required environment variables:
    R2_ACCESS_KEY   - R2 access key ID
    R2_SECRET       - R2 secret access key
    R2_BUCKET       - R2 bucket name
    R2_ENDPOINT     - R2 S3-compatible endpoint URL

Usage:
    python3 scripts/upload_to_r2.py --file path/to/artifact.exe
"""

import argparse
import os
import sys
from pathlib import Path

class _MissingBoto3:
    """Placeholder so the module is importable without boto3 installed.

    Supports arbitrary attribute access so that ``mock.patch`` can still
    target ``upload_to_r2.boto3.Session`` etc.  Real usage is guarded at
    call time with a clear error message.
    """

    def __getattr__(self, _name: str):
        return self


try:
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError, NoCredentialsError
except ImportError:
    boto3 = _MissingBoto3()  # type: ignore[assignment]
    Config = _MissingBoto3()  # type: ignore[assignment]
    ClientError = _MissingBoto3()  # type: ignore[assignment]
    NoCredentialsError = _MissingBoto3()  # type: ignore[assignment]


REQUIRED_ENV_VARS = [
    "R2_ACCESS_KEY",
    "R2_SECRET",
    "R2_BUCKET",
    "R2_ENDPOINT",
]


def validate_env() -> dict[str, str]:
    """Validate that all required environment variables are set.

    Returns:
        dict mapping env var names to their values.

    Raises:
        SystemExit: If any required env var is missing.
    """
    missing = [var for var in REQUIRED_ENV_VARS if not os.environ.get(var)]
    if missing:
        print(
            f"ERROR: Missing required environment variables: {', '.join(missing)}",
            file=sys.stderr,
        )
        print(
            "Set them before running, e.g.:",
            file=sys.stderr,
        )
        print(
            "  export R2_ACCESS_KEY=... R2_SECRET=... R2_BUCKET=... R2_ENDPOINT=...",
            file=sys.stderr,
        )
        sys.exit(1)
    return {var: os.environ[var] for var in REQUIRED_ENV_VARS}


def upload_to_r2(
    file_path: str,
    access_key: str,
    secret: str,
    bucket: str,
    endpoint: str,
) -> str:
    """Upload a file to an R2 bucket.

    Args:
        file_path: Local path to the file to upload.
        access_key: R2 access key ID.
        secret: R2 secret access key.
        bucket: R2 bucket name.
        endpoint: R2 S3-compatible endpoint URL.

    Returns:
        The public URL of the uploaded object.

    Raises:
        FileNotFoundError: If the local file does not exist.
        SystemExit: On upload failure.
    """
    if isinstance(boto3, _MissingBoto3):
        print("ERROR: boto3 is required. Install with: pip install boto3", file=sys.stderr)
        sys.exit(1)

    local_path = Path(file_path)
    if not local_path.is_file():
        print(f"ERROR: File not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    object_key = local_path.name

    session = boto3.Session(
        aws_access_key_id=access_key,
        aws_secret_access_key=secret,
    )

    s3_client = session.client(
        "s3",
        endpoint_url=endpoint,
        config=Config(signature_version="s3v4"),
    )

    try:
        s3_client.upload_file(
            str(local_path),
            bucket,
            object_key,
            ExtraArgs={"ContentType": "application/octet-stream"},
        )
    except NoCredentialsError:
        print("ERROR: Invalid R2 credentials.", file=sys.stderr)
        sys.exit(1)
    except ClientError as exc:
        print(f"ERROR: Upload failed: {exc}", file=sys.stderr)
        sys.exit(1)

    # Construct the public URL from the endpoint and bucket
    # R2 public URLs follow the pattern: https://<bucket>.<endpoint-host>/<key>
    # or simply: <endpoint>/<bucket>/<key> depending on endpoint style
    # We return the endpoint-based URL for consistency
    url = f"{endpoint.rstrip('/')}/{bucket}/{object_key}"
    print(f"Uploaded: {object_key} -> {url}")
    return url


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Upload .exe artifacts to Cloudflare R2",
    )
    parser.add_argument(
        "--file",
        required=True,
        help="Path to the .exe file to upload",
    )
    args = parser.parse_args()

    env = validate_env()
    upload_to_r2(
        file_path=args.file,
        access_key=env["R2_ACCESS_KEY"],
        secret=env["R2_SECRET"],
        bucket=env["R2_BUCKET"],
        endpoint=env["R2_ENDPOINT"],
    )


if __name__ == "__main__":
    main()
