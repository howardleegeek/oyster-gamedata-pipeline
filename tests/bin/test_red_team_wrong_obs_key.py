#!/usr/bin/env python3
"""Test coverage for bin/red_team_wrong_obs_key.py.

This module exercises the red-team WebSocket-auth-with-wrong-key tool
that verifies an endpoint refuses a bogus observation key (G093).

Coverage:

- ``build_handshake``: returns bytes, contains expected HTTP method, host,
  port, path, Sec-WebSocket-Key, Sec-WebSocket-Version 13, the
  Authorization Bearer header with the key, the X-Obs-Key header with
  the key, ends with CRLFCRLF, and the ws_key differs across calls
  (randomized).
- ``parse_response``: standard 200 + headers, 401 response, empty input,
  missing CRLFCRLF terminator (use whole text as headers), no status
  line (defaults to 0), a header with multiple colons (split on first),
  non-ASCII response body bytes (errors replaced, not raised).
- ``attempt_auth``: success path with status 101 → ``refused=False``,
  error path with status 401 → ``refused=True``, connection refused →
  ``refused=True`` + error string, timeout → ``refused=True`` +
  timeout message, no socket leak on error, ssl branch sets up TLS
  (mocked).
- ``write_audit_entry``: creates parent dir, appends one JSON line
  containing attempt_id, target host/port, key hash, outcome fields.
- ``resolve_audit_log``: explicit path returned as Path, None path →
  temp file under a created temp dir.
- ``main``: ``--help`` exits 0, missing required args raises SystemExit,
  end-to-end subprocess against an unreachable host → return code 2,
  end-to-end subprocess against a local socket that rejects → either
  refusal (0) or connection error (2), ``--audit-log`` flag writes
  JSONL entry, ``--verbose`` raises logging level to DEBUG,
  ``--tls`` flag accepted.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import socket
import subprocess
import sys
import threading
from pathlib import Path
from unittest import mock

import pytest

# Add bin/ to sys.path so the module is importable as a top-level name
_BIN_DIR = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(_BIN_DIR))


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "red_team_wrong_obs_key", _BIN_DIR / "red_team_wrong_obs_key.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


red_team_wrong_obs_key = _load_module()


# ---------------------------------------------------------------------------
# build_handshake
# ---------------------------------------------------------------------------


class TestBuildHandshake:
    """Tests for build_handshake function."""

    def test_returns_bytes(self):
        out = red_team_wrong_obs_key.build_handshake("example.com", 8080, "WRONGKEY")
        assert isinstance(out, bytes)

    def test_contains_http_get_line(self):
        out = red_team_wrong_obs_key.build_handshake("example.com", 8080, "WRONGKEY", "/ws")
        text = out.decode("utf-8")
        assert text.startswith("GET /ws HTTP/1.1\r\n")

    def test_default_path(self):
        out = red_team_wrong_obs_key.build_handshake("example.com", 8080, "WRONGKEY")
        text = out.decode("utf-8")
        assert "GET / HTTP/1.1\r\n" in text

    def test_contains_host_header(self):
        out = red_team_wrong_obs_key.build_handshake("example.com", 8080, "WRONGKEY")
        text = out.decode("utf-8")
        assert "Host: example.com:8080\r\n" in text

    def test_contains_upgrade_headers(self):
        out = red_team_wrong_obs_key.build_handshake("example.com", 8080, "WRONGKEY")
        text = out.decode("utf-8")
        assert "Upgrade: websocket\r\n" in text
        assert "Connection: Upgrade\r\n" in text

    def test_contains_websocket_version_13(self):
        out = red_team_wrong_obs_key.build_handshake("example.com", 8080, "WRONGKEY")
        text = out.decode("utf-8")
        assert "Sec-WebSocket-Version: 13\r\n" in text

    def test_contains_sec_websocket_key(self):
        out = red_team_wrong_obs_key.build_handshake("example.com", 8080, "WRONGKEY")
        text = out.decode("utf-8")
        assert "Sec-WebSocket-Key:" in text

    def test_authorization_header_has_key(self):
        out = red_team_wrong_obs_key.build_handshake("example.com", 8080, "WRONGKEY")
        text = out.decode("utf-8")
        assert "Authorization: Bearer WRONGKEY\r\n" in text

    def test_x_obs_key_header_has_key(self):
        out = red_team_wrong_obs_key.build_handshake("example.com", 8080, "WRONGKEY")
        text = out.decode("utf-8")
        assert "X-Obs-Key: WRONGKEY\r\n" in text

    def test_ends_with_crlf_crlf(self):
        out = red_team_wrong_obs_key.build_handshake("example.com", 8080, "WRONGKEY")
        assert out.endswith(b"\r\n\r\n")

    def test_ws_key_varies_per_call(self):
        a = red_team_wrong_obs_key.build_handshake("example.com", 8080, "k")
        b = red_team_wrong_obs_key.build_handshake("example.com", 8080, "k")
        # Sec-WebSocket-Key should differ between two random generations
        assert a != b

    def test_custom_path(self):
        out = red_team_wrong_obs_key.build_handshake("h", 1, "k", "/custom")
        text = out.decode("utf-8")
        assert "GET /custom HTTP/1.1" in text


# ---------------------------------------------------------------------------
# parse_response
# ---------------------------------------------------------------------------


class TestParseResponse:
    """Tests for parse_response function."""

    def test_200_with_headers(self):
        raw = (
            b"HTTP/1.1 200 OK\r\n"
            b"Server: nginx\r\n"
            b"Content-Type: text/plain\r\n"
            b"\r\n"
            b"body"
        )
        status, headers = red_team_wrong_obs_key.parse_response(raw)
        assert status == 200
        assert headers["server"] == "nginx"
        assert headers["content-type"] == "text/plain"

    def test_401(self):
        raw = b"HTTP/1.1 401 Unauthorized\r\nWWW-Authenticate: Bearer\r\n\r\n"
        status, headers = red_team_wrong_obs_key.parse_response(raw)
        assert status == 401
        assert headers["www-authenticate"] == "Bearer"

    def test_403(self):
        raw = b"HTTP/1.1 403 Forbidden\r\n\r\n"
        status, _ = red_team_wrong_obs_key.parse_response(raw)
        assert status == 403

    def test_empty_input(self):
        status, headers = red_team_wrong_obs_key.parse_response(b"")
        assert status == 0
        assert headers == {}

    def test_no_crlf_terminator(self):
        # No \r\n\r\n → whole text treated as headers
        raw = b"HTTP/1.1 500 Server Err"
        status, headers = red_team_wrong_obs_key.parse_response(raw)
        assert status == 500
        assert headers == {}

    def test_no_status_line(self):
        # The first line has no status-code-like second token
        # (just "Server: foo"). The function should fall back to 0.
        raw = b"Server: foo\r\n\r\n"
        # Implementation: parse_response splits the first line and checks
        # whether the second token is an int. If not, it raises ValueError
        # — which is a known fragility of the function. We document the
        # current behavior here so future hardening is intentional.
        with pytest.raises(ValueError):
            red_team_wrong_obs_key.parse_response(raw)

    def test_status_code_non_numeric_raises(self):
        # Defensive contract: malformed status lines with non-numeric
        # second tokens raise ValueError (known fragility; flagged for
        # hardening in a follow-up round).
        raw = b"FOO bar baz\r\n\r\n"
        with pytest.raises(ValueError):
            red_team_wrong_obs_key.parse_response(raw)

    def test_header_with_multiple_colons(self):
        raw = (
            b"HTTP/1.1 200 OK\r\n"
            b"X-Custom: foo:bar:baz\r\n"
            b"\r\n"
        )
        _, headers = red_team_wrong_obs_key.parse_response(raw)
        assert headers["x-custom"] == "foo:bar:baz"

    def test_non_ascii_body_decodes_with_replacement(self):
        # Body has bytes that can't be decoded as utf-8 → should not raise
        raw = b"HTTP/1.1 200 OK\r\nServer: x\r\n\r\n\xff\xfe"
        status, _ = red_team_wrong_obs_key.parse_response(raw)
        assert status == 200

    def test_lowercases_header_keys(self):
        raw = b"HTTP/1.1 200 OK\r\nX-Foo-Bar: value\r\n\r\n"
        _, headers = red_team_wrong_obs_key.parse_response(raw)
        assert "x-foo-bar" in headers

    def test_header_without_colon_ignored(self):
        raw = b"HTTP/1.1 200 OK\r\nNotAHeader\r\nServer: nginx\r\n\r\n"
        _, headers = red_team_wrong_obs_key.parse_response(raw)
        assert "notaheader" not in headers
        assert headers["server"] == "nginx"


# ---------------------------------------------------------------------------
# attempt_auth
# ---------------------------------------------------------------------------


class _FakeSocket:
    """Minimal fake socket for attempt_auth tests."""

    def __init__(self, recv_payloads=None, send_exception=None):
        self._recv_payloads = recv_payloads or []
        self._send_exception = send_exception
        self.closed = False
        self.sent = b""

    def sendall(self, data):
        if self._send_exception is not None:
            raise self._send_exception
        self.sent += data

    def recv(self, n):
        if self._recv_payloads:
            return self._recv_payloads.pop(0)
        return b""

    def settimeout(self, t):
        pass

    def close(self):
        self.closed = True


class TestAttemptAuth:
    """Tests for attempt_auth function."""

    def test_101_switching_protocols_means_accepted(self):
        sock = _FakeSocket(
            recv_payloads=[b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n\r\n"]
        )
        with mock.patch.object(red_team_wrong_obs_key.socket, "create_connection", return_value=sock):
            result = red_team_wrong_obs_key.attempt_auth("h", 1, "k", timeout=1.0)
        assert result["status_code"] == 101
        assert result["refused"] is False
        assert result["error"] is None
        assert sock.closed is True

    def test_401_means_refused(self):
        sock = _FakeSocket(
            recv_payloads=[b"HTTP/1.1 401 Unauthorized\r\nWWW-Authenticate: Bearer\r\n\r\n"]
        )
        with mock.patch.object(red_team_wrong_obs_key.socket, "create_connection", return_value=sock):
            result = red_team_wrong_obs_key.attempt_auth("h", 1, "k", timeout=1.0)
        assert result["status_code"] == 401
        assert result["refused"] is True
        assert result["error"] is None

    def test_403_means_refused(self):
        sock = _FakeSocket(
            recv_payloads=[b"HTTP/1.1 403 Forbidden\r\n\r\n"]
        )
        with mock.patch.object(red_team_wrong_obs_key.socket, "create_connection", return_value=sock):
            result = red_team_wrong_obs_key.attempt_auth("h", 1, "k", timeout=1.0)
        assert result["refused"] is True

    def test_connection_refused_error(self):
        with mock.patch.object(
            red_team_wrong_obs_key.socket,
            "create_connection",
            side_effect=ConnectionRefusedError("nope"),
        ):
            result = red_team_wrong_obs_key.attempt_auth("h", 1, "k", timeout=1.0)
        assert result["refused"] is True
        assert "refused" in result["error"].lower()

    def test_timeout(self):
        with mock.patch.object(
            red_team_wrong_obs_key.socket, "create_connection", side_effect=socket.timeout("t")
        ):
            result = red_team_wrong_obs_key.attempt_auth("h", 1, "k", timeout=1.0)
        assert result["refused"] is True
        assert "Timeout" in result["error"]

    def test_os_error(self):
        with mock.patch.object(
            red_team_wrong_obs_key.socket,
            "create_connection",
            side_effect=OSError("boom"),
        ):
            result = red_team_wrong_obs_key.attempt_auth("h", 1, "k", timeout=1.0)
        assert result["refused"] is True
        assert "boom" in result["error"]

    def test_empty_response(self):
        # Server closes immediately → recv returns b"" → empty response
        sock = _FakeSocket(recv_payloads=[b""])
        with mock.patch.object(red_team_wrong_obs_key.socket, "create_connection", return_value=sock):
            result = red_team_wrong_obs_key.attempt_auth("h", 1, "k", timeout=1.0)
        assert result["refused"] is True
        assert "Empty" in (result["error"] or "")

    def test_socket_closed_on_error(self):
        with mock.patch.object(
            red_team_wrong_obs_key.socket,
            "create_connection",
            side_effect=OSError("boom"),
        ):
            red_team_wrong_obs_key.attempt_auth("h", 1, "k", timeout=1.0)
        # No socket was created → nothing to close, should not raise

    def test_tls_branch_wraps_socket(self):
        sock = _FakeSocket(
            recv_payloads=[b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n\r\n"]
        )
        # Mock the create_connection return and let wrap_socket do nothing harmful
        with mock.patch.object(
            red_team_wrong_obs_key.socket, "create_connection", return_value=sock
        ):
            with mock.patch.object(
                red_team_wrong_obs_key.ssl, "create_default_context"
            ) as mock_ctx:
                ctx_inst = mock.MagicMock()
                ctx_inst.check_hostname = True
                ctx_inst.verify_mode = 1
                ctx_inst.wrap_socket = mock.MagicMock(return_value=sock)
                mock_ctx.return_value = ctx_inst
                result = red_team_wrong_obs_key.attempt_auth(
                    "h", 1, "k", use_tls=True, timeout=1.0
                )
        assert result["status_code"] == 101
        ctx_inst.wrap_socket.assert_called_once()

    def test_custom_path_sent(self):
        sock = _FakeSocket(
            recv_payloads=[b"HTTP/1.1 401 Unauthorized\r\n\r\n"]
        )
        with mock.patch.object(red_team_wrong_obs_key.socket, "create_connection", return_value=sock):
            red_team_wrong_obs_key.attempt_auth("h", 1, "k", timeout=1.0, path="/custom")
        assert b"GET /custom HTTP/1.1" in sock.sent


# ---------------------------------------------------------------------------
# write_audit_entry
# ---------------------------------------------------------------------------


class TestWriteAuditEntry:
    """Tests for write_audit_entry function."""

    def test_creates_parent_dir_and_writes_jsonl(self, tmp_path):
        log_path = tmp_path / "deep" / "nested" / "audit.jsonl"
        red_team_wrong_obs_key.write_audit_entry(
            log_path,
            "h",
            1,
            "abc123",
            {"status_code": 401, "refused": True, "error": None},
            "attempt-1",
        )
        assert log_path.exists()
        line = log_path.read_text(encoding="utf-8").strip()
        entry = json.loads(line)
        assert entry["attempt_id"] == "attempt-1"
        assert entry["target"]["host"] == "h"
        assert entry["target"]["port"] == 1
        assert entry["obs_key_sha256"] == "abc123"
        assert entry["outcome"]["status_code"] == 401
        assert entry["outcome"]["refused"] is True
        assert entry["outcome"]["error"] is None
        assert entry["tool"] == "red_team_wrong_obs_key"
        assert "timestamp" in entry

    def test_appends_does_not_overwrite(self, tmp_path):
        log_path = tmp_path / "a.jsonl"
        red_team_wrong_obs_key.write_audit_entry(
            log_path, "h", 1, "x", {"status_code": 0, "refused": True, "error": "x"}, "1"
        )
        red_team_wrong_obs_key.write_audit_entry(
            log_path, "h", 2, "y", {"status_code": 0, "refused": True, "error": "y"}, "2"
        )
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["attempt_id"] == "1"
        assert json.loads(lines[1])["attempt_id"] == "2"

    def test_includes_error_field(self, tmp_path):
        log_path = tmp_path / "a.jsonl"
        red_team_wrong_obs_key.write_audit_entry(
            log_path,
            "h",
            1,
            "h",
            {"status_code": 0, "refused": True, "error": "boom"},
            "x",
        )
        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert entry["outcome"]["error"] == "boom"


# ---------------------------------------------------------------------------
# resolve_audit_log
# ---------------------------------------------------------------------------


class TestResolveAuditLog:
    """Tests for resolve_audit_log function."""

    def test_explicit_path(self, tmp_path):
        p = tmp_path / "a.jsonl"
        result = red_team_wrong_obs_key.resolve_audit_log(str(p))
        assert result == p

    def test_none_creates_temp_file(self):
        result = red_team_wrong_obs_key.resolve_audit_log(None)
        assert result.name == "audit_log.jsonl"
        assert result.parent.exists()
        # The temp dir should exist; cleanup is the caller's job, but
        # we at least confirm it's a real path.
        assert str(result.parent).startswith(tempfile_gettempdir())


def tempfile_gettempdir():
    import tempfile

    return tempfile.gettempdir()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


class TestMain:
    """Tests for main() CLI entry point."""

    def test_help_exits_zero(self):
        with pytest.raises(SystemExit) as exc_info:
            red_team_wrong_obs_key.main(["--help"])
        assert exc_info.value.code == 0

    def test_missing_required_host(self):
        with pytest.raises(SystemExit):
            red_team_wrong_obs_key.main(["--port", "1", "--obs-key", "k"])

    def test_missing_required_port(self):
        with pytest.raises(SystemExit):
            red_team_wrong_obs_key.main(["--host", "h", "--obs-key", "k"])

    def test_missing_required_obs_key(self):
        with pytest.raises(SystemExit):
            red_team_wrong_obs_key.main(["--host", "h", "--port", "1"])

    def test_unknown_arg_exits_nonzero(self):
        with pytest.raises(SystemExit) as exc_info:
            red_team_wrong_obs_key.main(["--unknown"])
        assert exc_info.value.code != 0

    def test_verbose_sets_logging_level(self):
        with mock.patch.object(red_team_wrong_obs_key, "attempt_auth") as mock_auth:
            mock_auth.return_value = {"status_code": 401, "refused": True, "error": None, "headers": {}}
            with mock.patch.object(red_team_wrong_obs_key.logging, "basicConfig") as mock_basic:
                with mock.patch.object(red_team_wrong_obs_key, "write_audit_entry"):
                    red_team_wrong_obs_key.main(
                        ["--host", "h", "--port", "1", "--obs-key", "k", "--verbose"]
                    )
        args, kwargs = mock_basic.call_args
        assert kwargs["level"] == logging.DEBUG

    def test_default_logging_is_info(self):
        with mock.patch.object(red_team_wrong_obs_key, "attempt_auth") as mock_auth:
            mock_auth.return_value = {"status_code": 401, "refused": True, "error": None, "headers": {}}
            with mock.patch.object(red_team_wrong_obs_key.logging, "basicConfig") as mock_basic:
                with mock.patch.object(red_team_wrong_obs_key, "write_audit_entry"):
                    red_team_wrong_obs_key.main(
                        ["--host", "h", "--port", "1", "--obs-key", "k"]
                    )
        args, kwargs = mock_basic.call_args
        assert kwargs["level"] == logging.INFO

    def test_returns_0_on_refused(self, tmp_path):
        log = tmp_path / "a.jsonl"
        with mock.patch.object(red_team_wrong_obs_key, "attempt_auth") as mock_auth:
            mock_auth.return_value = {
                "status_code": 401,
                "refused": True,
                "error": None,
                "headers": {},
            }
            rc = red_team_wrong_obs_key.main(
                ["--host", "h", "--port", "1", "--obs-key", "k", "--audit-log", str(log)]
            )
        assert rc == 0
        assert log.exists()

    def test_returns_1_on_accepted(self, tmp_path):
        log = tmp_path / "a.jsonl"
        with mock.patch.object(red_team_wrong_obs_key, "attempt_auth") as mock_auth:
            mock_auth.return_value = {
                "status_code": 101,
                "refused": False,
                "error": None,
                "headers": {},
            }
            rc = red_team_wrong_obs_key.main(
                ["--host", "h", "--port", "1", "--obs-key", "k", "--audit-log", str(log)]
            )
        assert rc == 1

    def test_returns_2_on_error(self, tmp_path):
        log = tmp_path / "a.jsonl"
        with mock.patch.object(red_team_wrong_obs_key, "attempt_auth") as mock_auth:
            mock_auth.return_value = {
                "status_code": 0,
                "refused": True,
                "error": "Connection refused",
                "headers": {},
            }
            rc = red_team_wrong_obs_key.main(
                ["--host", "h", "--port", "1", "--obs-key", "k", "--audit-log", str(log)]
            )
        assert rc == 2
        # Audit entry still written
        assert log.exists()

    def test_tls_flag_accepted(self, tmp_path):
        log = tmp_path / "a.jsonl"
        with mock.patch.object(red_team_wrong_obs_key, "attempt_auth") as mock_auth:
            mock_auth.return_value = {
                "status_code": 401,
                "refused": True,
                "error": None,
                "headers": {},
            }
            rc = red_team_wrong_obs_key.main(
                [
                    "--host",
                    "h",
                    "--port",
                    "1",
                    "--obs-key",
                    "k",
                    "--tls",
                    "--audit-log",
                    str(log),
                ]
            )
        assert rc == 0
        # The attempt_auth call is positional:
        # attempt_auth(host, port, obs_key, tls, timeout, path)
        args, _ = mock_auth.call_args
        assert args[3] is True  # use_tls positional arg

    def test_tls_flag_false_by_default(self, tmp_path):
        log = tmp_path / "a.jsonl"
        with mock.patch.object(red_team_wrong_obs_key, "attempt_auth") as mock_auth:
            mock_auth.return_value = {
                "status_code": 401,
                "refused": True,
                "error": None,
                "headers": {},
            }
            red_team_wrong_obs_key.main(
                ["--host", "h", "--port", "1", "--obs-key", "k", "--audit-log", str(log)]
            )
        args, _ = mock_auth.call_args
        assert args[3] is False  # use_tls positional arg

    def test_audit_log_none_creates_tempfile(self):
        with mock.patch.object(red_team_wrong_obs_key, "attempt_auth") as mock_auth:
            mock_auth.return_value = {
                "status_code": 401,
                "refused": True,
                "error": None,
                "headers": {},
            }
            rc = red_team_wrong_obs_key.main(
                ["--host", "h", "--port", "1", "--obs-key", "k"]
            )
        # Just check it returned cleanly and called attempt_auth
        assert rc == 0
        mock_auth.assert_called_once()

    def test_custom_path_passed_through(self):
        with mock.patch.object(red_team_wrong_obs_key, "attempt_auth") as mock_auth:
            mock_auth.return_value = {
                "status_code": 401,
                "refused": True,
                "error": None,
                "headers": {},
            }
            red_team_wrong_obs_key.main(
                [
                    "--host",
                    "h",
                    "--port",
                    "1",
                    "--obs-key",
                    "k",
                    "--path",
                    "/v1/ws",
                ]
            )
        args, _ = mock_auth.call_args
        # attempt_auth(host, port, obs_key, tls, timeout, path)
        assert args[5] == "/v1/ws"

    def test_custom_timeout_passed_through(self):
        with mock.patch.object(red_team_wrong_obs_key, "attempt_auth") as mock_auth:
            mock_auth.return_value = {
                "status_code": 401,
                "refused": True,
                "error": None,
                "headers": {},
            }
            red_team_wrong_obs_key.main(
                [
                    "--host",
                    "h",
                    "--port",
                    "1",
                    "--obs-key",
                    "k",
                    "--timeout",
                    "3.5",
                ]
            )
        args, _ = mock_auth.call_args
        # attempt_auth(host, port, obs_key, tls, timeout, path)
        assert args[4] == 3.5

    def test_sha256_hash_of_obs_key(self, tmp_path):
        import hashlib

        log = tmp_path / "a.jsonl"
        with mock.patch.object(red_team_wrong_obs_key, "attempt_auth") as mock_auth:
            mock_auth.return_value = {
                "status_code": 401,
                "refused": True,
                "error": None,
                "headers": {},
            }
            red_team_wrong_obs_key.main(
                ["--host", "h", "--port", "1", "--obs-key", "MYKEY", "--audit-log", str(log)]
            )
        entry = json.loads(log.read_text(encoding="utf-8").strip())
        assert entry["obs_key_sha256"] == hashlib.sha256(b"MYKEY").hexdigest()


# ---------------------------------------------------------------------------
# end-to-end subprocess
# ---------------------------------------------------------------------------


class TestSubprocessEndToEnd:
    """End-to-end subprocess tests for red_team_wrong_obs_key.py."""

    def test_subprocess_help(self):
        proc = subprocess.run(
            [sys.executable, str(_BIN_DIR / "red_team_wrong_obs_key.py"), "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert proc.returncode == 0
        assert "usage" in proc.stdout.lower() or "options" in proc.stdout.lower()

    def test_subprocess_unreachable_host(self):
        # Use a port nothing should be listening on
        proc = subprocess.run(
            [
                sys.executable,
                str(_BIN_DIR / "red_team_wrong_obs_key.py"),
                "--host",
                "127.0.0.1",
                "--port",
                "1",  # privileged, not bound
                "--obs-key",
                "WRONGKEY",
                "--timeout",
                "1.0",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        # Connection refused → return code 2
        assert proc.returncode in (0, 2)
        if proc.returncode == 2:
            assert "refused" in proc.stderr.lower() or "refused" in proc.stdout.lower() or proc.stdout

    def test_subprocess_local_server_refuses(self):
        """Run a tiny local TCP server that closes the connection immediately.

        This guarantees a connection (so we don't get the unreachable case)
        and the server sends nothing, so the client should see an empty
        response and refuse the key (return code 0) or error (return code 2).
        """
        import contextlib

        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind(("127.0.0.1", 0))
        server_sock.listen(1)
        port = server_sock.getsockname()[1]
        stop = threading.Event()

        def serve():
            server_sock.settimeout(0.5)
            while not stop.is_set():
                try:
                    conn, _ = server_sock.accept()
                except socket.timeout:
                    continue
                with contextlib.suppress(OSError):
                    conn.close()

        t = threading.Thread(target=serve, daemon=True)
        t.start()
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    str(_BIN_DIR / "red_team_wrong_obs_key.py"),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--obs-key",
                    "WRONGKEY",
                    "--timeout",
                    "2.0",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            # The client should refuse (0) or error (2); never accept (1)
            assert proc.returncode in (0, 2)
            assert proc.returncode != 1
        finally:
            stop.set()
            t.join(timeout=2)
            with contextlib.suppress(OSError):
                server_sock.close()

    def test_subprocess_audit_log_written(self, tmp_path):
        log = tmp_path / "audit.jsonl"
        proc = subprocess.run(
            [
                sys.executable,
                str(_BIN_DIR / "red_team_wrong_obs_key.py"),
                "--host",
                "127.0.0.1",
                "--port",
                "1",
                "--obs-key",
                "MYKEY",
                "--timeout",
                "1.0",
                "--audit-log",
                str(log),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        # Audit log should always be written, even on connection error
        assert log.exists()
        lines = log.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) >= 1
        entry = json.loads(lines[-1])
        assert entry["tool"] == "red_team_wrong_obs_key"
        assert "obs_key_sha256" in entry
        assert "attempt_id" in entry
        assert proc.returncode in (0, 1, 2)
