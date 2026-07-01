#!/usr/bin/env python3
"""
Tests for bin/acceptance_signal_api.py

Coverage:
- send_signal: valid requests, invalid signal, invalid URL, HTTP errors, connection errors
- parse_args: valid args, missing required args, optional args
- main: success path, failure path, missing args
"""

import json
from datetime import datetime
from io import BytesIO, StringIO
from unittest import mock

import pytest

# Import the module under test
import bin.acceptance_signal_api as acceptance_signal_api


class TestSendSignal:
    """Tests for send_signal function."""

    def test_valid_accept_signal(self):
        """Test sending a valid 'accept' signal."""
        with mock.patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = mock.MagicMock()
            mock_response.status = 200
            mock_response.read.return_value = b'{"result":"accepted"}'
            mock_urlopen.return_value.__enter__.return_value = mock_response

            status, body = acceptance_signal_api.send_signal(
                url="https://vendor.example/webhook",
                signal="accept",
                transaction_id="TXN-12345",
            )

            assert status == 200
            assert body == '{"result":"accepted"}'
            mock_urlopen.assert_called_once()

    def test_valid_reject_signal_with_metadata(self):
        """Test sending a valid 'reject' signal with metadata."""
        with mock.patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = mock.MagicMock()
            mock_response.status = 201
            mock_response.read.return_value = b'{"status":"recorded"}'
            mock_urlopen.return_value.__enter__.return_value = mock_response

            metadata = {"reason": "quality", "score": 0.3}
            status, body = acceptance_signal_api.send_signal(
                url="https://vendor.example/webhook",
                signal="reject",
                transaction_id="TXN-67890",
                metadata=metadata,
            )

            assert status == 201
            # Verify metadata was included in the request
            call_args = mock_urlopen.call_args
            request = call_args[0][0]
            payload = json.loads(request.data.decode("utf-8"))
            assert payload["signal"] == "reject"
            assert payload["metadata"] == metadata

    def test_invalid_signal_raises_value_error(self):
        """Test that invalid signal raises ValueError."""
        with pytest.raises(ValueError, match="Signal must be 'accept' or 'reject'"):
            acceptance_signal_api.send_signal(
                url="https://vendor.example/webhook",
                signal="invalid",
                transaction_id="TXN-12345",
            )

    def test_invalid_url_raises_value_error(self):
        """Test that invalid URL raises ValueError."""
        with pytest.raises(ValueError, match="Invalid URL format"):
            acceptance_signal_api.send_signal(
                url="not-a-url",
                signal="accept",
                transaction_id="TXN-12345",
            )

    def test_empty_url_raises_value_error(self):
        """Test that empty URL raises ValueError."""
        with pytest.raises(ValueError, match="Invalid URL format"):
            acceptance_signal_api.send_signal(
                url="",
                signal="accept",
                transaction_id="TXN-12345",
            )

    def test_http_error_returns_status_and_body(self):
        """Test that HTTP errors return status code and error body."""
        with mock.patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = acceptance_signal_api.urllib.error.HTTPError(
                url="https://vendor.example/webhook",
                code=400,
                msg="Bad Request",
                hdrs={},
                fp=BytesIO(b'{"error":"invalid payload"}'),
            )

            status, body = acceptance_signal_api.send_signal(
                url="https://vendor.example/webhook",
                signal="accept",
                transaction_id="TXN-12345",
            )

            assert status == 400
            assert body == '{"error":"invalid payload"}'

    def test_connection_error_raises_runtime_error(self):
        """Test that connection errors raise RuntimeError."""
        with mock.patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = acceptance_signal_api.urllib.error.URLError(
                "Connection refused"
            )

            with pytest.raises(RuntimeError, match="Connection failed"):
                acceptance_signal_api.send_signal(
                    url="https://vendor.example/webhook",
                    signal="accept",
                    transaction_id="TXN-12345",
                )

    def test_custom_timeout(self):
        """Test that custom timeout is passed to urlopen."""
        with mock.patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = mock.MagicMock()
            mock_response.status = 200
            mock_response.read.return_value = b"{}"
            mock_urlopen.return_value.__enter__.return_value = mock_response

            acceptance_signal_api.send_signal(
                url="https://vendor.example/webhook",
                signal="accept",
                transaction_id="TXN-12345",
                timeout=60,
            )

            # Verify timeout was passed
            call_kwargs = mock_urlopen.call_args[1]
            assert call_kwargs["timeout"] == 60

    def test_payload_includes_timestamp(self):
        """Test that payload includes ISO timestamp."""
        with mock.patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = mock.MagicMock()
            mock_response.status = 200
            mock_response.read.return_value = b"{}"
            mock_urlopen.return_value.__enter__.return_value = mock_response

            acceptance_signal_api.send_signal(
                url="https://vendor.example/webhook",
                signal="accept",
                transaction_id="TXN-12345",
            )

            # Verify timestamp was included
            call_args = mock_urlopen.call_args
            request = call_args[0][0]
            payload = json.loads(request.data.decode("utf-8"))
            assert "timestamp" in payload
            # Verify it's a valid ISO format
            datetime.fromisoformat(payload["timestamp"])


class TestParseArgs:
    """Tests for parse_args function."""

    def test_valid_accept_args(self):
        """Test parsing valid accept signal arguments."""
        args = acceptance_signal_api.parse_args(
            ["--url", "https://vendor.example/webhook", "accept", "TXN-12345"]
        )

        assert args.url == "https://vendor.example/webhook"
        assert args.signal == "accept"
        assert args.transaction_id == "TXN-12345"
        assert args.metadata is None
        assert args.timeout == 30  # default

    def test_valid_reject_args_with_metadata(self):
        """Test parsing reject signal with JSON metadata."""
        args = acceptance_signal_api.parse_args(
            [
                "--url",
                "https://vendor.example/webhook",
                "reject",
                "TXN-67890",
                "-m",
                '{"reason":"quality"}',
            ]
        )

        assert args.signal == "reject"
        assert args.metadata == {"reason": "quality"}

    def test_custom_timeout(self):
        """Test parsing custom timeout."""
        args = acceptance_signal_api.parse_args(
            [
                "--url",
                "https://vendor.example/webhook",
                "accept",
                "TXN-12345",
                "--timeout",
                "60",
            ]
        )

        assert args.timeout == 60

    def test_quiet_flag(self):
        """Test parsing quiet flag."""
        args = acceptance_signal_api.parse_args(
            [
                "--url",
                "https://vendor.example/webhook",
                "accept",
                "TXN-12345",
                "-q",
            ]
        )

        assert args.quiet is True

    def test_missing_url_raises_system_exit(self):
        """Test that missing required --url raises SystemExit."""
        with pytest.raises(SystemExit):
            acceptance_signal_api.parse_args(["accept", "TXN-12345"])

    def test_invalid_signal_raises_system_exit(self):
        """Test that invalid signal choice raises SystemExit."""
        with pytest.raises(SystemExit):
            acceptance_signal_api.parse_args(
                ["--url", "https://vendor.example/webhook", "maybe", "TXN-12345"]
            )


class TestMain:
    """Tests for main function."""

    def test_successful_accept_signal(self):
        """Test main returns 0 on successful accept signal."""
        with mock.patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = mock.MagicMock()
            mock_response.status = 200
            mock_response.read.return_value = b'{"result":"accepted"}'
            mock_urlopen.return_value.__enter__.return_value = mock_response

            result = acceptance_signal_api.main(
                ["--url", "https://vendor.example/webhook", "accept", "TXN-12345"]
            )

            assert result == 0

    def test_successful_reject_signal(self):
        """Test main returns 0 on successful reject signal."""
        with mock.patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = mock.MagicMock()
            mock_response.status = 201
            mock_response.read.return_value = b'{"status":"recorded"}'
            mock_urlopen.return_value.__enter__.return_value = mock_response

            result = acceptance_signal_api.main(
                ["--url", "https://vendor.example/webhook", "reject", "TXN-67890"]
            )

            assert result == 0

    def test_http_error_returns_1(self):
        """Test main returns 1 on HTTP error."""
        with mock.patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = acceptance_signal_api.urllib.error.HTTPError(
                url="https://vendor.example/webhook",
                code=400,
                msg="Bad Request",
                hdrs={},
                fp=BytesIO(b'{"error":"invalid"}'),
            )

            result = acceptance_signal_api.main(
                ["--url", "https://vendor.example/webhook", "accept", "TXN-12345"]
            )

            assert result == 1

    def test_connection_error_returns_1(self):
        """Test main returns 1 on connection error."""
        with mock.patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = acceptance_signal_api.urllib.error.URLError(
                "Connection refused"
            )

            result = acceptance_signal_api.main(
                ["--url", "https://vendor.example/webhook", "accept", "TXN-12345"]
            )

            assert result == 1

    def test_invalid_signal_exits_with_code_2(self):
        """Test main exits with code 2 on invalid signal (argparse error)."""
        # argparse exits with code 2 for invalid choices
        with pytest.raises(SystemExit) as exc_info:
            acceptance_signal_api.main(
                ["--url", "https://vendor.example/webhook", "invalid", "TXN-12345"]
            )

        assert exc_info.value.code == 2

    def test_quiet_flag_suppresses_output(self):
        """Test that -q flag suppresses output."""
        with mock.patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = mock.MagicMock()
            mock_response.status = 200
            mock_response.read.return_value = b'{"result":"accepted"}'
            mock_urlopen.return_value.__enter__.return_value = mock_response

            with mock.patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                result = acceptance_signal_api.main(
                    [
                        "--url",
                        "https://vendor.example/webhook",
                        "accept",
                        "TXN-12345",
                        "-q",
                    ]
                )

                assert result == 0
                assert mock_stdout.getvalue() == ""

    def test_non_2xx_status_returns_1(self):
        """Test that non-2xx status codes return 1."""
        with mock.patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = mock.MagicMock()
            mock_response.status = 500
            mock_response.read.return_value = b"Internal Server Error"
            mock_urlopen.return_value.__enter__.return_value = mock_response

            # Patch stderr to capture error output
            with mock.patch("sys.stderr", new_callable=StringIO) as _:
                result = acceptance_signal_api.main(
                    ["--url", "https://vendor.example/webhook", "accept", "TXN-12345"]
                )

            assert result == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
