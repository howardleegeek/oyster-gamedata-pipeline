#!/usr/bin/env python3
"""
R001 · bin/mc_launcher_real.py — 启动真 Minecraft Java 1.20.4 客户端 (offline mode)

这是 PRD §11 STEP 7 的 launcher 实现。
启动 Minecraft Java Edition 客户端连接 localhost:25565 Paper 服务器，
自动切换 spectator gamemode。
"""

import argparse
import hashlib
import logging
import os
import socket
import struct
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def offline_uuid(username: str) -> str:
    """
    Generate Minecraft offline-mode UUID v3 from username (Java Notchian algorithm).

    The algorithm uses UUID.nameUUIDFromBytes("OfflinePlayer:" + username)
    which is equivalent to MD5 hashing of the UTF-8 bytes.

    Args:
        username: Minecraft username

    Returns:
        36-character UUID string (e.g., "12345678-1234-5678-1234-567812345678")
    """
    # Java's UUID.nameUUIDFromBytes uses MD5
    md5 = hashlib.md5()
    md5.update(f"OfflinePlayer:{username}".encode("utf-8"))
    md5_bytes = md5.digest()

    # Set version bits for UUID v3 (MD5-based)
    # Version 3: 0x30 in the 4th most significant byte
    md5_bytes_list = list(md5_bytes)
    md5_bytes_list[6] = (md5_bytes_list[6] & 0x0F) | 0x30  # Set version to 3
    md5_bytes_list[8] = (md5_bytes_list[8] & 0x3F) | 0x80  # Set variant to RFC 4122

    # Convert to UUID
    result_uuid = uuid.UUID(bytes=bytes(md5_bytes_list))
    return str(result_uuid)


def find_minecraft_launcher() -> str:
    """
    Locate Minecraft launcher executable (minecraft-launcher-lib OR system 'minecraft-launcher' binary).

    Returns:
        Path to launcher executable or command name

    Raises:
        RuntimeError: If no launcher can be found
    """
    # Check if minecraft-launcher-lib is available
    if "minecraft_launcher_lib" in sys.modules:
        return "minecraft-launcher-lib"

    import shutil

    # Check for 'minecraft-launcher' in PATH
    launcher_path = shutil.which("minecraft-launcher")
    if launcher_path:
        return launcher_path

    # Check common locations
    common_paths = [
        "/usr/bin/minecraft-launcher",
        "/usr/local/bin/minecraft-launcher",
        "/opt/minecraft-launcher/minecraft-launcher",
        os.path.expanduser("~/.local/bin/minecraft-launcher"),
    ]

    for path in common_paths:
        if os.path.exists(path) and os.access(path, os.X_OK):
            return path

    raise RuntimeError(
        "No Minecraft launcher found. Install minecraft-launcher-lib "
        "or ensure 'minecraft-launcher' binary is in PATH."
    )


def launch_minecraft(
    server_host: str = "localhost",
    server_port: int = 25565,
    username: str = "Spectator01",
    version: str = "1.20.4",
    java_xmx: str = "4G",
    duration_sec: Optional[float] = None,
    log_dir: str = "logs/mc",
) -> subprocess.Popen:
    """
    Boot Minecraft Java client in offline mode, auto-connect to server.

    Args:
        server_host: Server hostname/IP
        server_port: Server port
        username: Minecraft username
        version: Minecraft version
        java_xmx: Java heap size (e.g., "4G")
        duration_sec: Optional duration before killing process
        log_dir: Directory for log files

    Returns:
        subprocess.Popen object for the Minecraft process

    Raises:
        RuntimeError: If launch fails
        FileNotFoundError: If Java not found
    """
    # Create log directory
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    # Generate timestamp for log file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_path / f"mc_{timestamp}.log"

    # Generate offline UUID
    player_uuid = offline_uuid(username)

    try:
        launcher = find_minecraft_launcher()
    except RuntimeError as e:
        # Fallback to direct Java invocation
        import shutil

        java_path = shutil.which("java")
        if not java_path:
            raise FileNotFoundError("Java not found in PATH") from e

        # Construct Minecraft launch command
        # This is a simplified version - in production you'd need proper classpath
        cmd = [
            java_path,
            f"-Xmx{java_xmx}",
            "-cp",
            "libraries/*:client.jar",
            "net.minecraft.client.main.Main",
            "--username",
            username,
            "--uuid",
            player_uuid,
            "--version",
            version,
            "--gameDir",
            ".",
            "--assetsDir",
            "assets",
            "--assetIndex",
            "5",
            "--accessToken",
            "0",  # Offline mode
            "--userType",
            "legacy",
            "--versionType",
            "release",
            "--server",
            server_host,
            "--port",
            str(server_port),
        ]
    else:
        if launcher == "minecraft-launcher-lib":
            # Use minecraft-launcher-lib API
            import minecraft_launcher_lib

            options = {
                "username": username,
                "uuid": player_uuid,
                "token": "0",
                "jvmArguments": [f"-Xmx{java_xmx}"],
                "launcherVersion": "1.0.0",
                "customResolution": False,
            }

            # Get launch command from library
            minecraft_dir = minecraft_launcher_lib.utils.get_minecraft_directory()
            cmd = minecraft_launcher_lib.command.get_minecraft_command(
                version, minecraft_dir, options
            )

            # Add server connection arguments
            cmd.extend(["--server", server_host, "--port", str(server_port)])
        else:
            # Use system launcher binary
            cmd = [
                launcher,
                "--username",
                username,
                "--uuid",
                player_uuid,
                "--mc-version",
                version,
                "--server",
                server_host,
                "--port",
                str(server_port),
            ]

    # Start process with logging
    with open(log_file, "w") as f:
        f.write(f"Command: {' '.join(cmd)}\n")
        f.write(f"Timestamp: {timestamp}\n")
        f.write(f"Username: {username}\n")
        f.write(f"UUID: {player_uuid}\n")
        f.write("-" * 80 + "\n")
        f.flush()

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        # Start thread to capture output
        def capture_output():
            for line in process.stdout:
                f.write(line)
                f.flush()

        output_thread = threading.Thread(target=capture_output, daemon=True)
        output_thread.start()

    # Set up duration timer if specified
    if duration_sec:
        def kill_after_duration():
            time.sleep(duration_sec)
            if process.poll() is None:  # Still running
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()

        timer_thread = threading.Thread(target=kill_after_duration, daemon=True)
        timer_thread.start()

    return process


def wait_for_join(log_path: str, username: str, timeout_sec: float = 60.0) -> bool:
    """
    Poll log file for 'Connecting to <host>' or '<username> joined the game' line.

    Args:
        log_path: Path to log file
        username: Minecraft username to look for
        timeout_sec: Maximum time to wait

    Returns:
        True if join detected, False if timeout
    """
    start_time = time.time()
    log_file = Path(log_path)

    # Patterns to look for
    connect_pattern = "Connecting to"
    join_pattern = f"{username} joined the game"

    while time.time() - start_time < timeout_sec:
        if not log_file.exists():
            time.sleep(0.1)
            continue

        try:
            with open(log_file, "r") as f:
                content = f.read()

            if connect_pattern in content or join_pattern in content:
                return True
        except (IOError, OSError) as exc:
            logger.debug("log file read transient failure path=%s err=%s", log_file, exc)

        time.sleep(0.1)

    return False


def send_rcon_command(host: str, port: int, password: str, command: str) -> str:
    """
    Minimal RCON client (mcrcon protocol). Returns server response.

    Args:
        host: RCON server host
        port: RCON server port
        password: RCON password
        command: Command to send

    Returns:
        Server response as string

    Raises:
        ConnectionError: If connection fails
        RuntimeError: If authentication fails
    """
    # Create socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10.0)

    try:
        # Connect
        sock.connect((host, port))

        # Helper function to send packet
        def send_packet(request_id: int, packet_type: int, payload: str) -> None:
            payload_bytes = payload.encode("utf-8") + b"\x00\x00"
            length = len(payload_bytes) + 10  # 4 + 4 + 2
            packet = struct.pack("<iii", length, request_id, packet_type)
            packet += payload_bytes
            sock.sendall(packet)

        # Helper function to receive packet
        def recv_packet() -> tuple[int, int, str]:
            # Read length
            length_data = sock.recv(4)
            if len(length_data) < 4:
                raise ConnectionError("Incomplete packet")
            length = struct.unpack("<i", length_data)[0]

            # Read rest of packet
            remaining = length - 4
            packet_data = sock.recv(remaining)
            while len(packet_data) < remaining:
                chunk = sock.recv(remaining - len(packet_data))
                if not chunk:
                    break
                packet_data += chunk

            if len(packet_data) < 8:
                raise ConnectionError("Incomplete packet")

            request_id, packet_type = struct.unpack("<ii", packet_data[:8])
            # Payload ends with two null bytes
            if len(packet_data) > 10:
                payload = packet_data[8:-2].decode("utf-8", errors="replace")
            else:
                payload = ""
            return request_id, packet_type, payload

        # Authenticate
        send_packet(1, 3, password)  # 3 = SERVERDATA_AUTH
        
        # Receive auth response
        auth_id, auth_type, _ = recv_packet()

        if auth_id == -1:
            raise RuntimeError("RCON authentication failed")

        # Send command
        send_packet(2, 2, command)  # 2 = SERVERDATA_EXECCOMMAND
        
        # Receive command response
        cmd_id, cmd_type, response = recv_packet()

        return response

    finally:
        sock.close()


def main(argv: Optional[list[str]] = None) -> int:
    """
    CLI entry. Args: --server / --username / --gamemode / --duration / --log-dir

    Returns:
        Exit code (0 for success, non-zero for error)
    """
    parser = argparse.ArgumentParser(
        description="Launch Minecraft client in spectator mode"
    )
    parser.add_argument(
        "--server",
        default="localhost:25565",
        help="Server host:port (default: localhost:25565)",
    )
    parser.add_argument(
        "--username",
        default="Spectator01",
        help="Minecraft username (default: Spectator01)",
    )
    parser.add_argument(
        "--gamemode",
        default="spectator",
        choices=["spectator"],
        help="Gamemode (only spectator supported)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        help="Duration in seconds before killing client",
    )
    parser.add_argument(
        "--log-dir",
        default="logs/mc",
        help="Log directory (default: logs/mc)",
    )
    parser.add_argument(
        "--rcon-password",
        help="RCON password for gamemode switching",
    )
    parser.add_argument(
        "--rcon-port",
        type=int,
        default=25575,
        help="RCON port (default: 25575)",
    )
    parser.add_argument(
        "--version",
        default="1.20.4",
        help="Minecraft version (default: 1.20.4)",
    )
    parser.add_argument(
        "--java-xmx",
        default="4G",
        help="Java heap size (default: 4G)",
    )

    args = parser.parse_args(argv)

    # Parse server host:port
    if ":" in args.server:
        server_host, server_port_str = args.server.split(":", 1)
        try:
            server_port = int(server_port_str)
        except ValueError:
            print(f"Error: Invalid port number: {server_port_str}")
            return 1
    else:
        server_host = args.server
        server_port = 25565

    try:
        # Launch Minecraft
        print(f"Launching Minecraft {args.version} as {args.username}...")
        process = launch_minecraft(
            server_host=server_host,
            server_port=server_port,
            username=args.username,
            version=args.version,
            java_xmx=args.java_xmx,
            duration_sec=args.duration,
            log_dir=args.log_dir,
        )

        # Find the latest log file
        log_dir = Path(args.log_dir)
        log_files = sorted(log_dir.glob("mc_*.log"), key=os.path.getmtime, reverse=True)
        if not log_files:
            print("Error: No log file created")
            process.terminate()
            return 1

        latest_log = log_files[0]

        # Wait for join
        print("Waiting for client to join (timeout: 60s)...", end=" ")
        if wait_for_join(str(latest_log), args.username, timeout_sec=60.0):
            print("Client joined successfully")

            # Switch to spectator mode if RCON password provided
            if args.rcon_password:
                print("Switching to spectator mode via RCON...")
                try:
                    # Get player UUID
                    player_uuid = offline_uuid(args.username)

                    # Wait a bit for client to fully join
                    time.sleep(2)

                    # Send gamemode command
                    response = send_rcon_command(
                        host=server_host,
                        port=args.rcon_port,
                        password=args.rcon_password,
                        command=f"gamemode spectator {args.username}",
                    )
                    print(f"RCON response: {response}")

                    # Send spectate command
                    response = send_rcon_command(
                        host=server_host,
                        port=args.rcon_port,
                        password=args.rcon_password,
                        command=f"spectate {player_uuid} {args.username}",
                    )
                    print(f"RCON spectate response: {response}")

                except Exception as e:
                    print(f"Warning: RCON command failed: {e}")

            # Wait for process or duration
            if args.duration:
                print(f"Client will run for {args.duration} seconds...")
                try:
                    process.wait(timeout=args.duration)
                except subprocess.TimeoutExpired:
                    print("Duration expired, terminating client...")
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
            else:
                print("Client running. Press Ctrl+C to terminate.")
                try:
                    process.wait()
                except KeyboardInterrupt:
                    print("\nInterrupted, terminating client...")
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()

            return 0
        else:
            print("Error: Client failed to join within timeout")
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            return 1

    except FileNotFoundError as e:
        print(f"Error: {e}")
        return 1
    except RuntimeError as e:
        print(f"Error: {e}")
        return 1
    except Exception as e:
        print(f"Unexpected error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
