"""
RCON packet construction helpers for pytest fixtures.

This module provides pytest fixtures and helper functions for constructing
RCON (Remote Console) protocol packets for testing purposes. It replaces
magic numbers in test code with calculated values based on packet content.

The RCON protocol packet format is:
    - Length (4 bytes): Size of the rest of the packet
    - Request ID (4 bytes): Client-generated request ID
    - Type (4 bytes): Packet type (SERVERDATA_AUTH, SERVERDATA_AUTH_RESPONSE, etc.)
    - Body (variable): Packet payload as null-terminated string
    - Null terminator (1 byte): Zero byte

Reference: https://developer.valvesoftware.com/wiki/Source_RCON_Protocol
"""

import struct
from typing import Callable, Tuple
import pytest


# RCON packet type constants
SERVERDATA_AUTH = 3
SERVERDATA_AUTH_RESPONSE = 2
SERVERDATA_EXECCOMMAND = 2
SERVERDATA_RESPONSE_VALUE = 0


def rcon_pack_response(request_id: int, packet_type: int, body: str = "") -> bytes:
    """
    Construct an RCON response packet with correct length calculation.
    
    Args:
        request_id: The request ID for the packet (4 bytes)
        packet_type: The packet type (4 bytes)
        body: The packet body as a string (will be null-terminated)
        
    Returns:
        bytes: The complete RCON packet
        
    Example:
        >>> rcon_pack_response(1, SERVERDATA_RESPONSE_VALUE, "Hello")
        b'\\x0e\\x00\\x00\\x00\\x01\\x00\\x00\\x00\\x00\\x00\\x00\\x00Hello\\x00\\x00'
    """
    body_bytes = body.encode('utf-8') + b'\x00'
    length = 4 + 4 + len(body_bytes) + 1  # request_id + packet_type + body + null
    packet = struct.pack('<III', length, request_id, packet_type)
    packet += body_bytes
    packet += b'\x00'  # Second null terminator
    return packet


def parse_rcon_packet(packet: bytes) -> Tuple[int, int, int, str]:
    """
    Parse an RCON packet into its components.
    
    Args:
        packet: Raw RCON packet bytes
        
    Returns:
        Tuple of (length, request_id, packet_type, body)
        
    Raises:
        ValueError: If packet is malformed
    """
    if len(packet) < 12:
        raise ValueError(f"Packet too short: {len(packet)} bytes")
    
    length, request_id, packet_type = struct.unpack('<III', packet[:12])
    
    if length != len(packet) - 4:
        raise ValueError(f"Length mismatch: header says {length}, actual {len(packet)-4}")
    
    body_data = packet[12:-2]  # Remove header and two null terminators
    body = body_data.decode('utf-8', errors='replace')
    
    return length, request_id, packet_type, body


@pytest.fixture
def rcon_pack() -> Callable[[int, int, str], bytes]:
    """Fixture that returns rcon_pack_response function."""
    return rcon_pack_response


@pytest.fixture
def rcon_parse() -> Callable[[bytes], Tuple[int, int, int, str]]:
    """Fixture that returns parse_rcon_packet function."""
    return parse_rcon_packet


@pytest.fixture
def rcon_constants() -> dict:
    """Fixture that returns RCON packet type constants."""
    return {
        'SERVERDATA_AUTH': SERVERDATA_AUTH,
        'SERVERDATA_AUTH_RESPONSE': SERVERDATA_AUTH_RESPONSE,
        'SERVERDATA_EXECCOMMAND': SERVERDATA_EXECCOMMAND,
        'SERVERDATA_RESPONSE_VALUE': SERVERDATA_RESPONSE_VALUE,
    }


@pytest.fixture
def sample_rcon_packet() -> bytes:
    """Fixture that returns a sample valid RCON packet."""
    return rcon_pack_response(
        request_id=42,
        packet_type=SERVERDATA_RESPONSE_VALUE,
        body="Command executed successfully"
    )


def main(argv=None) -> int:
    """Command-line interface for testing RCON packet construction."""
    import sys
    
    # Simple test to verify the helper works
    packet = rcon_pack_response(1, SERVERDATA_RESPONSE_VALUE, "test")
    print(f"Test packet: {packet.hex()}")
    
    length, req_id, ptype, body = parse_rcon_packet(packet)
    print(f"Parsed: length={length}, id={req_id}, type={ptype}, body={repr(body)}")
    
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())