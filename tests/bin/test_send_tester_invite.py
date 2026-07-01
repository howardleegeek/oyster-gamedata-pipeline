#!/usr/bin/env python3
"""
Tests for bin/send_tester_invite.py

Coverage:
- main: success path, 401 error, 404 error, 409 error, connection error, missing env var
- Argument parsing: tester_id, base-url
"""

from unittest import mock

import pytest

import bin.send_tester_invite as send_tester_invite


class TestMain:
    """Tests for main function."""

    def test_success_prints_email_ready_text(self, capsys):
        """Test successful approval prints email-ready text."""
        with mock.patch.object(send_tester_invite.httpx, "post") as mock_post:
            mock_response = mock.MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "download_url": "https://example.com/download/abc123",
                "tester_id": "tester-001",
            }
            mock_post.return_value = mock_response

            with mock.patch.dict("os.environ", {"TESTER_ADMIN_TOKEN": "secret-token"}):
                with mock.patch("sys.argv", ["send_tester_invite.py", "tester-001"]):
                    send_tester_invite.main()

            captured = capsys.readouterr()
            assert "EMAIL-READY TEXT" in captured.out
            assert "https://example.com/download/abc123" in captured.out
            assert "tester-001" in captured.out

    def test_success_calls_correct_endpoint(self):
        """Test that the correct API endpoint is called."""
        with mock.patch.object(send_tester_invite.httpx, "post") as mock_post:
            mock_response = mock.MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "download_url": "https://example.com/dl",
                "tester_id": "t123",
            }
            mock_post.return_value = mock_response

            with mock.patch.dict("os.environ", {"TESTER_ADMIN_TOKEN": "my-secret"}):
                with mock.patch("sys.argv", ["send_tester_invite.py", "t123"]):
                    send_tester_invite.main()

            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert "http://localhost:8500/api/v1/testers/t123/approve" in str(call_args)

    def test_success_custom_base_url(self):
        """Test custom base URL is used."""
        with mock.patch.object(send_tester_invite.httpx, "post") as mock_post:
            mock_response = mock.MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "download_url": "https://example.com/dl",
                "tester_id": "t123",
            }
            mock_post.return_value = mock_response

            with mock.patch.dict("os.environ", {"TESTER_ADMIN_TOKEN": "token"}):
                with mock.patch(
                    "sys.argv",
                    ["send_tester_invite.py", "t123", "--base-url", "https://custom.example.com"],
                ):
                    send_tester_invite.main()

            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert "https://custom.example.com" in str(call_args)

    def test_missing_admin_token_exits_with_error(self, capsys):
        """Test missing TESTER_ADMIN_TOKEN exits with code 1."""
        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch("sys.argv", ["send_tester_invite.py", "tester-001"]):
                with pytest.raises(SystemExit) as exc_info:
                    send_tester_invite.main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "TESTER_ADMIN_TOKEN" in captured.err

    def test_connection_error_exits_with_error(self, capsys):
        """Test connection error exits with code 1."""
        with mock.patch.object(send_tester_invite.httpx, "post") as mock_post:
            mock_post.side_effect = send_tester_invite.httpx.ConnectError("Connection failed")

            with mock.patch.dict("os.environ", {"TESTER_ADMIN_TOKEN": "token"}):
                with mock.patch("sys.argv", ["send_tester_invite.py", "tester-001"]):
                    with pytest.raises(SystemExit) as exc_info:
                        send_tester_invite.main()

            assert exc_info.value.code == 1
            captured = capsys.readouterr()
            assert "Could not connect" in captured.err

    def test_401_unauthorized_exits_with_error(self, capsys):
        """Test 401 response exits with code 1."""
        with mock.patch.object(send_tester_invite.httpx, "post") as mock_post:
            mock_response = mock.MagicMock()
            mock_response.status_code = 401
            mock_post.return_value = mock_response

            with mock.patch.dict("os.environ", {"TESTER_ADMIN_TOKEN": "bad-token"}):
                with mock.patch("sys.argv", ["send_tester_invite.py", "tester-001"]):
                    with pytest.raises(SystemExit) as exc_info:
                        send_tester_invite.main()

            assert exc_info.value.code == 1
            captured = capsys.readouterr()
            assert "Unauthorized" in captured.err

    def test_404_not_found_exits_with_error(self, capsys):
        """Test 404 response exits with code 1."""
        with mock.patch.object(send_tester_invite.httpx, "post") as mock_post:
            mock_response = mock.MagicMock()
            mock_response.status_code = 404
            mock_post.return_value = mock_response

            with mock.patch.dict("os.environ", {"TESTER_ADMIN_TOKEN": "token"}):
                with mock.patch("sys.argv", ["send_tester_invite.py", "nonexistent"]):
                    with pytest.raises(SystemExit) as exc_info:
                        send_tester_invite.main()

            assert exc_info.value.code == 1
            captured = capsys.readouterr()
            assert "not found" in captured.err

    def test_409_conflict_exits_with_error(self, capsys):
        """Test 409 response exits with code 1."""
        with mock.patch.object(send_tester_invite.httpx, "post") as mock_post:
            mock_response = mock.MagicMock()
            mock_response.status_code = 409
            mock_response.json.return_value = {"detail": "Already processed"}
            mock_post.return_value = mock_response

            with mock.patch.dict("os.environ", {"TESTER_ADMIN_TOKEN": "token"}):
                with mock.patch("sys.argv", ["send_tester_invite.py", "tester-001"]):
                    with pytest.raises(SystemExit) as exc_info:
                        send_tester_invite.main()

            assert exc_info.value.code == 1
            captured = capsys.readouterr()
            assert "Already processed" in captured.err

    def test_unexpected_status_exits_with_error(self, capsys):
        """Test unexpected status code exits with code 1."""
        with mock.patch.object(send_tester_invite.httpx, "post") as mock_post:
            mock_response = mock.MagicMock()
            mock_response.status_code = 500
            mock_response.text = "Internal Server Error"
            mock_post.return_value = mock_response

            with mock.patch.dict("os.environ", {"TESTER_ADMIN_TOKEN": "token"}):
                with mock.patch("sys.argv", ["send_tester_invite.py", "tester-001"]):
                    with pytest.raises(SystemExit) as exc_info:
                        send_tester_invite.main()

            assert exc_info.value.code == 1
            captured = capsys.readouterr()
            assert "500" in captured.err


class TestArgumentParsing:
    """Tests for argument parsing."""

    def test_tester_id_required(self):
        """Test that tester_id is a required positional argument."""
        # When no args provided, should fail
        with mock.patch("sys.argv", ["send_tester_invite.py"]):
            with pytest.raises(SystemExit):
                send_tester_invite.main()

    def test_tester_id_passed_correctly(self):
        """Test tester_id is passed to the API call."""
        with mock.patch.object(send_tester_invite.httpx, "post") as mock_post:
            mock_response = mock.MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "download_url": "https://example.com/dl",
                "tester_id": "my-tester-id",
            }
            mock_post.return_value = mock_response

            with mock.patch.dict("os.environ", {"TESTER_ADMIN_TOKEN": "token"}):
                with mock.patch("sys.argv", ["send_tester_invite.py", "my-tester-id"]):
                    send_tester_invite.main()

            call_args = str(mock_post.call_args)
            assert "my-tester-id" in call_args


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
