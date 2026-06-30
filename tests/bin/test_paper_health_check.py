"""Tests for bin/paper_health_check.py."""

import json
import socket
from io import BytesIO
from unittest import mock

import pytest

from bin.paper_health_check import (
    check_server,
    decode_varint,
    encode_varint,
    main,
)


class TestEncodeVarint:
    """Tests for encode_varint function."""

    def test_encode_zero(self):
        """Encode 0 returns single byte 0x00."""
        assert encode_varint(0) == b"\x00"

    def test_encode_single_byte(self):
        """Encode values 1-127 return single byte."""
        assert encode_varint(1) == b"\x01"
        assert encode_varint(127) == b"\x7f"

    def test_encode_two_bytes(self):
        """Encode 128 returns two bytes 0x80 0x01."""
        assert encode_varint(128) == b"\x80\x01"
        assert encode_varint(300) == b"\xac\x02"

    def test_encode_three_bytes(self):
        """Encode 16384 returns three bytes."""
        assert encode_varint(16384) == b"\x80\x80\x01"
        assert encode_varint(2097151) == b"\xff\xff\x7f"

    def test_encode_max_varint(self):
        """Encode max 32-bit signed int."""
        # Max VarInt for 32-bit is 0xffffffff (4294967295)
        result = encode_varint(4294967295)
        # Should be 5 bytes, all 0xff
        assert len(result) == 5
        assert result == b"\xff\xff\xff\xff\x0f"


class FakeSocket:
    """Fake socket for testing decode_varint."""

    def __init__(self, data: bytes):
        self._data = BytesIO(data)
        self._closed = False

    def recv(self, n):
        return self._data.read(n)

    def close(self):
        self._closed = True


class TestDecodeVarint:
    """Tests for decode_varint function."""

    def test_decode_zero(self):
        """Decode 0x00 returns 0."""
        sock = FakeSocket(b"\x00")
        assert decode_varint(sock) == 0

    def test_decode_single_byte(self):
        """Decode single byte varints."""
        sock = FakeSocket(b"\x7f")
        assert decode_varint(sock) == 127

    def test_decode_two_bytes(self):
        """Decode two byte varint 128."""
        sock = FakeSocket(b"\x80\x01")
        assert decode_varint(sock) == 128

    def test_decode_two_bytes_300(self):
        """Decode two byte varint 300."""
        sock = FakeSocket(b"\xac\x02")
        assert decode_varint(sock) == 300

    def test_decode_three_bytes(self):
        """Decode three byte varint 16384."""
        sock = FakeSocket(b"\x80\x80\x01")
        assert decode_varint(sock) == 16384


class TestCheckServer:
    """Tests for check_server function."""

    @pytest.fixture
    def mock_socket_class(self):
        """Mock socket class."""
        with mock.patch("bin.paper_health_check.socket.socket") as mock_sock:
            yield mock_sock

    def test_server_connection_failure(self, mock_socket_class):
        """Connection failure returns 1."""
        mock_socket_class.side_effect = socket.error("Connection refused")

        result = check_server("localhost", 25565)
        assert result == 1

    def test_server_timeout(self, mock_socket_class):
        """Connection timeout returns 1."""
        mock_socket_class.side_effect = socket.timeout("Timed out")

        result = check_server("localhost", 25565)
        assert result == 1

    def test_server_invalid_json(self, mock_socket_class):
        """Invalid JSON response returns 1."""
        mock_sock = mock.MagicMock()
        mock_socket_class.return_value = mock_sock

        # Build response: packet_length (varint) + packet_id (varint) + json_data
        json_bytes = b"not valid json"
        packet_id = encode_varint(0)
        payload = packet_id + json_bytes
        full_response = encode_varint(len(payload)) + payload

        # Create a side effect that returns data byte by byte
        data_iter = iter(full_response)

        def recv_side_effect(n):
            try:
                return next(data_iter) + b""  # Ensure we return bytes
            except StopIteration:
                return b""

        mock_sock.recv = recv_side_effect

        result = check_server("localhost", 25565)
        assert result == 1

    def test_server_version_mismatch(self, mock_socket_class):
        """Server returns wrong version, returns 1 (WARNING)."""
        mock_sock = mock.MagicMock()
        mock_socket_class.return_value = mock_sock

        # Build response with wrong version
        status = {"version": {"name": "Paper 1.19.2"}, "players": {"online": 0}}
        json_bytes = json.dumps(status).encode()
        packet_id = encode_varint(0)
        payload = packet_id + json_bytes
        full_response = encode_varint(len(payload)) + payload

        data_iter = iter(full_response)

        def recv_side_effect(n):
            try:
                return b"".join([next(data_iter) for _ in range(n)])
            except StopIteration:
                return b""

        mock_sock.recv = recv_side_effect

        result = check_server("localhost", 25565)
        assert result == 1


class TestMain:
    """Tests for main function."""

    def test_main_default_args(self):
        """main with default args calls check_server with localhost:25565."""
        with mock.patch("bin.paper_health_check.check_server") as mock_check:
            mock_check.return_value = 0
            with mock.patch("sys.argv", ["paper_health_check"]):
                try:
                    main()
                except SystemExit as e:
                    assert e.code == 0
            mock_check.assert_called_once_with("localhost", 25565)

    def test_main_custom_args(self):
        """main with custom args passes through host and port."""
        with mock.patch("bin.paper_health_check.check_server") as mock_check:
            mock_check.return_value = 0
            with mock.patch(
                "sys.argv", ["paper_health_check", "--host", "mc.example.com", "--port", "25577"]
            ):
                try:
                    main()
                except SystemExit as e:
                    assert e.code == 0
            mock_check.assert_called_once_with("mc.example.com", 25577)

    def test_main_exits_zero_on_success(self):
        """main exits with 0 when check_server returns 0."""
        with mock.patch("bin.paper_health_check.check_server", return_value=0):
            with pytest.raises(SystemExit) as exc_info:
                with mock.patch("sys.argv", ["paper_health_check"]):
                    main()
            assert exc_info.value.code == 0

    def test_main_exits_one_on_failure(self):
        """main exits with 1 when check_server returns non-zero."""
        with mock.patch("bin.paper_health_check.check_server", return_value=1):
            with pytest.raises(SystemExit) as exc_info:
                with mock.patch("sys.argv", ["paper_health_check"]):
                    main()
            assert exc_info.value.code == 1
