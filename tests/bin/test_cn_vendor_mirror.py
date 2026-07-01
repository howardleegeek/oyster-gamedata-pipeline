#!/usr/bin/env python3
"""Tests for bin/cn_vendor_mirror.py — Aliyun OSS mirror + presigned URL issuer.

Covers:
- _env: returns env value when set, default when unset, raises EnvironmentError
  when unset and no default.
- _hmac_sha1: produces the canonical HMAC-SHA1 digest (verified against a
  hand-computed reference vector).
- OSSConfig / S3Config: read required vars; honor default for optional
  OSS_REGION and S3_REGION; raise EnvironmentError when required var absent.
- generate_presigned_url: deterministic signature (monkeypatched time);
  encodes bucket, endpoint, key, access key id, expiry, signature into the
  URL; URL-encodes the object key and signature; uses the supplied http_method
  in the string-to-sign; honors non-default expires_seconds.
- _build_parser: subcommands (issue, sync, list) registered, issue requires
  --object, sync has --key/--prefix/--recursive, list has --prefix.
- main: --issue prints a presigned URL and exits 0; --list returns 0 even
  when no objects are present (mocked oss2); missing OSS env var → exit 1
  (no traceback); unknown command → exit 1; argparse error → SystemExit
  with code 2.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

# Make bin/ importable as a top-level name (mirrors sibling tests).
_BIN_DIR = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(_BIN_DIR))

import cn_vendor_mirror as m  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_oss_env(**overrides):
    """Return a complete OSS env dict; call-site merges with monkeypatch.setenv."""
    env = {
        "OSS_ACCESS_KEY_ID": "AKID",
        "OSS_ACCESS_KEY_SECRET": "SECRET",
        "OSS_BUCKET": "my-bucket",
        "OSS_ENDPOINT": "oss-cn-shanghai.aliyuncs.com",
        "OSS_REGION": "cn-shanghai",
    }
    env.update(overrides)
    return env


def _make_s3_env(**overrides):
    env = {
        "S3_ACCESS_KEY_ID": "AKIA",
        "S3_SECRET_ACCESS_KEY": "S3SECRET",
        "S3_BUCKET": "upstream",
        "S3_REGION": "us-east-1",
    }
    env.update(overrides)
    return env


# ---------------------------------------------------------------------------
# _env
# ---------------------------------------------------------------------------


class TestEnv:
    """_env reads os.environ with a default or raises EnvironmentError."""

    def test_returns_existing_value(self, monkeypatch):
        monkeypatch.setenv("OYSTER_TEST_X", "hello")
        assert m._env("OYSTER_TEST_X") == "hello"

    def test_returns_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("OYSTER_TEST_MISSING", raising=False)
        assert m._env("OYSTER_TEST_MISSING", default="fallback") == "fallback"

    def test_raises_when_required_and_unset(self, monkeypatch):
        monkeypatch.delenv("OYSTER_TEST_REQUIRED", raising=False)
        with pytest.raises(EnvironmentError) as excinfo:
            m._env("OYSTER_TEST_REQUIRED")
        assert "OYSTER_TEST_REQUIRED" in str(excinfo.value)

    def test_empty_string_is_returned_not_default(self, monkeypatch):
        """An explicitly set empty string is a valid value, distinct from unset."""
        monkeypatch.setenv("OYSTER_TEST_EMPTY", "")
        assert m._env("OYSTER_TEST_EMPTY", default="x") == ""


# ---------------------------------------------------------------------------
# _hmac_sha1
# ---------------------------------------------------------------------------


class TestHmacSha1:
    """_hmac_sha1 returns the canonical HMAC-SHA1 digest as raw bytes."""

    def test_known_vector(self):
        # Reference vector: HMAC-SHA1(key=b"key", msg=b"The quick brown fox
        # jumps over the lazy dog") = de7c9b85b8b78aa6bc8a7a36f70a90701c9db4d9
        digest = m._hmac_sha1(b"key", b"The quick brown fox jumps over the lazy dog")
        assert digest == bytes.fromhex("de7c9b85b8b78aa6bc8a7a36f70a90701c9db4d9")

    def test_empty_key(self):
        # HMAC-SHA1 with empty key has a well-defined value.
        digest = m._hmac_sha1(b"", b"abc")
        assert digest == hmac.new(b"", b"abc", hashlib.sha1).digest()

    def test_returns_bytes_not_hex(self):
        digest = m._hmac_sha1(b"k", b"m")
        assert isinstance(digest, bytes)
        assert len(digest) == 20  # SHA-1 digest size


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------


class TestOSSConfig:
    """OSSConfig reads required env vars; OSS_REGION defaults to cn-shanghai."""

    def test_loads_all_fields(self, monkeypatch):
        for k, v in _make_oss_env().items():
            monkeypatch.setenv(k, v)
        cfg = m.OSSConfig()
        assert cfg.access_key_id == "AKID"
        assert cfg.access_key_secret == "SECRET"
        assert cfg.bucket == "my-bucket"
        assert cfg.endpoint == "oss-cn-shanghai.aliyuncs.com"
        assert cfg.region == "cn-shanghai"

    def test_default_region(self, monkeypatch):
        env = _make_oss_env()
        monkeypatch.delenv("OSS_REGION", raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        cfg = m.OSSConfig()
        assert cfg.region == "cn-shanghai"

    def test_missing_access_key_raises(self, monkeypatch):
        for k, v in _make_oss_env().items():
            monkeypatch.setenv(k, v)
        monkeypatch.delenv("OSS_ACCESS_KEY_ID", raising=False)
        with pytest.raises(EnvironmentError):
            m.OSSConfig()


class TestS3Config:
    """S3Config reads required env vars; S3_REGION defaults to us-east-1."""

    def test_loads_all_fields(self, monkeypatch):
        for k, v in _make_s3_env().items():
            monkeypatch.setenv(k, v)
        cfg = m.S3Config()
        assert cfg.access_key_id == "AKIA"
        assert cfg.secret_access_key == "S3SECRET"
        assert cfg.bucket == "upstream"
        assert cfg.region == "us-east-1"

    def test_default_region(self, monkeypatch):
        env = _make_s3_env()
        monkeypatch.delenv("S3_REGION", raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        cfg = m.S3Config()
        assert cfg.region == "us-east-1"

    def test_missing_secret_raises(self, monkeypatch):
        for k, v in _make_s3_env().items():
            monkeypatch.setenv(k, v)
        monkeypatch.delenv("S3_SECRET_ACCESS_KEY", raising=False)
        with pytest.raises(EnvironmentError):
            m.S3Config()


# ---------------------------------------------------------------------------
# generate_presigned_url
# ---------------------------------------------------------------------------


class TestGeneratePresignedUrl:
    """generate_presigned_url produces deterministic, well-formed URLs."""

    def test_url_components_present(self, monkeypatch):
        for k, v in _make_oss_env().items():
            monkeypatch.setenv(k, v)
        cfg = m.OSSConfig()
        url = m.generate_presigned_url(cfg, "assets/report.pdf", expires_seconds=600)
        assert url.startswith("https://my-bucket.oss-cn-shanghai.aliyuncs.com/")
        assert "OSSAccessKeyId=AKID" in url
        assert "Signature=" in url
        assert "Expires=" in url

    def test_deterministic_signature(self, monkeypatch):
        """Same input + frozen time → identical URL."""
        for k, v in _make_oss_env().items():
            monkeypatch.setenv(k, v)
        cfg = m.OSSConfig()
        with mock.patch.object(m.time, "time", return_value=1_700_000_000):
            u1 = m.generate_presigned_url(cfg, "k1")
            u2 = m.generate_presigned_url(cfg, "k1")
        assert u1 == u2

    def test_signature_matches_hand_computed(self, monkeypatch):
        """Verify the Signature param against a hand-computed reference."""
        for k, v in _make_oss_env().items():
            monkeypatch.setenv(k, v)
        cfg = m.OSSConfig()
        with mock.patch.object(m.time, "time", return_value=1_700_000_000):
            url = m.generate_presigned_url(cfg, "key", http_method="GET")
        expires = 1_700_000_000 + 3600
        canonical = "/my-bucket/key"
        msg = f"GET\n\n\n{expires}\n{canonical}".encode()
        expected = base64.b64encode(hmac.new(b"SECRET", msg, hashlib.sha1).digest()).decode()
        # Signature is URL-quoted; compare unquoted values.
        from urllib.parse import parse_qs, urlparse

        qs = parse_qs(urlparse(url).query)
        from urllib.parse import unquote

        assert unquote(qs["Signature"][0]) == expected

    def test_different_keys_produce_different_signatures(self, monkeypatch):
        for k, v in _make_oss_env().items():
            monkeypatch.setenv(k, v)
        cfg = m.OSSConfig()
        with mock.patch.object(m.time, "time", return_value=1_700_000_000):
            u1 = m.generate_presigned_url(cfg, "keyA")
            u2 = m.generate_presigned_url(cfg, "keyB")
        assert u1 != u2

    def test_object_key_is_url_encoded(self, monkeypatch):
        """Spaces and slashes in the key must be percent-encoded."""
        for k, v in _make_oss_env().items():
            monkeypatch.setenv(k, v)
        cfg = m.OSSConfig()
        url = m.generate_presigned_url(cfg, "path with space/and/slash.pdf")
        # The path should be percent-encoded — a literal space must NOT appear
        # in the path portion of the URL.
        from urllib.parse import urlparse

        path = urlparse(url).path
        assert " " not in path
        assert "%20" in path

    def test_custom_expires_seconds(self, monkeypatch):
        for k, v in _make_oss_env().items():
            monkeypatch.setenv(k, v)
        cfg = m.OSSConfig()
        with mock.patch.object(m.time, "time", return_value=1_700_000_000):
            url_default = m.generate_presigned_url(cfg, "k")
            url_short = m.generate_presigned_url(cfg, "k", expires_seconds=10)
        # Different expiry → different URL.
        assert url_default != url_short
        # Verify expires value in URL.
        from urllib.parse import parse_qs, urlparse

        qd = parse_qs(urlparse(url_default).query)["Expires"][0]
        qs = parse_qs(urlparse(url_short).query)["Expires"][0]
        assert int(qd) == 1_700_000_000 + 3600
        assert int(qs) == 1_700_000_000 + 10

    def test_custom_http_method_in_string_to_sign(self, monkeypatch):
        """The signature must change with the HTTP method."""
        for k, v in _make_oss_env().items():
            monkeypatch.setenv(k, v)
        cfg = m.OSSConfig()
        with mock.patch.object(m.time, "time", return_value=1_700_000_000):
            u_get = m.generate_presigned_url(cfg, "k", http_method="GET")
            u_head = m.generate_presigned_url(cfg, "k", http_method="HEAD")
        assert u_get != u_head


# ---------------------------------------------------------------------------
# _build_parser
# ---------------------------------------------------------------------------


class TestBuildParser:
    """_build_parser wires subcommands and arguments correctly."""

    def test_returns_argument_parser(self):
        parser = m._build_parser()
        assert isinstance(parser, argparse.ArgumentParser)

    def test_issue_subcommand_requires_object(self):
        parser = m._build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["issue"])

    def test_issue_subcommand_parses_args(self):
        parser = m._build_parser()
        ns = parser.parse_args(
            ["issue", "--object", "foo/bar", "--expires", "120", "--method", "HEAD"]
        )
        assert ns.command == "issue"
        assert ns.object_key == "foo/bar"
        assert ns.expires == 120
        assert ns.method == "HEAD"

    def test_sync_subcommand_defaults(self):
        parser = m._build_parser()
        ns = parser.parse_args(["sync"])
        assert ns.command == "sync"
        assert ns.key is None
        assert ns.prefix == ""
        assert ns.recursive is False

    def test_sync_subcommand_with_key(self):
        parser = m._build_parser()
        ns = parser.parse_args(["sync", "--key", "k1", "--recursive"])
        assert ns.key == "k1"
        assert ns.recursive is True

    def test_list_subcommand(self):
        parser = m._build_parser()
        ns = parser.parse_args(["list", "--prefix", "assets/"])
        assert ns.command == "list"
        assert ns.prefix == "assets/"


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


class TestMain:
    """main() is the CLI entry point — covers issue, sync, list, and error paths."""

    def test_issue_command_prints_url_and_exits_0(self, monkeypatch, capsys):
        for k, v in _make_oss_env().items():
            monkeypatch.setenv(k, v)
        with mock.patch.object(m.time, "time", return_value=1_700_000_000):
            rc = m.main(["issue", "--object", "assets/x.pdf"])
        out = capsys.readouterr().out.strip()
        assert rc == 0
        assert out.startswith("https://my-bucket.oss-cn-shanghai.aliyuncs.com/")
        assert "OSSAccessKeyId=AKID" in out

    def test_missing_oss_env_returns_1(self, monkeypatch, capsys):
        # Clear all OSS_* env vars.
        for k in list(os.environ):
            if k.startswith("OSS_"):
                monkeypatch.delenv(k, raising=False)
        rc = m.main(["issue", "--object", "x"])
        assert rc == 1
        # Error is logged (stderr), no Python traceback.
        captured = capsys.readouterr()
        assert "Traceback" not in captured.out
        assert "Traceback" not in captured.err

    def test_unknown_command_exits_2(self, monkeypatch):
        """Argparse with required subparsers rejects unknown commands → SystemExit(2)."""
        for k, v in _make_oss_env().items():
            monkeypatch.setenv(k, v)
        with pytest.raises(SystemExit) as excinfo:
            m.main(["nope"])
        assert excinfo.value.code == 2

    def test_issue_method_choice_enforced(self, monkeypatch):
        """--method rejects values outside GET/HEAD."""
        for k, v in _make_oss_env().items():
            monkeypatch.setenv(k, v)
        with pytest.raises(SystemExit) as excinfo:
            m.main(["issue", "--object", "k", "--method", "POST"])
        assert excinfo.value.code == 2

    def test_sync_missing_oss_env_returns_1(self, monkeypatch):
        for k in list(os.environ):
            if k.startswith("OSS_") or k.startswith("S3_"):
                monkeypatch.delenv(k, raising=False)
        rc = m.main(["sync", "--key", "k"])
        assert rc == 1

    def test_sync_missing_s3_env_returns_1(self, monkeypatch):
        for k, v in _make_oss_env().items():
            monkeypatch.setenv(k, v)
        for k in list(os.environ):
            if k.startswith("S3_"):
                monkeypatch.delenv(k, raising=False)
        rc = m.main(["sync", "--key", "k"])
        assert rc == 1

    def test_sync_neither_key_nor_prefix_exits_2(self, monkeypatch):
        """parser.error() raises SystemExit(2) when sync has no target."""
        for k, v in _make_oss_env().items():
            monkeypatch.setenv(k, v)
        for k, v in _make_s3_env().items():
            monkeypatch.setenv(k, v)
        with pytest.raises(SystemExit) as excinfo:
            m.main(["sync"])
        assert excinfo.value.code == 2

    def test_sync_key_calls_sync_object(self, monkeypatch, capsys):
        """sync --key delegates to sync_object and prints the result."""
        for k, v in _make_oss_env().items():
            monkeypatch.setenv(k, v)
        for k, v in _make_s3_env().items():
            monkeypatch.setenv(k, v)
        with mock.patch.object(m, "sync_object", return_value="https://signed/url") as mock_sync:
            rc = m.main(["sync", "--key", "assets/k"])
        out = capsys.readouterr().out
        assert rc == 0
        mock_sync.assert_called_once()
        assert "Synced: assets/k" in out
        assert "https://signed/url" in out

    def test_sync_prefix_calls_sync_prefix(self, monkeypatch, capsys):
        for k, v in _make_oss_env().items():
            monkeypatch.setenv(k, v)
        for k, v in _make_s3_env().items():
            monkeypatch.setenv(k, v)
        with mock.patch.object(m, "sync_prefix", return_value=["u1", "u2", "u3"]):
            rc = m.main(["sync", "--prefix", "assets/", "--recursive"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "Synced 3 object(s)" in out
        assert "u1" in out and "u2" in out and "u3" in out

    def test_list_command_prints_objects(self, monkeypatch, capsys):
        for k, v in _make_oss_env().items():
            monkeypatch.setenv(k, v)
        objs = [
            {"Key": "a/1.bin", "Size": 100, "LastModified": "2026-01-01"},
            {"Key": "a/2.bin", "Size": 200, "LastModified": "2026-01-02"},
        ]
        with mock.patch.object(m, "list_objects", return_value=objs):
            rc = m.main(["list", "--prefix", "a/"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "a/1.bin" in out
        assert "a/2.bin" in out
        assert "Total: 2 object(s)" in out

    def test_list_command_empty(self, monkeypatch, capsys):
        for k, v in _make_oss_env().items():
            monkeypatch.setenv(k, v)
        with mock.patch.object(m, "list_objects", return_value=[]):
            rc = m.main(["list", "--prefix", "empty/"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "Total: 0 object(s)" in out

    def test_main_no_args_exits_2(self, monkeypatch):
        """No subcommand → argparse error → SystemExit(2)."""
        for k, v in _make_oss_env().items():
            monkeypatch.setenv(k, v)
        with pytest.raises(SystemExit) as excinfo:
            m.main([])
        assert excinfo.value.code == 2


# ---------------------------------------------------------------------------
# Smoke: __main__ guard does not break import
# ---------------------------------------------------------------------------


def test_module_imports_cleanly():
    """Importing the module must not execute the CLI."""
    import importlib

    mod = importlib.import_module("bin.cn_vendor_mirror")
    assert hasattr(mod, "main")
    assert hasattr(mod, "generate_presigned_url")
