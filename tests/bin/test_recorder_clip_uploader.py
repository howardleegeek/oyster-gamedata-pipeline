"""Tests for bin/recorder_clip_uploader.py."""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path
from unittest import mock

import pytest

from bin import recorder_clip_uploader as up


@pytest.fixture
def tarball(tmp_path):
    """Create a small dummy tarball for upload."""
    p = tmp_path / "clip-20260506-123000.tar.gz"
    p.write_bytes(b"\x1f\x8b\x08\x00" + b"X" * 1024)
    return p


@pytest.fixture
def config_file(tmp_path):
    """Create a config.json with an ingest endpoint."""
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "ingest_endpoint": "https://api.test/v1/ingest",
        "vendor_id": "vendor-test",
        "auth_token": "Bearer abc",
    }))
    return cfg_path


class TestLoadConfig:
    def test_missing_file_returns_empty(self, tmp_path):
        cfg = up.load_config(tmp_path / "nope.json")
        assert cfg.ingest_endpoint is None
        assert cfg.vendor_id is None

    def test_corrupt_json_returns_empty(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{")
        cfg = up.load_config(bad)
        assert cfg.ingest_endpoint is None

    def test_valid_config(self, config_file):
        cfg = up.load_config(config_file)
        assert cfg.ingest_endpoint == "https://api.test/v1/ingest"
        assert cfg.vendor_id == "vendor-test"
        assert cfg.auth_token == "Bearer abc"


class TestBuildMultipart:
    def test_body_contains_filename_and_vendor(self, tarball):
        body, ct = up.build_multipart_body(tarball, "vendor-x")
        assert ct.startswith("multipart/form-data; boundary=")
        text = body.decode("utf-8", errors="replace")
        assert "vendor-x" in text
        assert tarball.name in text
        assert "application/gzip" in text


class TestUploadClip:
    def test_skipped_without_endpoint(self, tarball):
        cfg = up.UploadConfig(None, None, None)
        result = up.upload_clip(tarball, config=cfg)
        assert result["status"] == "skipped"

    def test_dry_run(self, tarball):
        cfg = up.UploadConfig("https://api.test/v1/ingest", "v1", None)
        result = up.upload_clip(tarball, config=cfg, dry_run=True)
        assert result["status"] == "skipped"
        assert result["reason"] == "dry-run"
        assert result["body_bytes"] > 0

    def test_uploaded_path(self, tarball):
        cfg = up.UploadConfig("https://api.test/v1/ingest", "v1", None)

        def fake_post(endpoint, body, content_type, auth_token=None,
                       timeout=60.0):
            return {"status_code": 200, "elapsed_s": 0.1,
                    "response": {"clip_id": "abc"}}

        with mock.patch.object(up, "post_tarball", side_effect=fake_post):
            result = up.upload_clip(tarball, config=cfg)
        assert result["status"] == "uploaded"
        assert result["status_code"] == 200

    def test_http_error_path(self, tarball):
        cfg = up.UploadConfig("https://api.test/v1/ingest", "v1", None)
        err = urllib.error.HTTPError(
            "https://api.test/v1/ingest", 503, "Service Unavailable",
            hdrs=None, fp=io.BytesIO(b""))
        with mock.patch.object(up, "post_tarball", side_effect=err):
            result = up.upload_clip(tarball, config=cfg)
        assert result["status"] == "failed"
        assert result["status_code"] == 503

    def test_network_error_path(self, tarball):
        cfg = up.UploadConfig("https://api.test/v1/ingest", "v1", None)
        err = urllib.error.URLError("dns failed")
        with mock.patch.object(up, "post_tarball", side_effect=err):
            result = up.upload_clip(tarball, config=cfg)
        assert result["status"] == "failed"
        assert result["reason"] == "network"


class TestMain:
    def test_exit_1_on_missing_tarball(self, tmp_path):
        missing = tmp_path / "nope.tar.gz"
        rc = up.main(["--tarball", str(missing)])
        assert rc == 1

    def test_exit_0_on_skip(self, tarball, tmp_path):
        # Empty config → skipped → exit 0.
        empty = tmp_path / "empty.json"
        empty.write_text("{}")
        rc = up.main(["--tarball", str(tarball), "--config", str(empty)])
        assert rc == 0

    def test_exit_0_on_dry_run(self, tarball, config_file):
        rc = up.main([
            "--tarball", str(tarball),
            "--config", str(config_file),
            "--dry-run",
        ])
        assert rc == 0
