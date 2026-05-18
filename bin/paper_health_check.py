#!/usr/bin/env python3
"""Paper server health probe - TCP connect and parse Minecraft handshake response."""

import argparse, json, socket, struct, sys


def encode_varint(value: int) -> bytes:
    """Encode integer as Minecraft VarInt."""
    result = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        result.append(b | 0x80 if value else b)
        if not value: break
    return bytes(result)


def decode_varint(sock) -> int:
    """Decode Minecraft VarInt from socket.

    Args:
        sock: A socket object with recv() method.

    Returns:
        The decoded integer value.

    Raises:
        IndexError: If socket returns empty data.
    """
    result, shift = 0, 0
    while True:
        b = sock.recv(1)[0]
        result |= (b & 0x7F) << shift
        if not (b & 0x80): break
        shift += 7
    return result


def check_server(host: str, port: int) -> int:
    """Check if Paper server is up and running version 1.20.4.

    Performs a TCP handshake with the Minecraft server, sends a status
    request, and verifies the server version matches 1.20.4.

    Args:
        host: Server hostname or IP address.
        port: Server port number.

    Returns:
        0 if server is up and version matches, 1 otherwise.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        sock.connect((host, port))
        # Send handshake
        data = encode_varint(0) + encode_varint(765) + encode_varint(len(host)) + host.encode() + struct.pack(">H", port) + encode_varint(1)
        sock.sendall(encode_varint(len(data)) + data)
        # Send status request
        sock.sendall(encode_varint(1) + encode_varint(0))
        # Read response
        decode_varint(sock)  # packet length
        decode_varint(sock)  # packet ID
        json_len = decode_varint(sock)
        json_data = b""
        while len(json_data) < json_len:
            json_data += sock.recv(json_len - len(json_data))
        sock.close()
        status = json.loads(json_data.decode())
        version = status.get("version", {}).get("name", "")
        players = status.get("players", {}).get("online", 0)
        print(f"Server: {host}:{port}\nVersion: {version}\nPlayers: {players}")
        if "1.20.4" in version:
            print("Status: OK")
            return 0
        print("Status: WARNING (version mismatch)")
        return 1
    except Exception as e:
        print(f"Error: {e}")
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Paper server health probe")
    parser.add_argument("--host", default="localhost", help="Server host")
    parser.add_argument("--port", type=int, default=25565, help="Server port")
    args = parser.parse_args()
    sys.exit(check_server(args.host, args.port))


if __name__ == "__main__":
    main()
