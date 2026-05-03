#!/usr/bin/env python3
"""
Tests for mc_launcher_real.py
"""

import argparse
import hashlib
import io
import json
import os
import socket
import struct
import subprocess
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
    
    # Mock shutil.which to return None
    with mock.patch("shutil.which", return_value=None):
        # Mock os.path.exists to return False for all paths
        with mock.patch("os.path.exists", return_value=False):
            with mock.patch("os.access", return_value=False):
                # Should raise RuntimeError
                with pytest.raises(RuntimeError, match="No Minecraft launcher found"):
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
    # Mock socket
    mock_sock = mock.MagicMock()
    mock_sock.recv.side_effect = [
        # First recv: length (4 bytes) for auth response
        struct.pack("<i", 12),  # Length = 12
        # Second recv: auth response packet
        struct.pack("<ii", 1, 2) + b"auth\x00\x00",  # request_id=1, type=2, payload="auth"
        # Third recv: length for command response
        struct.pack("<i", 20),  # Length = 20
        # Fourth recv: command response packet
        struct.pack("<ii", 2, 0) + b"Command output\x00\x00",  # request_id=2, type=0
    ]
    
    with mock.patch("socket.socket", return_value=mock_sock):
        # Capture sent data
        sent_data = []
        original_sendall = mock_sock.sendall
        
        def capture_sendall(data):
            sent_data.append(data)
            return original_sendall(data)
        
        mock_sock.sendall = capture_sendall
        
        # Send RCON command
        response = mc_launcher_real.send_rcon_command(
            host="localhost",
            port=25575,
            password="testpass",
            command="test command"
        )
        
        # Verify response
        assert response == "Command output"
        
        # Verify packets were sent
        assert len(sent_data) == 2  # Auth packet + command packet
        
        # Verify auth packet structure
        auth_packet = sent_data[0]
        # Length (4) + request_id (4) + type (4) + payload + 2 null bytes
        assert len(auth_packet) >= 10
        # Check type is 3 (SERVERDATA_AUTH)
        auth_type = struct.unpack("<i", auth_packet[4:8])[0]
        assert auth_type == 3
        
        # Verify command packet structure
        cmd_packet = sent_data[1]
        # Check type is 2 (SERVERDATA_EXECCOMMAND)
        cmd_type = struct.unpack("<i", cmd_packet[4:8])[0]
        assert cmd_type == 2


def test_rcon_command_auth_failure() -> None:
    """Test RCON authentication failure."""
    # Mock socket
    mock_sock = mock.MagicMock()
    mock_sock.recv.side_effect = [
        # Auth response with request_id = -1 (auth failure)
        struct.pack("<i", 12),  # Length
        struct.pack("<ii", -1, 2) + b"auth\x00\x00",  # request_id=-1 indicates failure
    ]
    
    with mock.patch("socket.socket", return_value=mock_sock):
        with pytest.raises(RuntimeError, match="RCON authentication failed"):
            mc_launcher_real.send_rcon_command(
                host="localhost",
                port=25575,
                password="wrongpass",
                command="test"
            )


def test_rcon_command_connection_error() -> None:
    """Test RCON connection failure."""
    with mock.patch("socket.socket") as mock_socket_class:
        mock_sock = mock.MagicMock()
        mock_sock.connect.side_effect = ConnectionRefusedError("Connection refused")
        mock_socket_class.return_value = mock_sock
        
        with pytest.raises(ConnectionError):
            mc_launcher_real.send_rcon_command(
                host="localhost",
                port=25575,
                password="testpass",
                command="test"
            )


def test_main_argparse() -> None:
    """Test argparse argument parsing."""
    # Test default values
    with mock.patch("sys.argv", ["mc_launcher_real.py"]):
        with mock.patch("mc_launcher_real.launch_minecraft") as mock_launch:
            mock_process = mock.MagicMock()
            mock_process.poll.return_value = None
            mock_process.wait.return_value = 0
            mock_launch.return_value = mock_process
            
            with mock.patch("mc_launcher_real.wait_for_join", return_value=True):
                with mock.patch("pathlib.Path.glob") as mock_glob:
                    mock_file = mock.MagicMock()
                    mock_file.__gt__ = mock.MagicMock(return_value=True)  # For sorting
                    mock_glob.return_value = [mock_file]
                    
                    result = mc_launcher_real.main([])
    
    # Test with custom arguments
    test_args = [
        "--server", "example.com:25566",
        "--username", "TestPlayer",
        "--gamemode", "spectator",
        "--duration", "300",
        "--log-dir", "/custom/logs",
        "--rcon-password", "secret",
        "--rcon-port", "25576",
        "--version", "1.19.4",
        "--java-xmx", "2G"
    ]
    
    with mock.patch("sys.argv", ["mc_launcher_real.py"] + test_args):
        with mock.patch("mc_launcher_real.launch_minecraft") as mock_launch:
            mock_process = mock.MagicMock()
            mock_process.poll.return_value = None
            mock_process.wait.return_value = 0
            mock_launch.return_value = mock_process
            
            with mock.patch("mc_launcher_real.wait_for_join", return_value=True):
                with mock.patch("mc_launcher_real.send_rcon_command") as mock_rcon:
                    # Mock log file finding
                    with mock.patch("pathlib.Path.glob") as mock_glob:
                        mock_file = mock.MagicMock()
                        mock_file.__gt__ = mock.MagicMock(return_value=True)
                        mock_glob.return_value = [mock_file]
                        
                        with mock.patch("mc_launcher_real.offline_uuid", return_value="test-uuid"):
                            result = mc_launcher_real.main(test_args)


def test_launch_minecraft_fallback() -> None:
    """Test fallback to direct Java invocation when launcher not found."""
    # Mock find_minecraft_launcher to raise RuntimeError
    with mock.patch("mc_launcher_real.find_minecraft_launcher", side_effect=RuntimeError("No launcher")):
        # Mock shutil.which to return a Java path
        with mock.patch("shutil.which", return_value="/usr/bin/java"):
            # Mock subprocess.Popen
            with mock.patch("subprocess.Popen") as mock_popen:
                mock_process = mock.MagicMock()
                mock_process.stdout = io.StringIO()
                mock_popen.return_value = mock_process
                
                # Mock threading.Thread
                with mock.patch("threading.Thread"):
                    # Mock Path.mkdir to avoid directory creation
                    with mock.patch("pathlib.Path.mkdir"):
                        # Mock open for log file
                        with mock.patch("builtins.open", mock.mock_open()):
                            process = mc_launcher_real.launch_minecraft()
                            
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
    
    with mock.patch.dict(sys.modules, {"minecraft_launcher_lib": mock_lib}):
        with mock.patch("mc_launcher_real.find_minecraft_launcher", return_value="minecraft-launcher-lib"):
            with mock.patch("subprocess.Popen") as mock_popen:
                mock_process = mock.MagicMock()
                mock_process.stdout = io.StringIO()
                mock_popen.return_value = mock_process
                
                with mock.patch("threading.Thread"):
                    with mock.patch("pathlib.Path.mkdir"):
                        with mock.patch("builtins.open", mock.mock_open()):
                            process = mc_launcher_real.launch_minecraft()
                            
                            # Should have used the library
                            mock_lib.command.get_minecraft_command.assert_called_once()
                            mock_popen.assert_called_once()


def test_launch_minecraft_with_system_launcher() -> None:
    """Test using system launcher binary."""
    with mock.patch("mc_launcher_real.find_minecraft_launcher", return_value="/usr/bin/minecraft-launcher"):
        with mock.patch("subprocess.Popen") as mock_popen:
            mock_process = mock.MagicMock()
            mock_process.stdout = io.StringIO()
            mock_popen.return_value = mock_process
            
            with mock.patch("threading.Thread"):
                with mock.patch("pathlib.Path.mkdir"):
                    with mock.patch("builtins.open", mock.mock_open()):
                        process = mc_launcher_real.launch_minecraft()
                        
                        # Should have called Popen with system launcher
                        mock_popen.assert_called_once()
                        call_args = mock_popen.call_args[0][0]
                        assert call_args[0] == "/usr/bin/minecraft-launcher"


def test_launch_minecraft_java_not_found() -> None:
    """Test error when Java not found in fallback mode."""
    # Mock find_minecraft_launcher to raise RuntimeError
    with mock.patch("mc_launcher_real.find_minecraft_launcher", side_effect=RuntimeError("No launcher")):
        # Mock shutil.which to return None (Java not found)
        with mock.patch("shutil.which", return_value=None):
            with pytest.raises(FileNotFoundError, match="Java not found"):
                mc_launcher_real.launch_minecraft()


def test_main_client_fails_to_join() -> None:
    """Test main when client fails to join."""
    with mock.patch("mc_launcher_real.launch_minecraft") as mock_launch:
        mock_process = mock.MagicMock()
        mock_process.poll.return_value = None
        mock_process.terminate.return_value = None
        mock_process.wait.return_value = 0
        mock_launch.return_value = mock_process
        
        with mock.patch("mc_launcher_real.wait_for_join", return_value=False):
            with mock.patch("pathlib.Path.glob") as mock_glob:
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
    with mock.patch("mc_launcher_real.launch_minecraft") as mock_launch:
        mock_process = mock.MagicMock()
        mock_process.poll.return_value = None
        mock_process.wait.return_value = 0
        mock_launch.return_value = mock_process
        
        with mock.patch("mc_launcher_real.wait_for_join", return_value=True):
            with mock.patch("pathlib.Path.glob") as mock_glob:
                mock_file = mock.MagicMock()
                mock_file.__gt__ = mock.MagicMock(return_value=True)
                mock_glob.return_value = [mock_file]
                
                with mock.patch("mc_launcher_real.offline_uuid", return_value="test-uuid-1234"):
                    with mock.patch("mc_launcher_real.send_rcon_command") as mock_rcon:
                        mock_rcon.return_value = "OK"
                        
                        with mock.patch("time.sleep"):
                            result = mc_launcher_real.main([
                                "--rcon-password", "testpass",
                                "--duration", "10"
                            ])
                            
                            # Should have sent RCON commands
                            assert mock_rcon.call_count == 2
                            
                            # First call: gamemode command
                            first_call = mock_rcon.call_args_list[0]
                            assert first_call[0][2] == "testpass"  # password
                            assert "gamemode spectator" in first_call[0][3]  # command
                            
                            # Second call: spectate command
                            second_call = mock_rcon.call_args_list[1]
                            assert "spectate test-uuid-1234" in second_call[0][3]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])