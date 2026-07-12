#!/usr/bin/env python3
"""G093 · Red Team: WebSocket Auth with Wrong Observation Key"""
import argparse
import base64
import hashlib
import json
import logging
import os
import socket
import ssl
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

__version__ = "1.0.0"
logger = logging.getLogger("red_team_wrong_obs_key")

def build_handshake(host: str, port: int, obs_key: str, path: str = "/") -> bytes:
    """Build WebSocket upgrade request with wrong observation key."""
    ws_key = base64.b64encode(os.urandom(16)).decode("ascii")
    headers = [f"GET {path} HTTP/1.1", f"Host: {host}:{port}", "Upgrade: websocket",
               "Connection: Upgrade", f"Sec-WebSocket-Key: {ws_key}", "Sec-WebSocket-Version: 13",
               f"Authorization: Bearer {obs_key}", f"X-Obs-Key: {obs_key}", "\r\n"]
    return "\r\n".join(headers).encode("utf-8")

def parse_response(raw: bytes) -> tuple[int, dict[str, str]]:
    """Parse HTTP response; return (status_code, headers)."""
    text = raw.decode("utf-8", errors="replace")
    hdr_end = text.find("\r\n\r\n")
    if hdr_end == -1:
        hdr_end = len(text)
    lines = text[:hdr_end].split("\r\n")
    if not lines:
        return 0, {}
    status_code = int(lines[0].split(" ", 2)[1]) if len(lines[0].split()) >= 2 else 0
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return status_code, headers

def attempt_auth(host: str, port: int, obs_key: str, use_tls: bool = False,
                 timeout: float = 10.0, path: str = "/") -> dict:
    """Attempt WebSocket handshake with incorrect observation key."""
    request = build_handshake(host, port, obs_key, path)
    result = {"status_code": 0, "refused": False, "headers": {}, "error": None}
    sock = None
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        if use_tls:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            sock = ctx.wrap_socket(sock, server_hostname=host)
        sock.sendall(request)
        response = b""
        sock.settimeout(timeout)
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
        if response:
            status_code, headers = parse_response(response)
            result["status_code"] = status_code
            result["headers"] = headers
            result["refused"] = status_code != 101  # 101 = Switching Protocols
        else:
            result["error"] = "Empty response"
            result["refused"] = True
    except socket.timeout:
        result["error"] = f"Timeout after {timeout}s"
        result["refused"] = True
    except ConnectionRefusedError:
        result["error"] = f"Connection refused by {host}:{port}"
        result["refused"] = True
    except OSError as e:
        result["error"] = str(e)
        result["refused"] = True
    finally:
        if sock:
            try:
                sock.close()
            except OSError as sock_close_exc:
                logger.debug(
                    "socket close failed (non-fatal) [%s]: %s",
                    type(sock_close_exc).__name__,
                    sock_close_exc,
                )
    return result

def write_audit_entry(audit_path: Path, host: str, port: int,
                      obs_key_hash: str, result: dict, attempt_id: str) -> None:
    """Append structured audit entry to log file."""
    entry = {
        "attempt_id": attempt_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool": "red_team_wrong_obs_key",
        "version": __version__,
        "target": {"host": host, "port": port},
        "obs_key_sha256": obs_key_hash,
        "outcome": {"status_code": result["status_code"], "refused": result["refused"],
                   "error": result["error"]}
    }
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with open(audit_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    logger.info("Audit entry written to %s", audit_path)

def resolve_audit_log(path: Optional[str]) -> Path:
    """Resolve audit log path; create temp file if None."""
    if path:
        return Path(path)
    temp_dir = tempfile.mkdtemp(prefix="redteam_audit_")
    return Path(temp_dir) / "audit_log.jsonl"

def main(argv: Optional[list[str]] = None) -> int:
    """Main entry point. Returns: 0=refused, 1=accepted, 2=error, 3=invalid args."""
    parser = argparse.ArgumentParser(
        prog="red_team_wrong_obs_key",
        description="Red-team tool: attempt WebSocket auth with wrong observation key"
    )
    parser.add_argument("--host", required=True, help="Target hostname or IP")
    parser.add_argument("--port", type=int, required=True, help="Target port")
    parser.add_argument("--obs-key", required=True, help="Wrong observation key to test")
    parser.add_argument("--path", default="/", help="WebSocket request path")
    parser.add_argument("--tls", action="store_true", help="Use TLS")
    parser.add_argument("--timeout", type=float, default=10.0, help="Timeout in seconds")
    parser.add_argument("--audit-log", help="Audit log file path")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    attempt_id = str(uuid.uuid4())
    obs_key_hash = hashlib.sha256(args.obs_key.encode("utf-8")).hexdigest()
    logger.info("Red-team attempt %s — target %s:%d (key hash: %s…)",
                attempt_id, args.host, args.port, obs_key_hash[:12])

    result = attempt_auth(args.host, args.port, args.obs_key,
                          args.tls, args.timeout, args.path)

    audit_path = resolve_audit_log(args.audit_log)
    write_audit_entry(audit_path, args.host, args.port, obs_key_hash, result, attempt_id)

    if result["error"] is not None:
        logger.error("Connection error: %s", result["error"])
        return 2
    if result["refused"]:
        logger.info("PASS — server correctly refused wrong key (HTTP %d)", result["status_code"])
        return 0
    logger.critical(
        "FAIL — server ACCEPTED wrong observation key! (HTTP %d)", result["status_code"]
    )
    return 1

if __name__ == "__main__":
    sys.exit(main())
