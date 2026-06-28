"""
tests/test_bug_report.py — Tests for bin/bug_report.py

All Discord HTTP POST calls are mocked via responses library / unittest.mock.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

# Ensure bin/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bin.bug_report as br

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_config_dir(tmp_path):
    """Create a temporary ~/.oyster directory with a config.json."""
    oyster_dir = tmp_path / ".oyster"
    oyster_dir.mkdir()
    config = {
        "bug_report_webhook": "https://discord.com/api/webhooks/test/test-token",
    }
    (oyster_dir / "config.json").write_text(json.dumps(config))
    return oyster_dir


@pytest.fixture
def tmp_config_dir_no_webhook(tmp_path):
    """Config file exists but bug_report_webhook key is missing."""
    oyster_dir = tmp_path / ".oyster"
    oyster_dir.mkdir()
    (oyster_dir / "config.json").write_text(json.dumps({"other_key": "value"}))
    return oyster_dir


@pytest.fixture
def tmp_config_dir_empty_webhook(tmp_path):
    """Config file exists but bug_report_webhook is empty string."""
    oyster_dir = tmp_path / ".oyster"
    oyster_dir.mkdir()
    (oyster_dir / "config.json").write_text(json.dumps({"bug_report_webhook": ""}))
    return oyster_dir


@pytest.fixture
def crash_dump_file(tmp_path):
    """Create a small fake crash dump file."""
    dump_path = tmp_path / "crash.dmp"
    dump_path.write_bytes(b"\x00\x01\x02\x03CRASHDUMP")
    return dump_path


@pytest.fixture
def log_file(tmp_path):
    """Create a fake OysterRecorder.log with 300 lines."""
    log_path = tmp_path / "OysterRecorder.log"
    lines = [f"[2026-05-19 12:{i:02d}:00] Log line {i}\n" for i in range(300)]
    log_path.write_text("".join(lines))
    return log_path


# ---------------------------------------------------------------------------
# load_config tests
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_load_config_success(self, tmp_config_dir):
        with mock.patch.object(br, "CONFIG_PATH", str(tmp_config_dir / "config.json")):
            cfg = br.load_config()
        assert cfg["bug_report_webhook"] == "https://discord.com/api/webhooks/test/test-token"

    def test_load_config_missing_file(self):
        with (
            mock.patch.object(br, "CONFIG_PATH", "/nonexistent/path/config.json"),
            pytest.raises(SystemExit) as exc_info,
        ):
            br.load_config()
        assert exc_info.value.code == 1

    def test_load_config_invalid_json(self, tmp_path):
        bad_json = tmp_path / "config.json"
        bad_json.write_text("{not valid json")
        with (
            mock.patch.object(br, "CONFIG_PATH", str(bad_json)),
            pytest.raises(SystemExit) as exc_info,
        ):
            br.load_config()
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# get_webhook_url tests
# ---------------------------------------------------------------------------


class TestGetWebhookUrl:
    def test_webhook_present(self):
        cfg = {"bug_report_webhook": "https://example.com/hook"}
        assert br.get_webhook_url(cfg) == "https://example.com/hook"

    def test_webhook_missing(self, capsys):
        cfg = {"other_key": "value"}
        with pytest.raises(SystemExit) as exc_info:
            br.get_webhook_url(cfg)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "bug_report_webhook" in captured.err

    def test_webhook_empty(self, capsys):
        cfg = {"bug_report_webhook": "   "}
        with pytest.raises(SystemExit) as exc_info:
            br.get_webhook_url(cfg)
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# hash_user_identifier tests
# ---------------------------------------------------------------------------


class TestHashUserIdentifier:
    def test_returns_hex_string(self):
        h = br.hash_user_identifier()
        assert len(h) == 16
        int(h, 16)  # should not raise — valid hex

    def test_deterministic(self):
        h1 = br.hash_user_identifier()
        h2 = br.hash_user_identifier()
        assert h1 == h2

    def test_no_pii_in_output(self):
        """Hash should not contain raw HOME or USER values."""
        h = br.hash_user_identifier()
        home = os.environ.get("HOME", "")
        user = os.environ.get("USER", "")
        if home:
            assert home not in h
        if user:
            assert user not in h


# ---------------------------------------------------------------------------
# read_crash_dump tests
# ---------------------------------------------------------------------------


class TestReadCrashDump:
    def test_existing_file(self, crash_dump_file):
        result = br.read_crash_dump(str(crash_dump_file))
        assert result is not None
        # Should be valid base64
        decoded = base64.b64decode(result)
        assert decoded == b"\x00\x01\x02\x03CRASHDUMP"

    def test_nonexistent_file(self, capsys):
        result = br.read_crash_dump("/nonexistent/crash.dmp")
        assert result is None
        captured = capsys.readouterr()
        assert "not found" in captured.out

    def test_oversized_file(self, tmp_path, capsys):
        big = tmp_path / "big.dmp"
        # Write 3 MB file
        big.write_bytes(b"x" * (3 * 1024 * 1024))
        result = br.read_crash_dump(str(big))
        assert result is None
        captured = capsys.readouterr()
        assert "exceeds" in captured.out


# ---------------------------------------------------------------------------
# tail_log tests
# ---------------------------------------------------------------------------


class TestTailLog:
    def test_tail_200_lines(self, log_file):
        result = br.tail_log(str(log_file), lines=200)
        assert result is not None
        lines = result.strip().split("\n")
        assert len(lines) == 200
        # Should be the last 200 lines (299 down to 100)
        assert "Log line 299" in lines[-1]

    def test_nonexistent_file(self, capsys):
        result = br.tail_log("/nonexistent/OysterRecorder.log")
        assert result is None

    def test_fewer_lines_than_requested(self, tmp_path):
        small_log = tmp_path / "small.log"
        small_log.write_text("line1\nline2\nline3\n")
        result = br.tail_log(str(small_log), lines=200)
        assert result is not None
        assert "line1" in result
        assert "line3" in result


# ---------------------------------------------------------------------------
# build_discord_payload tests
# ---------------------------------------------------------------------------


class TestBuildDiscordPayload:
    def test_basic_payload(self):
        payload = br.build_discord_payload(
            report_id="test-uuid",
            severity=2,
            title="Game crashes on startup",
            steps="1. Launch game\n2. Click Play",
            expected="Game starts normally",
            actual="Game crashes with segfault",
            user_hash="abc123",
            crash_dump_b64=None,
            log_tail=None,
        )
        assert payload["content"] == "🐛 **Bug Report** `test-uuid`"
        assert len(payload["embeds"]) == 1
        embed = payload["embeds"][0]
        assert embed["title"] == "Game crashes on startup"
        assert embed["color"] == 0xFFFF00  # yellow for medium
        assert embed["footer"]["text"] == "Report ID: test-uuid"

    def test_severity_colors(self):
        for sev, color in [(1, 0x00FF00), (2, 0xFFFF00), (3, 0xFF0000)]:
            payload = br.build_discord_payload(
                report_id="x",
                severity=sev,
                title="t",
                steps="s",
                expected="e",
                actual="a",
                user_hash="h",
                crash_dump_b64=None,
                log_tail=None,
            )
            assert payload["embeds"][0]["color"] == color

    def test_with_crash_dump(self):
        payload = br.build_discord_payload(
            report_id="x",
            severity=1,
            title="t",
            steps="s",
            expected="e",
            actual="a",
            user_hash="h",
            crash_dump_b64="base64data",
            log_tail=None,
        )
        assert "crash_dump.b64" in payload["attachments_data"]
        assert payload["attachments_data"]["crash_dump.b64"] == "base64data"

    def test_with_log_tail(self):
        payload = br.build_discord_payload(
            report_id="x",
            severity=1,
            title="t",
            steps="s",
            expected="e",
            actual="a",
            user_hash="h",
            crash_dump_b64=None,
            log_tail="some log lines",
        )
        assert "log_tail.txt" in payload["attachments_data"]

    def test_no_pii_in_payload(self):
        """Payload should not contain OAuth tokens or credentials."""
        payload = br.build_discord_payload(
            report_id="x",
            severity=1,
            title="t",
            steps="s",
            expected="e",
            actual="a",
            user_hash="h",
            crash_dump_b64=None,
            log_tail=None,
        )
        payload_str = json.dumps(payload)
        assert "token" not in payload_str.lower() or "webhook" not in payload_str.lower()
        assert "password" not in payload_str.lower()
        assert "secret" not in payload_str.lower()


# ---------------------------------------------------------------------------
# post_to_webhook tests
# ---------------------------------------------------------------------------


class TestPostToWebhook:
    @mock.patch("bin.bug_report.requests.post")
    def test_success_200(self, mock_post):
        mock_resp = mock.Mock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        resp = br.post_to_webhook("https://example.com/hook", {"content": "test"})
        assert resp.status_code == 200
        mock_post.assert_called_once()

    @mock.patch("bin.bug_report.requests.post")
    def test_success_204(self, mock_post):
        mock_resp = mock.Mock()
        mock_resp.status_code = 204
        mock_post.return_value = mock_resp

        resp = br.post_to_webhook("https://example.com/hook", {"content": "test"})
        assert resp.status_code == 204

    @mock.patch("bin.bug_report.requests.post")
    def test_retry_on_500_then_success(self, mock_post):
        """Should retry once on 500, then succeed."""
        resp_500 = mock.Mock()
        resp_500.status_code = 500
        resp_200 = mock.Mock()
        resp_200.status_code = 200
        mock_post.side_effect = [resp_500, resp_200]

        resp = br.post_to_webhook("https://example.com/hook", {"content": "test"})
        assert resp.status_code == 200
        assert mock_post.call_count == 2

    @mock.patch("bin.bug_report.requests.post")
    def test_retry_on_connection_error_then_success(self, mock_post):
        """Should retry once on connection error, then succeed."""
        import requests as req

        resp_200 = mock.Mock()
        resp_200.status_code = 200
        mock_post.side_effect = [
            req.exceptions.ConnectionError("Connection refused"),
            resp_200,
        ]

        resp = br.post_to_webhook("https://example.com/hook", {"content": "test"})
        assert resp.status_code == 200
        assert mock_post.call_count == 2

    @mock.patch("bin.bug_report.requests.post")
    def test_no_retry_on_400(self, mock_post):
        """Client errors (4xx) should NOT be retried."""
        mock_resp = mock.Mock()
        mock_resp.status_code = 400
        mock_post.return_value = mock_resp

        resp = br.post_to_webhook("https://example.com/hook", {"content": "test"})
        assert resp.status_code == 400
        assert mock_post.call_count == 1

    @mock.patch("bin.bug_report.requests.post")
    def test_all_retries_exhausted(self, mock_post):
        """If all retries fail, should raise the last exception."""
        import requests as req

        mock_post.side_effect = req.exceptions.ConnectionError("Connection refused")

        with pytest.raises(req.exceptions.ConnectionError):
            br.post_to_webhook("https://example.com/hook", {"content": "test"})
        # Initial + 1 retry = 2 calls
        assert mock_post.call_count == 2


# ---------------------------------------------------------------------------
# Integration-style test: full flow with mocked I/O and HTTP
# ---------------------------------------------------------------------------


class TestFullFlow:
    @mock.patch("bin.bug_report.post_to_webhook")
    @mock.patch("bin.bug_report.tail_log")
    @mock.patch("bin.bug_report.read_crash_dump")
    @mock.patch("bin.bug_report.prompt_yes_no")
    @mock.patch("bin.bug_report.prompt_required")
    @mock.patch("bin.bug_report.prompt_severity")
    @mock.patch("bin.bug_report.get_webhook_url")
    @mock.patch("bin.bug_report.load_config")
    def test_full_interactive_flow(
        self,
        mock_load_config,
        mock_get_webhook,
        mock_severity,
        mock_required,
        mock_yes_no,
        mock_crash,
        mock_log,
        mock_post,
        capsys,
    ):
        """Simulate a full interactive run with all mocks."""
        mock_load_config.return_value = {"bug_report_webhook": "https://example.com/hook"}
        mock_get_webhook.return_value = "https://example.com/hook"
        mock_severity.return_value = 3
        mock_required.side_effect = [
            "Game crashes on launch",
            "1. Open game\n2. Click Start",
            "Game loads normally",
            "Segfault immediately",
        ]
        mock_yes_no.side_effect = [True, True]  # attach both
        mock_crash.return_value = base64.b64encode(b"fake crash data").decode()
        mock_log.return_value = "[2026-05-19] ERROR: segfault"

        mock_resp = mock.Mock()
        mock_resp.status_code = 204
        mock_post.return_value = mock_resp

        br.main()

        captured = capsys.readouterr()
        assert "Report sent, ID:" in captured.out
        # Verify post was called with a proper payload
        call_args = mock_post.call_args
        payload = call_args[0][1]
        assert "embeds" in payload
        assert "attachments_data" in payload

    @mock.patch("bin.bug_report.post_to_webhook")
    @mock.patch("bin.bug_report.tail_log")
    @mock.patch("bin.bug_report.read_crash_dump")
    @mock.patch("bin.bug_report.prompt_yes_no")
    @mock.patch("bin.bug_report.prompt_required")
    @mock.patch("bin.bug_report.prompt_severity")
    @mock.patch("bin.bug_report.get_webhook_url")
    @mock.patch("bin.bug_report.load_config")
    def test_flow_no_attachments(
        self,
        mock_load_config,
        mock_get_webhook,
        mock_severity,
        mock_required,
        mock_yes_no,
        mock_crash,
        mock_log,
        mock_post,
        capsys,
    ):
        """Simulate a run where user declines attachments."""
        mock_load_config.return_value = {"bug_report_webhook": "https://example.com/hook"}
        mock_get_webhook.return_value = "https://example.com/hook"
        mock_severity.return_value = 1
        mock_required.side_effect = [
            "Minor UI glitch",
            "Open settings",
            "Settings open",
            "Button misaligned",
        ]
        mock_yes_no.side_effect = [False, False]  # no attachments

        mock_resp = mock.Mock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        br.main()

        captured = capsys.readouterr()
        assert "Report sent, ID:" in captured.out
        # Verify no attachments_data in payload
        call_args = mock_post.call_args
        payload = call_args[0][1]
        assert "attachments_data" not in payload

    @mock.patch("bin.bug_report.post_to_webhook")
    @mock.patch("bin.bug_report.tail_log")
    @mock.patch("bin.bug_report.read_crash_dump")
    @mock.patch("bin.bug_report.prompt_yes_no")
    @mock.patch("bin.bug_report.prompt_required")
    @mock.patch("bin.bug_report.prompt_severity")
    @mock.patch("bin.bug_report.get_webhook_url")
    @mock.patch("bin.bug_report.load_config")
    def test_flow_http_failure(
        self,
        mock_load_config,
        mock_get_webhook,
        mock_severity,
        mock_required,
        mock_yes_no,
        mock_crash,
        mock_log,
        mock_post,
        capsys,
    ):
        """Simulate a run where the webhook POST fails."""
        mock_load_config.return_value = {"bug_report_webhook": "https://example.com/hook"}
        mock_get_webhook.return_value = "https://example.com/hook"
        mock_severity.return_value = 2
        mock_required.side_effect = [
            "Crash",
            "steps",
            "expected",
            "actual",
        ]
        mock_yes_no.side_effect = [False, False]

        mock_resp = mock.Mock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_post.return_value = mock_resp

        with pytest.raises(SystemExit) as exc_info:
            br.main()
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# Edge case: webhook URL missing → exit with clear error
# ---------------------------------------------------------------------------


class TestWebhookMissing:
    def test_missing_webhook_exits_with_error(self, capsys):
        cfg = {"some_other_key": "value"}
        with pytest.raises(SystemExit) as exc_info:
            br.get_webhook_url(cfg)
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "bug_report_webhook" in err


# ---------------------------------------------------------------------------
# Crash dump base64 encoding verification
# ---------------------------------------------------------------------------


class TestCrashDumpBase64:
    def test_crash_dump_is_valid_base64(self, crash_dump_file):
        result = br.read_crash_dump(str(crash_dump_file))
        assert result is not None
        # Verify it decodes back to original
        decoded = base64.b64decode(result)
        assert decoded == b"\x00\x01\x02\x03CRASHDUMP"

    def test_crash_dump_encoding_roundtrip(self, tmp_path):
        """Verify that any binary content round-trips through base64."""
        original = bytes(range(256))
        dump = tmp_path / "full_binary.dmp"
        dump.write_bytes(original)
        result = br.read_crash_dump(str(dump))
        assert result is not None
        assert base64.b64decode(result) == original
