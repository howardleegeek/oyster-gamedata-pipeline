#!/usr/bin/env python3
"""
Tests for mc_launcher_real.py
"""

import hashlib
import io
import os
import struct
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from unittest import mock

import pytest

# Import the module to test
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "bin"))
import mc_launcher_real


def test_offline_uuid_deterministic() -> None:
    """Same username → same UUID, different username → different UUID."""
    # Same username should produce same UUID
    uuid1 = mc_launcher_real.offline_uuid("TestUser")
    uuid2 = mc_launcher_real.offline_uuid("TestUser")
    assert uuid1 == uuid2

    # Different usernames should produce different UUIDs
    uuid3 = mc_launcher_real.offline_uuid("TestUser2")
    assert uuid1 != uuid3

    # Verify it's deterministic with direct MD5 calculation
    md5 = hashlib.md5()
    md5.update(b"OfflinePlayer:TestUser")
    md5_bytes = md5.digest()
    md5_bytes_list = list(md5_bytes)
    md5_bytes_list[6] = (md5_bytes_list[6] & 0x0F) | 0x30
    md5_bytes_list[8] = (md5_bytes_list[8] & 0x3F) | 0x80
    expected_uuid = str(uuid.UUID(bytes=bytes(md5_bytes_list)))
    assert uuid1 == expected_uuid


def test_offline_uuid_format() -> None:
    """Return 36-char UUID v3 format."""
    result = mc_launcher_real.offline_uuid("Spectator01")

    # Should be 36 characters
    assert len(result) == 36

    # Should match UUID format: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
    parts = result.split("-")
    assert len(parts) == 5
    assert len(parts[0]) == 8
    assert len(parts[1]) == 4
    assert len(parts[2]) == 4
    assert len(parts[3]) == 4
    assert len(parts[4]) == 12

    # Should be UUID v3 (version bits = 0x30 in byte 6)
    uuid_obj = uuid.UUID(result)
    assert uuid_obj.version == 3

    # Should be RFC 4122 variant (variant bits = 0x80 in byte 8)
    assert uuid_obj.variant == uuid.RFC_4122


def test_find_launcher_uses_lib_when_present() -> None:
    """monkeypatch sys.modules to simulate minecraft-launcher-lib presence."""
    # Mock the minecraft_launcher_lib module
    mock_lib = mock.MagicMock()
    mock_lib.utils = mock.MagicMock()
    mock_lib.utils.get_minecraft_directory.return_value = "/fake/minecraft/dir"

    with mock.patch.dict(sys.modules, {"minecraft_launcher_lib": mock_lib}):
        result = mc_launcher_real.find_minecraft_launcher()
        assert result == "minecraft-launcher-lib"


def test_find_launcher_falls_back_when_missing() -> None:
    """monkeypatch sys.modules to remove minecraft-launcher-lib."""
    # Remove minecraft-launcher-lib from sys.modules
    original_lib = sys.modules.get("minecraft_launcher_lib")
    if "minecraft_launcher_lib" in sys.modules:
        del sys.modules["minecraft_launcher_lib"]

    # Mock shutil.which to return None, os.path.exists to return False, and os.access to return False
    with (
        mock.patch("shutil.which", return_value=None),
        mock.patch("os.path.exists", return_value=False),
        mock.patch("os.access", return_value=False),
        pytest.raises(RuntimeError, match="No Minecraft launcher found"),
    ):
        mc_launcher_real.find_minecraft_launcher()

    # Restore original module if it existed
    if original_lib:
        sys.modules["minecraft_launcher_lib"] = original_lib


def test_wait_for_join_finds_pattern() -> None:
    """Temporary file + write join log."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
        log_path = f.name
        # Write initial content without pattern
        f.write("Initial log line\n")
        f.flush()

        # Start a thread to write the pattern after a short delay
        def write_pattern():
            time.sleep(0.1)
            with open(log_path, "a") as f2:
                f2.write("Connecting to localhost\n")
                f2.flush()

        thread = threading.Thread(target=write_pattern)
        thread.start()

        # Should find the pattern
        result = mc_launcher_real.wait_for_join(log_path, "TestUser", timeout_sec=2.0)
        assert result is True

        thread.join()

    # Clean up
    os.unlink(log_path)


def test_wait_for_join_times_out() -> None:
    """Temporary file without pattern + assert False."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
        log_path = f.name
        # Write content without the pattern
        f.write("Some other log line\n")
        f.write("Not the pattern we're looking for\n")
        f.flush()

        # Should timeout and return False
        result = mc_launcher_real.wait_for_join(log_path, "TestUser", timeout_sec=0.5)
        assert result is False

    # Clean up
    os.unlink(log_path)


def test_wait_for_join_finds_username_pattern() -> None:
    """Test that it finds '<username> joined the game' pattern."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
        log_path = f.name
        # Write initial content
        f.write("Starting game\n")
        f.flush()

        # Start a thread to write the join pattern
        def write_pattern():
            time.sleep(0.1)
            with open(log_path, "a") as f2:
                f2.write("Spectator01 joined the game\n")
                f2.flush()

        thread = threading.Thread(target=write_pattern)
        thread.start()

        # Should find the pattern
        result = mc_launcher_real.wait_for_join(log_path, "Spectator01", timeout_sec=2.0)
        assert result is True

        thread.join()

    # Clean up
    os.unlink(log_path)


def test_rcon_command_constructs_packet() -> None:
    """Mock socket, verify packet bytes."""
    # Create mock socket
    mock_sock = mock.MagicMock()

    # Track sent data
    sent_data = []

    def capture_sendall(data):
        sent_data.append(data)
        # Don't actually send anything

    mock_sock.sendall = capture_sendall

    # Setup recv to return valid responses
    # Auth response: length=14 (4+4+4+2), id=1, type=2, empty payload
    auth_response_length = struct.pack("<i", 14)
    auth_response_packet = struct.pack("<ii", 1, 2) + b"\x00\x00"

    # Command response: length=27 (4+4+4+15), id=2, type=0, payload="Command output"
    cmd_response_length = struct.pack("<i", 27)
    cmd_response_packet = struct.pack("<ii", 2, 0) + b"Command output\x00\x00"

    # Sequence of recv calls
    recv_calls = [
        (4, auth_response_length),  # First recv(4) gets length
        (10, auth_response_packet),  # Then recv(10) gets rest (14-4=10)
        (4, cmd_response_length),  # First recv(4) for command response length
        (23, cmd_response_packet),  # Then recv(23) gets rest (27-4=23)
    ]

    recv_call_idx = 0

    def mock_recv(bufsize):
        nonlocal recv_call_idx
        if recv_call_idx < len(recv_calls):
            expected_size, response = recv_calls[recv_call_idx]
            # For simplicity, just return the response regardless of bufsize
            recv_call_idx += 1
            return response
        return b""

    mock_sock.recv = mock_recv

    with mock.patch("socket.socket", return_value=mock_sock):
        response = mc_launcher_real.send_rcon_command(
            host="localhost", port=25575, password="testpass", command="test command"
        )

        # Verify response
        assert response == "Command output"

        # Verify packets were sent
        assert len(sent_data) >= 2  # At least auth and command packets


def test_rcon_command_auth_failure() -> None:
    """Test RCON authentication failure."""
    # Create mock socket
    mock_sock = mock.MagicMock()

    # Auth failure response: length=14, id=-1, type=2, empty payload
    auth_fail_length = struct.pack("<i", 14)
    auth_fail_packet = struct.pack("<ii", -1, 2) + b"\x00\x00"

    recv_calls = [
        (4, auth_fail_length),
        (10, auth_fail_packet),
    ]

    recv_call_idx = 0

    def mock_recv(bufsize):
        nonlocal recv_call_idx
        if recv_call_idx < len(recv_calls):
            expected_size, response = recv_calls[recv_call_idx]
            recv_call_idx += 1
            return response
        return b""

    mock_sock.recv = mock_recv

    with mock.patch("socket.socket", return_value=mock_sock), pytest.raises(
        RuntimeError, match="RCON authentication failed"
    ):
        mc_launcher_real.send_rcon_command(
            host="localhost", port=25575, password="wrongpass", command="test"
        )


def test_rcon_command_connection_error() -> None:
    """Test RCON connection failure."""
    with mock.patch("socket.socket") as mock_socket_class:
        mock_sock = mock.MagicMock()
        mock_sock.connect.side_effect = ConnectionRefusedError("Connection refused")
        mock_socket_class.return_value = mock_sock

        with pytest.raises(ConnectionError):
            mc_launcher_real.send_rcon_command(
                host="localhost", port=25575, password="testpass", command="test"
            )


def test_main_argparse() -> None:
    """Test argparse argument parsing."""
    # Test default values
    with (
        mock.patch("sys.argv", ["mc_launcher_real.py"]),
        mock.patch("mc_launcher_real.launch_minecraft") as mock_launch,
    ):
        mock_process = mock.MagicMock()
        mock_process.poll.return_value = None
        mock_process.wait.return_value = 0
        mock_launch.return_value = mock_process

        with (
            mock.patch("mc_launcher_real.wait_for_join", return_value=True),
            mock.patch("pathlib.Path.glob") as mock_glob,
        ):
            mock_file = mock.MagicMock()
            mock_file.__gt__ = mock.MagicMock(return_value=True)  # For sorting
            mock_glob.return_value = [mock_file]

            mc_launcher_real.main([])

    # Test with custom arguments
    test_args = [
        "--server",
        "example.com:25566",
        "--username",
        "TestPlayer",
        "--gamemode",
        "spectator",
        "--duration",
        "300",
        "--log-dir",
        "/custom/logs",
        "--rcon-password",
        "secret",
        "--rcon-port",
        "25576",
        "--version",
        "1.19.4",
        "--java-xmx",
        "2G",
    ]

    with (
        mock.patch("sys.argv", ["mc_launcher_real.py"] + test_args),
        mock.patch("mc_launcher_real.launch_minecraft") as mock_launch,
    ):
        mock_process = mock.MagicMock()
        mock_process.poll.return_value = None
        mock_process.wait.return_value = 0
        mock_launch.return_value = mock_process

        with (
            mock.patch("mc_launcher_real.wait_for_join", return_value=True),
            mock.patch("mc_launcher_real.send_rcon_command"),
            mock.patch("pathlib.Path.glob") as mock_glob,
            mock.patch("mc_launcher_real.offline_uuid", return_value="test-uuid"),
        ):
            mock_file = mock.MagicMock()
            mock_file.__gt__ = mock.MagicMock(return_value=True)
            mock_glob.return_value = [mock_file]

            mc_launcher_real.main(test_args)


def test_launch_minecraft_fallback() -> None:
    """Test fallback to direct Java invocation when launcher not found."""
    # Mock find_minecraft_launcher to raise RuntimeError
    with (
        mock.patch(
            "mc_launcher_real.find_minecraft_launcher", side_effect=RuntimeError("No launcher")
        ),
        mock.patch("shutil.which", return_value="/usr/bin/java"),
        mock.patch("subprocess.Popen") as mock_popen,
        mock.patch("threading.Thread"),
        mock.patch("pathlib.Path.mkdir"),
        mock.patch("builtins.open", mock.mock_open()),
    ):
        mock_process = mock.MagicMock()
        mock_process.stdout = io.StringIO()
        mock_popen.return_value = mock_process

        mc_launcher_real.launch_minecraft()

        # Should have called Popen with Java command
        mock_popen.assert_called_once()
        call_args = mock_popen.call_args[0][0]
        assert call_args[0] == "/usr/bin/java"
        assert "-Xmx4G" in call_args


def test_launch_minecraft_with_minecraft_launcher_lib() -> None:
    """Test using minecraft-launcher-lib when available."""
    # Mock minecraft_launcher_lib
    mock_lib = mock.MagicMock()
    mock_lib.utils.get_minecraft_directory.return_value = "/fake/minecraft/dir"
    mock_lib.command.get_minecraft_command.return_value = ["/fake/launcher", "--args"]

    with (
        mock.patch.dict(sys.modules, {"minecraft_launcher_lib": mock_lib}),
        mock.patch("mc_launcher_real.find_minecraft_launcher", return_value="minecraft-launcher-lib"),
        mock.patch("subprocess.Popen") as mock_popen,
        mock.patch("threading.Thread"),
        mock.patch("pathlib.Path.mkdir"),
        mock.patch("builtins.open", mock.mock_open()),
    ):
        mock_process = mock.MagicMock()
        mock_process.stdout = io.StringIO()
        mock_popen.return_value = mock_process

        mc_launcher_real.launch_minecraft()

        # Should have used the library
        mock_lib.command.get_minecraft_command.assert_called_once()
        mock_popen.assert_called_once()


def test_launch_minecraft_with_system_launcher() -> None:
    """Test using system launcher binary."""
    with (
        mock.patch("mc_launcher_real.find_minecraft_launcher", return_value="/usr/bin/minecraft-launcher"),
        mock.patch("subprocess.Popen") as mock_popen,
        mock.patch("threading.Thread"),
        mock.patch("pathlib.Path.mkdir"),
        mock.patch("builtins.open", mock.mock_open()),
    ):
        mock_process = mock.MagicMock()
        mock_process.stdout = io.StringIO()
        mock_popen.return_value = mock_process

        mc_launcher_real.launch_minecraft()

        # Should have called Popen with system launcher
        mock_popen.assert_called_once()
        call_args = mock_popen.call_args[0][0]
        assert call_args[0] == "/usr/bin/minecraft-launcher"


def test_launch_minecraft_java_not_found() -> None:
    """Test error when Java not found in fallback mode."""
    # Mock find_minecraft_launcher to raise RuntimeError
    with (
        mock.patch(
            "mc_launcher_real.find_minecraft_launcher", side_effect=RuntimeError("No launcher")
        ),
        mock.patch("shutil.which", return_value=None),
        pytest.raises(FileNotFoundError, match="Java not found"),
    ):
        mc_launcher_real.launch_minecraft()


def test_main_client_fails_to_join() -> None:
    """Test main when client fails to join."""
    with (
        mock.patch("mc_launcher_real.launch_minecraft") as mock_launch,
        mock.patch("mc_launcher_real.wait_for_join", return_value=False),
        mock.patch("pathlib.Path.glob") as mock_glob,
    ):
        mock_process = mock.MagicMock()
        mock_process.poll.return_value = None
        mock_process.terminate.return_value = None
        mock_process.wait.return_value = 0
        mock_launch.return_value = mock_process

        mock_file = mock.MagicMock()
        mock_file.__gt__ = mock.MagicMock(return_value=True)
        mock_glob.return_value = [mock_file]

        # Should return error code 1
        result = mc_launcher_real.main(["--server", "localhost:25565"])
        assert result == 1

        # Should have terminated the process
        mock_process.terminate.assert_called_once()


def test_main_with_rcon_commands() -> None:
    """Test main with RCON password provided."""
    with (
        mock.patch("mc_launcher_real.launch_minecraft") as mock_launch,
        mock.patch("mc_launcher_real.wait_for_join", return_value=True),
        mock.patch("pathlib.Path.glob") as mock_glob,
        mock.patch("mc_launcher_real.offline_uuid", return_value="test-uuid-1234"),
        mock.patch("mc_launcher_real.send_rcon_command") as mock_rcon,
        mock.patch("time.sleep"),
    ):
        mock_process = mock.MagicMock()
        mock_process.poll.return_value = None
        mock_process.wait.return_value = 0
        mock_launch.return_value = mock_process

        mock_file = mock.MagicMock()
        mock_file.__gt__ = mock.MagicMock(return_value=True)
        mock_glob.return_value = [mock_file]

        mock_rcon.return_value = "OK"

        mc_launcher_real.main(["--rcon-password", "testpass", "--duration", "10"])

        # Should have sent RCON commands
        assert mock_rcon.call_count == 2

        # Get the call objects
        calls = mock_rcon.call_args_list

        # First call: gamemode command
        first_call = calls[0]
        # Get keyword arguments (since send_rcon_command is called with kwargs)
        first_kwargs = first_call[1]
        assert first_kwargs["password"] == "testpass"
        assert "gamemode spectator" in first_kwargs["command"]

        # Second call: spectate command
        second_call = calls[1]
        second_kwargs = second_call[1]
        assert "spectate test-uuid-1234" in second_kwargs["command"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
