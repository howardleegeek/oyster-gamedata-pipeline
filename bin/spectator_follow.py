#!/usr/bin/env python3
"""
RCON-based spectator follow script for Minecraft.
Continuously sends /spectate commands to keep a spectator following a bot.
"""

import argparse
import logging
import signal
import socket
import struct
import sys
import time
from enum import IntEnum
from typing import Optional

logger = logging.getLogger(__name__)


class PacketType(IntEnum):
    """RCON packet types."""
    RESPONSE = 0
    AUTH = 3
    COMMAND = 2


class RconClient:
    """Minimal RCON client (Source RCON protocol, packet types 3=auth, 2=cmd, 0=resp)."""

    def __init__(self, host: str, port: int, password: str, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.password = password
        self.timeout = timeout
        self.sock: Optional[socket.socket] = None
        self.request_id = 1

    def __enter__(self) -> "RconClient":
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()

    def connect(self):
        """Establish connection to RCON server."""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect((self.host, self.port))

    def close(self):
        """Close the connection."""
        if self.sock:
            self.sock.close()
            self.sock = None

    def _send_packet(self, packet_type: PacketType, body: str) -> int:
        """Send a packet and return the packet ID."""
        if not self.sock:
            raise RuntimeError("Not connected")

        packet_id = self.request_id
        self.request_id += 1

        # Build packet: length + id + type + body + null terminators
        body_bytes = body.encode('utf-8') + b'\x00'
        packet = struct.pack('<iii',
                            len(body_bytes) + 10,  # length of id(4) + type(4) + body + nulls
                            packet_id,
                            packet_type) + body_bytes

        self.sock.sendall(packet)
        return packet_id

    def _receive_packet(self) -> tuple[int, PacketType, str]:
        """Receive a packet and return (id, type, body)."""
        if not self.sock:
            raise RuntimeError("Not connected")

        # Read length (4 bytes, little-endian)
        length_data = self.sock.recv(4)
        if len(length_data) < 4:
            raise socket.error("Connection closed")

        length = struct.unpack('<i', length_data)[0]

        # Read the rest of the packet
        packet_data = self.sock.recv(length)
        if len(packet_data) < length:
            raise socket.error("Connection closed")

        # Parse packet: id(4) + type(4) + body + null terminators
        packet_id = struct.unpack('<i', packet_data[0:4])[0]
        packet_type = struct.unpack('<i', packet_data[4:8])[0]

        # Body is everything after type, minus 2 null terminators
        body = packet_data[8:-2].decode('utf-8', errors='ignore')

        return packet_id, PacketType(packet_type), body

    def authenticate(self) -> bool:
        """Authenticate with RCON server. Returns True on success."""
        try:
            packet_id = self._send_packet(PacketType.AUTH, self.password)
            # Receive auth response
            response_id, response_type, _ = self._receive_packet()

            # Also receive the empty command response
            try:
                self._receive_packet()
            except Exception as e:
                # Best-effort drain of the trailing empty response — preserve
                # original semantics (fall through to response_id check) so
                # auth still succeeds/fails by the first response. We just
                # log the swallow so future protocol drift is visible.
                logger.debug(
                    "authenticate: trailing _receive_packet() failed; "
                    "falling through to response_id check: %s",
                    e,
                    exc_info=True,
                )

            # Auth succeeds if response ID matches our packet ID
            # Auth fails if response ID is -1
            return response_id == packet_id
        except (socket.error, struct.error) as e:
            logging.error(f"Authentication failed: {e}")
            return False

    def send(self, cmd: str) -> str:
        """Send a command and return the response."""
        if not self.sock:
            raise RuntimeError("Not connected")

        self._send_packet(PacketType.COMMAND, cmd)

        # Receive response
        response_id, response_type, body = self._receive_packet()

        # Multi-packet response handling (not needed for simple commands)
        return body


def get_player_uuid(rcon: RconClient, username: str) -> str | None:
    """Send 'data get entity <username> UUID' or use /list to find UUID."""
    try:
        # Try data get entity command first
        response = rcon.send(f"data get entity {username} UUID")

        # Parse response like: "DataPilot has the following entity data: [I; -123456789, -987654321, 1234567890, -1234567890]"
        # Or: "DataPilot has UUID: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
        if "UUID" in response:
            # Look for UUID pattern
            import re
            uuid_pattern = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
            match = re.search(uuid_pattern, response, re.IGNORECASE)
            if match:
                return match.group(0)

        # Fallback to /list command
        response = rcon.send("list")
        if username in response:
            # /list returns: "There are 2/20 players online: DataPilot, Spectator01"
            # We can't get UUID from this, but we can at least confirm player exists
            # For now, return None and let spectate command handle it
            pass

    except Exception as e:
        logging.error(f"Failed to get UUID for {username}: {e}")

    return None


def spectate_loop(
    rcon: RconClient,
    bot_username: str,
    spectator_username: str,
    interval_sec: float = 5.0,
    duration_sec: float | None = None,
) -> int:
    """Every interval_sec, re-send /spectate command to keep spectator locked on bot.
    Returns total spectate commands sent."""

    logging.info(f"Starting spectate loop: {spectator_username} -> {bot_username}")
    logging.info(f"Interval: {interval_sec}s, Duration: {'infinite' if duration_sec is None else f'{duration_sec}s'}")

    start_time = time.time()
    commands_sent = 0
    consecutive_failures = 0

    # Setup signal handler for graceful shutdown
    stop_requested = False

    def signal_handler(sig, frame):
        nonlocal stop_requested
        logging.info("Received interrupt signal, stopping...")
        stop_requested = True

    signal.signal(signal.SIGINT, signal_handler)

    try:
        while not stop_requested:
            # Check duration if specified
            if duration_sec is not None:
                elapsed = time.time() - start_time
                if elapsed >= duration_sec:
                    logging.info(f"Duration {duration_sec}s reached, stopping")
                    break

            # Send spectate command with retries
            success = False
            for attempt in range(3):  # 3 retries
                try:
                    # Exponential backoff: 1s, 2s, 4s
                    if attempt > 0:
                        backoff = 2 ** (attempt - 1)
                        logging.debug(f"Retry {attempt} in {backoff}s...")
                        time.sleep(backoff)

                    response = rcon.send(f"spectate {bot_username} {spectator_username}")
                    commands_sent += 1
                    consecutive_failures = 0
                    success = True

                    if "Unknown" in response or "not found" in response.lower():
                        logging.warning(f"Command may have failed: {response}")
                    else:
                        logging.debug(f"Spectate command sent ({commands_sent} total)")

                    break

                except Exception as e:
                    logging.warning(f"Attempt {attempt + 1} failed: {e}")

            if not success:
                consecutive_failures += 1
                logging.error(f"Failed to send spectate command (consecutive failures: {consecutive_failures})")

                if consecutive_failures >= 5:
                    logging.error("Too many consecutive failures, stopping")
                    break

            # Wait for next interval (unless we need to stop)
            if not stop_requested:
                # Calculate sleep time, but check for stop_requested periodically
                sleep_end = time.time() + interval_sec
                while time.time() < sleep_end and not stop_requested:
                    time.sleep(min(0.1, sleep_end - time.time()))

    except KeyboardInterrupt:
        logging.info("Keyboard interrupt received")
    except Exception as e:
        logging.error(f"Unexpected error in spectate loop: {e}")
        raise

    logging.info(f"Spectate loop finished. Total commands sent: {commands_sent}")
    return commands_sent


def main(argv: list[str] | None = None) -> int:
    """CLI: --rcon-host / --rcon-port / --rcon-password / --bot / --spectator / --interval / --duration"""

    parser = argparse.ArgumentParser(
        description="Keep a Minecraft spectator following a bot via RCON",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    parser.add_argument("--rcon-host", required=True, help="RCON server hostname")
    parser.add_argument("--rcon-port", type=int, default=25575, help="RCON server port")
    parser.add_argument("--rcon-password", required=True, help="RCON password")
    parser.add_argument("--bot", required=True, help="Bot username to follow")
    parser.add_argument("--spectator", required=True, help="Spectator username")
    parser.add_argument("--interval", type=float, default=5.0,
                       help="Interval between spectate commands (seconds)")
    parser.add_argument("--duration", type=float,
                       help="Total duration to run (seconds). If not set, runs until interrupted")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")

    args = parser.parse_args(argv)

    # Setup logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    try:
        with RconClient(args.rcon_host, args.rcon_port, args.rcon_password) as rcon:
            if not rcon.authenticate():
                raise RuntimeError("RCON authentication failed")

            logging.info("RCON authentication successful")

            # Optional: Get UUIDs (not strictly required for spectate command)
            bot_uuid = get_player_uuid(rcon, args.bot)
            spectator_uuid = get_player_uuid(rcon, args.spectator)

            if bot_uuid:
                logging.debug(f"Bot UUID: {bot_uuid}")
            if spectator_uuid:
                logging.debug(f"Spectator UUID: {spectator_uuid}")

            # Run the spectate loop
            commands_sent = spectate_loop(
                rcon=rcon,
                bot_username=args.bot,
                spectator_username=args.spectator,
                interval_sec=args.interval,
                duration_sec=args.duration
            )

            return 0 if commands_sent > 0 else 1

    except RuntimeError as e:
        logging.error(f"Runtime error: {e}")
        return 1
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
