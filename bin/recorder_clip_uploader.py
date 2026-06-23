#!/usr/bin/env python3
"""recorder_clip_uploader.py — Opt-in tarball uploader for finished clips.

Posts a recorder-produced ``.tar.gz`` to a configurable backend ingest
endpoint (compatible with G190 :mod:`backend_ingest_handler` — i.e.
``POST /v1/ingest`` with ``multipart/form-data`` containing ``vendor_id``
and ``file``).

Configuration is read from
``%LOCALAPPDATA%/OysterRecorder/config.json`` on Windows or
``$XDG_CONFIG_HOME/OysterRecorder/config.json`` (default
``~/.config/OysterRecorder/config.json``) elsewhere::

    {
        "ingest_endpoint": "https://api.example.com/v1/ingest",
        "vendor_id": "vendor-foo",
        "auth_token": "Bearer abc..."   # optional; sent as Authorization
    }

If the config file is missing or ``ingest_endpoint`` is unset, the script
silently skips upload — the recorder must never block the tester on a
networking flow that they have not opted into.

Usage:
    python3 bin/recorder_clip_uploader.py --tarball clip-...tar.gz
    python3 bin/recorder_clip_uploader.py --tarball ... --vendor-id v-1
    python3 bin/recorder_clip_uploader.py --tarball ... --dry-run

Exit codes:
    0 — uploaded OK, or skipped because config missing (opt-out by default)
    1 — invalid args / tarball missing
    2 — backend rejected (HTTP 4xx/5xx)
    3 — network error / unreachable endpoint
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

CONFIG_FILENAME: str = "config.json"
APP_DIRNAME: str = "OysterRecorder"
DEFAULT_TIMEOUT_SECONDS: float = 60.0
MAX_PAYLOAD_BYTES: int = 6 * 1024 * 1024 * 1024  # 6 GiB safety lid
USER_AGENT: str = "OysterRecorder-Uploader/1.0"


@dataclass(frozen=True)
class UploadConfig:
    """Configuration loaded from the recorder config.json."""

    ingest_endpoint: Optional[str]
    vendor_id: Optional[str]
    auth_token: Optional[str]


def config_dir() -> Path:
    """Return the platform-appropriate config directory.

    Windows uses ``%LOCALAPPDATA%\\OysterRecorder``; POSIX hosts use
    ``$XDG_CONFIG_HOME`` (falling back to ``~/.config``) plus the
    ``OysterRecorder`` subdir.
    """
    if platform.system() == "Windows":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / APP_DIRNAME
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / APP_DIRNAME
    return Path.home() / ".config" / APP_DIRNAME


def load_config(config_path: Optional[Path] = None) -> UploadConfig:
    """Load the recorder config, returning empty UploadConfig if missing.

    Args:
        config_path: Optional override; mostly used for tests.  When None,
            falls back to :func:`config_dir` / ``config.json``.
    """
    path = config_path or (config_dir() / CONFIG_FILENAME)
    if not path.exists():
        return UploadConfig(None, None, None)
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Bad config %s: %s", path, exc)
        return UploadConfig(None, None, None)
    if not isinstance(payload, dict):
        return UploadConfig(None, None, None)
    return UploadConfig(
        ingest_endpoint=payload.get("ingest_endpoint"),
        vendor_id=payload.get("vendor_id"),
        auth_token=payload.get("auth_token"),
    )


def build_multipart_body(tarball_path: Path,
                         vendor_id: str) -> Tuple[bytes, str]:
    """Return ``(body, content_type)`` for a multipart/form-data POST.

    The recorder is the only producer that talks to /v1/ingest, so we hand
    -roll a minimal multipart payload rather than depending on ``requests``
    or ``urllib3``.  This keeps the recorder install size small and lets
    the same script run inside the standalone Windows .exe bundle.
    """
    boundary = f"----OysterRecorderBoundary{uuid.uuid4().hex}"
    pre = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="vendor_id"\r\n\r\n'
        f"{vendor_id}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; '
        f'filename="{tarball_path.name}"\r\n'
        f"Content-Type: application/gzip\r\n\r\n"
    ).encode("utf-8")
    file_bytes = tarball_path.read_bytes()
    if len(file_bytes) > MAX_PAYLOAD_BYTES:
        raise ValueError(
            f"Tarball too large: {len(file_bytes)} > {MAX_PAYLOAD_BYTES}")
    post = f"\r\n--{boundary}--\r\n".encode("utf-8")
    body = pre + file_bytes + post
    content_type = f"multipart/form-data; boundary={boundary}"
    return body, content_type


def post_tarball(endpoint: str, body: bytes, content_type: str,
                 auth_token: Optional[str] = None,
                 timeout: float = DEFAULT_TIMEOUT_SECONDS) -> Dict[str, Any]:
    """POST a multipart body to ``endpoint`` and return parsed JSON.

    Raises:
        urllib.error.HTTPError: server rejected (status_code >= 400).
        urllib.error.URLError: network unreachable / DNS failure.
    """
    headers: Dict[str, str] = {
        "Content-Type": content_type,
        "User-Agent": USER_AGENT,
    }
    if auth_token:
        headers["Authorization"] = auth_token
    req = urllib.request.Request(endpoint, data=body, headers=headers,
                                  method="POST")
    started = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        elapsed = time.monotonic() - started
        try:
            return {
                "status_code": resp.status,
                "elapsed_s": elapsed,
                "response": json.loads(raw.decode("utf-8") or "{}"),
            }
        except json.JSONDecodeError:
            return {
                "status_code": resp.status,
                "elapsed_s": elapsed,
                "response": {"raw": raw.decode("utf-8", errors="replace")},
            }


def upload_clip(tarball_path: Path,
                config: Optional[UploadConfig] = None,
                vendor_id_override: Optional[str] = None,
                dry_run: bool = False) -> Dict[str, Any]:
    """Upload ``tarball_path`` if config has an endpoint; else skip.

    Returns a dict with at least ``{"status": str}``; status is one of
    ``"uploaded" | "skipped" | "failed"``.
    """
    cfg = config or load_config()
    endpoint = cfg.ingest_endpoint
    if not endpoint:
        logger.info("No ingest endpoint configured — skipping upload.")
        return {"status": "skipped", "reason": "no endpoint"}
    vendor_id = vendor_id_override or cfg.vendor_id or "anonymous"
    body, content_type = build_multipart_body(tarball_path, vendor_id)
    if dry_run:
        return {
            "status": "skipped", "reason": "dry-run",
            "endpoint": endpoint, "vendor_id": vendor_id,
            "body_bytes": len(body),
        }
    try:
        result = post_tarball(endpoint, body, content_type,
                              auth_token=cfg.auth_token)
        result["status"] = "uploaded"
        result["vendor_id"] = vendor_id
        return result
    except urllib.error.HTTPError as exc:
        return {"status": "failed", "reason": "http_error",
                "status_code": exc.code, "detail": str(exc)}
    except urllib.error.URLError as exc:
        return {"status": "failed", "reason": "network",
                "detail": str(exc.reason)}


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--tarball", type=Path, required=True,
                        help="Path to the .tar.gz produced by the recorder")
    parser.add_argument("--vendor-id", type=str, default=None,
                        help="Override vendor_id from config.json")
    parser.add_argument("--config", type=Path, default=None,
                        help="Override config.json path (test usage)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Resolve config + payload size, do not POST")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point.  Status maps to documented exit codes."""
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s: %(message)s")
    tarball: Path = args.tarball.resolve()
    if not tarball.exists() or not tarball.is_file():
        logger.error("Tarball not found: %s", tarball)
        return 1
    config = load_config(args.config) if args.config else load_config()
    result = upload_clip(tarball, config=config,
                         vendor_id_override=args.vendor_id,
                         dry_run=args.dry_run)
    logger.info("Upload result: %s", result)
    status = result.get("status")
    if status == "uploaded" or status == "skipped":
        return 0
    if result.get("reason") == "network":
        return 3
    return 2


if __name__ == "__main__":
    sys.exit(main())
