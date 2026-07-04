"""
Regression test: bin/storage_backend.py S3StorageBackend.delete
no longer silently swallows Exception during head_object check.

When head_object raises an exception (e.g., ClientError for "not found"
or network errors), the module logger must record a debug entry binding
the exception, instead of silently swallowing it.

This guards against the previous `except Exception: existed = False`
regression that masked S3 API errors and could hide credential issues,
network problems, or bucket-not-found scenarios.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Ensure repo root is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from bin.storage_backend import S3StorageBackend  # noqa: E402


@pytest.fixture
def s3_backend() -> S3StorageBackend:
    """Create an S3StorageBackend with mocked boto3 client."""
    backend = S3StorageBackend(
        bucket="test-bucket",
        region="us-east-1",
    )
    # Replace the real client with a mock
    backend.client = MagicMock()
    return backend


def test_s3_delete_logs_head_object_error(
    s3_backend: S3StorageBackend,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When head_object raises an exception, the module logger must
    record a debug entry binding the exception, instead of silently
    swallowing it."""
    # Set log level to DEBUG to capture the message
    caplog.set_level(logging.DEBUG, logger="oyster.storage")

    # Configure head_object to raise an exception (simulating S3 API error)
    s3_backend.client.head_object.side_effect = Exception("S3 head_object failed: network error")

    # Call delete - should return False (asset didn't exist) but also log the error
    result = s3_backend.delete("nonexistent-asset.tar.gz")

    # Verify the result is False (asset not found)
    assert result is False

    # Verify a debug log was emitted with the exception bound
    assert any(
        record.levelno == logging.DEBUG
        and "head_object" in record.message.lower()
        and "s3 head_object failed" in record.message.lower()
        for record in caplog.records
    ), f"Expected debug log about head_object failure. Got: {caplog.records}"


def test_s3_delete_success_when_asset_exists(
    s3_backend: S3StorageBackend,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When head_object succeeds (asset exists), delete should work normally
    and return True."""
    caplog.set_level(logging.DEBUG, logger="oyster.storage")

    # Configure head_object to succeed (return metadata)
    s3_backend.client.head_object.return_value = {"ContentLength": 1234}

    # Configure delete_object to succeed
    s3_backend.client.delete_object.return_value = {}

    result = s3_backend.delete("existing-asset.tar.gz")

    # Verify the result is True (asset existed and was deleted)
    assert result is True

    # Verify delete_object was called for both asset and metadata
    s3_backend.client.delete_object.assert_called()


def test_s3_delete_client_error_not_found(
    s3_backend: S3StorageBackend,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When head_object raises botocore.exceptions.ClientError with 404,
    the module logger must record a debug entry, and delete should return False."""
    import botocore.exceptions

    caplog.set_level(logging.DEBUG, logger="oyster.storage")

    # Configure head_object to raise ClientError (404 Not Found)
    error_response = {"Error": {"Code": "404", "Message": "Not Found"}}
    s3_backend.client.head_object.side_effect = botocore.exceptions.ClientError(
        error_response, "HeadObject"
    )

    result = s3_backend.delete("nonexistent-asset.tar.gz")

    # Verify the result is False (asset not found)
    assert result is False

    # Verify a debug log was emitted with the exception bound
    assert any(
        record.levelno == logging.DEBUG
        and "head_object" in record.message.lower()
        for record in caplog.records
    ), f"Expected debug log about head_object ClientError. Got: {caplog.records}"
