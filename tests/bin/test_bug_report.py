#!/usr/bin/env python3
"""
Tests for bin/bug_report.py — CLI tool for 内测 users to report bugs to Discord.

Validates: config loading, webhook URL extraction, user hash generation,
severity prompt validation, required prompt handling, yes/no prompt parsing,
crash dump reading, log tailing, Discord payload building, webhook posting,
and main() CLI entry point.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure the bin module is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "bin"))

import bug_report


class TestLoadConfig:
    """Tests for load_config()."""

    def test_load_config_success(self, tmp_path):
        """Test loading a valid JSON config file."""
        config_file = tmp_path / "config.json"
        config_data = {"bug_report_webhook": "https://discord.com/api/webhooks/123"}
        config_file.write_text(json.dumps(config_data))

        with patch.object(bug_report, "CONFIG_PATH", str(config_file)):
            result = bug_report.load_config()

        assert result == config_data

    def test_load_config_missing_file(self, tmp_path):
        """Test error when config file does not exist."""
        config_file = tmp_path / "nonexistent.json"

        with patch.object(bug_report, "CONFIG_PATH", str(config_file)):
            with pytest.raises(SystemExit) as exc_info:
                bug_report.load_config()
            assert exc_info.value.code == 1

    def test_load_config_invalid_json(self, tmp_path):
        """Test error when config file contains invalid JSON."""
        config_file = tmp_path / "config.json"
        config_file.write_text("{ invalid json }")

        with patch.object(bug_report, "CONFIG_PATH", str(config_file)):
            with pytest.raises(SystemExit) as exc_info:
                bug_report.load_config()
            assert exc_info.value.code == 1

    def test_load_config_not_dict(self, tmp_path):
        """Test error when config is not a JSON object."""
        config_file = tmp_path / "config.json"
        config_file.write_text("123")

        with patch.object(bug_report, "CONFIG_PATH", str(config_file)):
            with pytest.raises(SystemExit) as exc_info:
                bug_report.load_config()
            assert exc_info.value.code == 1


class TestGetWebhookUrl:
    """Tests for get_webhook_url()."""

    def test_get_webhook_url_primary_key(self):
        """Test extraction from primary bug_report_webhook key."""
        config = {"bug_report_webhook": "https://discord.com/api/webhooks/123"}
        result = bug_report.get_webhook_url(config)
        assert result == "https://discord.com/api/webhooks/123"

    def test_get_webhook_url_legacy_key(self):
        """Test extraction from legacy discord_webhook key."""
        config = {"discord_webhook": "https://discord.com/api/webhooks/456"}
        result = bug_report.get_webhook_url(config)
        assert result == "https://discord.com/api/webhooks/456"

    def test_get_webhook_url_primary_takes_precedence(self):
        """Test that primary key takes precedence over legacy."""
        config = {
            "bug_report_webhook": "https://discord.com/api/webhooks/123",
            "discord_webhook": "https://discord.com/api/webhooks/456",
        }
        result = bug_report.get_webhook_url(config)
        assert result == "https://discord.com/api/webhooks/123"

    def test_get_webhook_url_whitespace_stripped(self):
        """Test that whitespace is stripped from URL."""
        config = {"bug_report_webhook": "  https://discord.com/api/webhooks/123  "}
        result = bug_report.get_webhook_url(config)
        assert result == "https://discord.com/api/webhooks/123"

    def test_get_webhook_url_missing(self):
        """Test error when neither key is present."""
        config = {}
        with pytest.raises(SystemExit) as exc_info:
            bug_report.get_webhook_url(config)
        assert exc_info.value.code == 1


class TestHashUserIdentifier:
    """Tests for hash_user_identifier()."""

    def test_hash_user_identifier_returns_sha256_prefix(self):
        """Test that the function returns a prefix of a SHA-256 hash."""
        result = bug_report.hash_user_identifier()
        # Function returns first 16 chars of SHA-256 hex digest
        assert len(result) == 16
        assert all(c in "0123456789abcdef" for c in result)

    def test_hash_user_identifier_deterministic(self):
        """Test that the hash is stable across calls."""
        result1 = bug_report.hash_user_identifier()
        result2 = bug_report.hash_user_identifier()
        assert result1 == result2

    def test_hash_user_identifier_uses_home_and_username(self):
        """Test that different environments produce different hashes."""
        with patch.dict(os.environ, {"HOME": "/home/user1", "USERNAME": "user1"}):
            hash1 = bug_report.hash_user_identifier()

        with patch.dict(os.environ, {"HOME": "/home/user2", "USERNAME": "user2"}):
            hash2 = bug_report.hash_user_identifier()

        assert hash1 != hash2


class TestBuildDiscordPayload:
    """Tests for build_discord_payload()."""

    def test_build_discord_payload_minimal(self):
        """Test payload building with minimal required fields."""
        payload = bug_report.build_discord_payload(
            report_id="test-123",
            severity=2,
            title="Test Bug",
            steps="Step 1\nStep 2",
            expected="Expected behavior",
            actual="Actual behavior",
            user_hash="abc123def456",
        )

        assert payload["content"] == "🐛 **Bug Report** `test-123`"
        assert len(payload["embeds"]) == 1
        embed = payload["embeds"][0]
        assert embed["title"] == "Test Bug"
        assert embed["color"] == 0xFFFF00  # Yellow for Medium
        assert len(embed["fields"]) == 6

    def test_build_discord_payload_severity_low(self):
        """Test severity level 1 (Low) maps to green."""
        payload = bug_report.build_discord_payload(
            report_id="test-123",
            severity=1,
            title="Test Bug",
            steps="Steps",
            expected="Expected",
            actual="Actual",
            user_hash="abc123",
        )
        assert payload["embeds"][0]["color"] == 0x00FF00

    def test_build_discord_payload_severity_critical(self):
        """Test severity level 3 (Critical) maps to red."""
        payload = bug_report.build_discord_payload(
            report_id="test-123",
            severity=3,
            title="Test Bug",
            steps="Steps",
            expected="Expected",
            actual="Actual",
            user_hash="abc123",
        )
        assert payload["embeds"][0]["color"] == 0xFF0000

    def test_build_discord_payload_severity_unknown(self):
        """Test unknown severity maps to gray."""
        payload = bug_report.build_discord_payload(
            report_id="test-123",
            severity=99,
            title="Test Bug",
            steps="Steps",
            expected="Expected",
            actual="Actual",
            user_hash="abc123",
        )
        assert payload["embeds"][0]["color"] == 0x888888

    def test_build_discord_payload_with_crash_dump(self):
        """Test payload includes crash dump attachment."""
        crash_b64 = base64.b64encode(b"mock crash dump").decode("utf-8")
        payload = bug_report.build_discord_payload(
            report_id="test-123",
            severity=2,
            title="Test Bug",
            steps="Steps",
            expected="Expected",
            actual="Actual",
            user_hash="abc123",
            crash_dump_b64=crash_b64,
        )

        assert "attachments_data" in payload
        assert "crash_dump.b64" in payload["attachments_data"]
        assert payload["attachments_data"]["crash_dump.b64"] == crash_b64

    def test_build_discord_payload_with_log_tail(self):
        """Test payload includes log tail attachment."""
        log_data = "line 1\nline 2\nline 3"
        payload = bug_report.build_discord_payload(
            report_id="test-123",
            severity=2,
            title="Test Bug",
            steps="Steps",
            expected="Expected",
            actual="Actual",
            user_hash="abc123",
            log_tail=log_data,
        )

        assert "attachments_data" in payload
        assert "log_tail.txt" in payload["attachments_data"]
        assert payload["attachments_data"]["log_tail.txt"] == log_data


class TestReadCrashDump:
    """Tests for read_crash_dump()."""

    def test_read_crash_dump_success(self, tmp_path):
        """Test successful reading of a crash dump file."""
        crash_file = tmp_path / "crash.dmp"
        crash_file.write_bytes(b"mock crash dump data")

        result = bug_report.read_crash_dump(str(crash_file))
        assert result == base64.b64encode(b"mock crash dump data").decode("utf-8")

    def test_read_crash_dump_nonexistent(self, tmp_path):
        """Test error handling for nonexistent file."""
        crash_file = tmp_path / "nonexistent.dmp"
        result = bug_report.read_crash_dump(str(crash_file))
        assert result is None


class TestTailLog:
    """Tests for tail_log()."""

    def test_tail_log_success(self, tmp_path):
        """Test successful reading of log file tail."""
        log_file = tmp_path / "test.log"
        log_file.write_text("\n".join(f"line {i}" for i in range(250)))

        result = bug_report.tail_log(str(log_file), lines=10)
        assert result is not None
        lines = result.split("\n")
        assert len(lines) == 10
        assert lines[0].strip() == "line 240"

    def test_tail_log_fewer_lines_than_requested(self, tmp_path):
        """Test when file has fewer lines than requested."""
        log_file = tmp_path / "test.log"
        log_file.write_text("line 1\nline 2\nline 3")

        result = bug_report.tail_log(str(log_file), lines=100)
        assert result is not None
        assert "line 1" in result

    def test_tail_log_nonexistent(self, tmp_path):
        """Test error handling for nonexistent file."""
        log_file = tmp_path / "nonexistent.log"
        result = bug_report.tail_log(str(log_file))
        assert result is None

    def test_tail_log_truncation(self, tmp_path):
        """Test that content over MAX_ATTACH_BYTES is truncated."""
        # Create a log file with enough content to exceed 2MB
        large_content = "x" * (3 * 1024 * 1024)  # 3MB
        log_file = tmp_path / "large.log"
        log_file.write_text(large_content)

        result = bug_report.tail_log(str(log_file))
        assert result is not None
        # Result should be truncated to fit within MAX_ATTACH_BYTES
        assert len(result.encode("utf-8")) <= bug_report.MAX_ATTACH_BYTES


class TestMain:
    """Tests for main() CLI entry point."""

    def test_main_missing_config(self, tmp_path, capsys):
        """Test main() exits when config file is missing."""
        nonexistent_config = str(tmp_path / "nonexistent.json")

        with patch.object(bug_report, "CONFIG_PATH", nonexistent_config):
            with pytest.raises(SystemExit) as exc_info:
                bug_report.main()
            assert exc_info.value.code == 1

    def test_main_missing_webhook(self, tmp_path):
        """Test main() exits when webhook is not configured."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({}))

        with patch.object(bug_report, "CONFIG_PATH", str(config_file)):
            with patch("builtins.input", side_effect=["3", "Test", "Steps", "Expected", "Actual"]):
                with pytest.raises(SystemExit) as exc_info:
                    bug_report.main()
                assert exc_info.value.code == 1

    @patch("bug_report.requests.post")
    @patch("builtins.input")
    def test_main_success(self, mock_input, mock_post, tmp_path):
        """Test successful bug report submission."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"bug_report_webhook": "https://discord.com/api/webhooks/123"}))

        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_post.return_value = mock_response

        # Mock all the interactive prompts
        mock_input.side_effect = [
            "3",  # severity
            "Test Bug Title",  # title
            "1. Open app\n2. Click button",  # steps
            "App should not crash",  # expected
            "App crashes",  # actual
            "no",  # attach crash dump - no
            "no",  # attach log - no
        ]

        with patch.object(bug_report, "CONFIG_PATH", str(config_file)):
            bug_report.main()

        # Verify the webhook was called
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert call_args[0][0] == "https://discord.com/api/webhooks/123"
        assert "embeds" in call_args[1]["json"]

    @patch("bug_report.requests.post")
    @patch("builtins.input")
    def test_main_retry_on_5xx(self, mock_input, mock_post, tmp_path):
        """Test that main() retries once on 5xx errors."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"bug_report_webhook": "https://discord.com/api/webhooks/123"}))

        # First call fails with 500, second succeeds
        mock_response_fail = MagicMock()
        mock_response_fail.status_code = 500

        mock_response_success = MagicMock()
        mock_response_success.status_code = 204

        mock_post.side_effect = [mock_response_fail, mock_response_success]

        mock_input.side_effect = [
            "3",
            "Test Bug",
            "Steps",
            "Expected",
            "Actual",
            "no",
            "no",
        ]

        with patch.object(bug_report, "CONFIG_PATH", str(config_file)):
            bug_report.main()

        # Should have been called twice (initial + retry)
        assert mock_post.call_count == 2

    @patch("bug_report.requests.post")
    @patch("bug_report.read_crash_dump")
    @patch("builtins.input")
    def test_main_with_crash_dump(self, mock_input, mock_crash_dump, mock_post, tmp_path):
        """Test bug report with crash dump attachment."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"bug_report_webhook": "https://discord.com/api/webhooks/123"}))

        # Mock the crash dump to return test data
        mock_crash_dump.return_value = base64.b64encode(b"mock crash data").decode("ascii")

        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_post.return_value = mock_response

        # Include crash dump path in input
        mock_input.side_effect = [
            "3",
            "Test Bug",
            "Steps",
            "Expected",
            "Actual",
            "yes",  # include crash dump
            "no",  # attach log - no
        ]

        with patch.object(bug_report, "CONFIG_PATH", str(config_file)):
            bug_report.main()

        # Verify crash dump was included
        call_args = mock_post.call_args
        payload = call_args[1]["json"]
        assert "crash_dump.b64" in payload.get("attachments_data", {})

    @patch("bug_report.requests.post")
    @patch("bug_report.tail_log")
    @patch("builtins.input")
    def test_main_with_log_tail(self, mock_input, mock_tail_log, mock_post, tmp_path):
        """Test bug report with log tail attachment."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"bug_report_webhook": "https://discord.com/api/webhooks/123"}))

        # Mock the log tail to return test data
        mock_tail_log.return_value = "line 1\nline 2\nline 3"

        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_post.return_value = mock_response

        mock_input.side_effect = [
            "2",
            "Test Bug",
            "Steps",
            "Expected",
            "Actual",
            "no",  # no crash dump
            "yes",  # include log tail
        ]

        with patch.object(bug_report, "CONFIG_PATH", str(config_file)):
            bug_report.main()

        # Verify log tail was included
        call_args = mock_post.call_args
        payload = call_args[1]["json"]
        assert "log_tail.txt" in payload.get("attachments_data", {})


class TestPromptSeverity:
    """Tests for prompt_severity()."""

    def test_prompt_severity_valid(self):
        """Test valid severity input."""
        with patch("builtins.input", side_effect=["2"]):
            result = bug_report.prompt_severity()
            assert result == 2

    def test_prompt_severity_invalid_retry(self):
        """Test invalid input retries until valid."""
        with patch("builtins.input", side_effect=["invalid", "5", "1"]):
            result = bug_report.prompt_severity()
            assert result == 1


class TestPromptRequired:
    """Tests for prompt_required()."""

    def test_prompt_required_valid(self):
        """Test non-empty input is returned."""
        with patch("builtins.input", return_value="some input"):
            result = bug_report.prompt_required("Enter something:")
            assert result == "some input"

    def test_prompt_required_empty_retries(self):
        """Test empty input prompts again."""
        with patch("builtins.input", side_effect=["", "  ", "valid input"]):
            result = bug_report.prompt_required("Enter something:")
            assert result == "valid input"


class TestPromptYesNo:
    """Tests for prompt_yes_no()."""

    def test_prompt_yes_no_true_variations(self):
        """Test various yes inputs return True."""
        for yes_input in ["yes", "Y", "y", "YES"]:
            with patch("builtins.input", return_value=yes_input):
                result = bug_report.prompt_yes_no("Continue?")
                assert result is True

    def test_prompt_yes_no_false_variations(self):
        """Test various no inputs return False."""
        for no_input in ["no", "N", "n", "NO"]:
            with patch("builtins.input", return_value=no_input):
                result = bug_report.prompt_yes_no("Continue?")
                assert result is False

    def test_prompt_yes_no_invalid_retries(self):
        """Test invalid input retries until valid."""
        with patch("builtins.input", side_effect=["maybe", "y"]):
            result = bug_report.prompt_yes_no("Continue?")
            assert result is True


class TestPostToWebhook:
    """Tests for post_to_webhook()."""

    @patch("bug_report.requests.post")
    def test_post_to_webhook_success(self, mock_post):
        """Test successful webhook posting."""
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_post.return_value = mock_response

        payload = {"content": "test"}
        result = bug_report.post_to_webhook("https://example.com/webhook", payload)

        assert result == mock_response
        mock_post.assert_called_once()

    @patch("bug_report.requests.post")
    def test_post_to_webhook_retry_on_5xx(self, mock_post):
        """Test retry on 5xx server error."""
        mock_response_fail = MagicMock()
        mock_response_fail.status_code = 500  # 5xx triggers retry

        mock_response_success = MagicMock()
        mock_response_success.status_code = 204  # < 500 returns immediately

        mock_post.side_effect = [mock_response_fail, mock_response_success]

        payload = {"content": "test"}
        result = bug_report.post_to_webhook("https://example.com/webhook", payload)

        assert result == mock_response_success
        assert mock_post.call_count == 2

    @patch("bug_report.requests.post")
    def test_post_to_webhook_no_retry_on_4xx(self, mock_post):
        """Test no retry on 4xx client error."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception("Client Error")
        mock_post.return_value = mock_response

        payload = {"content": "test"}

        with pytest.raises(Exception):
            bug_report.post_to_webhook("https://example.com/webhook", payload)

        assert mock_post.call_count == 1  # No retry
