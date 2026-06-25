#!/usr/bin/env python3
"""mock_obs_recorder.py — Fake OBS recorder for local smoke / CI.

Writes a minimal "mp4" placeholder (valid ftyp header + 1 KB of zeros)
and a real ``metadata.json`` next to it.  No OBS, no ffmpeg, no real
capture — just enough bytes for downstream validators to accept the file.

Usage
-----
    python3 bin/mock_obs_recorder.py --output-dir /tmp/smoke_clip
    # → writes /tmp/smoke_clip/recording.mp4  (ftyp + 1 KB zeros)
    # → writes /tmp/smoke_clip/metadata.json   (real metadata)

Exit codes
----------
    0 — files written successfully
    1 — bad args / I/O error
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import logging
import socket
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Minimal MP4 ftyp box (ISO Base Media File Format) — 32 bytes.
# This is a valid-enough header that ffprobe / validators recognise it
# as an MP4 container (even though the rest is zeros).
_MP4_FTYPE_HEADER: bytes = (
    b"\x00\x00\x00\x1c"  # box size: 28 bytes
    b"ftyp"  # box type
    b"isom"  # major brand
    b"\x00\x00\x02\x00"  # minor version
    b"isom"  # compatible brand 1
    b"iso2"  # compatible brand 2
    b"mp41"  # compatible brand 3
)

_ZERO_PAYLOAD_SIZE: int = 1024  # 1 KB of zeros after the header


def _build_metadata(
    *,
    session_id: Optional[str] = None,
    hostname: Optional[str] = None,
    timestamp: Optional[str] = None,
    game: str = "minecraft",
    pid: int = 12345,
    window_title: str = "MC 1.21.4",
) -> Dict[str, Any]:
    """Build the metadata.json payload.

    Mirrors the schema from ``recorder_metadata_emitter.py`` but adds
    game-detection fields so the smoke test can verify the full chain.
    """
    ts = timestamp or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    name = hostname if hostname is not None else socket.gethostname()
    dev_id = hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
    sid = session_id or str(uuid.uuid4())

    return {
        "timestamp": ts,
        "location": "anonymous",
        "device_id": dev_id,
        "session_id": sid,
        "game": game,
        "pid": pid,
        "window_title": window_title,
        "recorder": "mock_obs_recorder",
        "duration_sec": 30,
    }


def write_fake_recording(
    output_dir: Path,
    *,
    metadata: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
    game: str = "minecraft",
    pid: int = 12345,
    window_title: str = "MC 1.21.4",
) -> Dict[str, Path]:
    """Write a fake mp4 + metadata.json into *output_dir*.

    Returns a dict mapping ``"video"`` and ``"metadata"`` to their paths.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # --- fake mp4 -----------------------------------------------------------
    video_path = output_dir / "recording.mp4"
    payload = _MP4_FTYPE_HEADER + b"\x00" * _ZERO_PAYLOAD_SIZE
    video_path.write_bytes(payload)
    logger.info("Wrote fake mp4 (%d bytes) → %s", len(payload), video_path)

    # --- metadata.json ------------------------------------------------------
    meta = metadata or _build_metadata(
        session_id=session_id,
        game=game,
        pid=pid,
        window_title=window_title,
    )
    meta_path = output_dir / "metadata.json"
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    logger.info("Wrote metadata.json → %s", meta_path)

    return {"video": video_path, "metadata": meta_path}


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory to write fake recording + metadata into",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="Override session_id in metadata (default: uuid4)",
    )
    parser.add_argument(
        "--game",
        default="minecraft",
        help="Game name to embed in metadata",
    )
    parser.add_argument(
        "--pid",
        type=int,
        default=12345,
        help="Fake game PID to embed in metadata",
    )
    parser.add_argument(
        "--window-title",
        default="MC 1.21.4",
        help="Fake window title to embed in metadata",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    try:
        result = write_fake_recording(
            args.output_dir,
            session_id=args.session_id,
            game=args.game,
            pid=args.pid,
            window_title=args.window_title,
        )
        sys.stdout.write(json.dumps({k: str(v) for k, v in result.items()}) + "\n")
        return 0
    except Exception as exc:
        logger.error("Failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
