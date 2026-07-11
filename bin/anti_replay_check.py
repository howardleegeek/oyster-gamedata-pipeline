#!/usr/bin/env python3
"""
Anti-replay check for uploaded sessions.

Detects duplicate / replay-attack sessions where a tester re-submits
the same recording or a near-identical one.

Checks per uploaded session:
1. session_id duplicate check (memory dedup against last 100 sessions)
2. video_hash sha256 first/last 1MB → reject if matches prior
3. frame_0001.png perceptual hash → reject if >0.95 similarity to prior
4. input event sequence hash → reject duplicate input streams

Exit codes:
  0 – session accepted (no replay detected)
  1 – session rejected (exact duplicate found)
  2 – session flagged (near-match / perceptual similarity)

Rejections are logged to dashboard/replay_attacks.json.
"""

import argparse
import hashlib
import json
import logging
import sys
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from PIL import Image
except ImportError:
    Image = None  # type: ignore[misc]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_MEMORY_SESSIONS = 100
VIDEO_HASH_CHUNK = 1024 * 1024  # 1 MB
PERCEPTUAL_HASH_SIZE = 16  # 16x16 for average hash
SIMILARITY_THRESHOLD = 0.95
REPLAY_LOG_PATH = Path("dashboard") / "replay_attacks.json"

# ---------------------------------------------------------------------------
# In-memory session store
# ---------------------------------------------------------------------------


class SessionStore:
    """Thread-unsafe in-memory store for the last N sessions."""

    def __init__(self, max_size: int = MAX_MEMORY_SESSIONS):
        self.max_size = max_size
        self.session_ids: deque[str] = deque(maxlen=max_size)
        self.video_hashes: deque[tuple[str, str]] = deque(
            maxlen=max_size
        )  # (hash, session_id)
        self.perceptual_hashes: deque[tuple[str, str]] = deque(
            maxlen=max_size
        )  # (hex_hash, session_id)
        self.input_hashes: deque[tuple[str, str]] = deque(
            maxlen=max_size
        )  # (hash, session_id)

    def add_session_id(self, session_id: str) -> bool:
        """Return True if session_id is a duplicate."""
        is_dup = session_id in self.session_ids
        self.session_ids.append(session_id)
        return is_dup

    def add_video_hash(self, vhash: str, session_id: str) -> bool:
        """Return True if video_hash matches a prior session."""
        for prev_hash, _ in self.video_hashes:
            if prev_hash == vhash:
                return True
        self.video_hashes.append((vhash, session_id))
        return False

    def add_perceptual_hash(
        self, phash: str, session_id: str
    ) -> tuple[bool, str | None]:
        """
        Return (is_near_match, matched_session_id).
        Near-match means similarity > SIMILARITY_THRESHOLD.
        """
        matched_id: str | None = None
        for prev_hash, prev_id in self.perceptual_hashes:
            sim = _hash_similarity(phash, prev_hash)
            if sim > SIMILARITY_THRESHOLD:
                matched_id = prev_id
                break
        self.perceptual_hashes.append((phash, session_id))
        return matched_id is not None, matched_id

    def add_input_hash(self, ihash: str, session_id: str) -> bool:
        """Return True if input event sequence hash matches a prior session."""
        for prev_hash, _ in self.input_hashes:
            if prev_hash == ihash:
                return True
        self.input_hashes.append((ihash, session_id))
        return False


# ---------------------------------------------------------------------------
# Hashing helpers
# ---------------------------------------------------------------------------


def compute_video_hash(video_path: str | Path) -> str:
    """
    SHA-256 of the first 1 MB + last 1 MB of a video file.
    Returns hex digest string.
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    file_size = video_path.stat().st_size
    h = hashlib.sha256()

    with open(video_path, "rb") as f:
        # First 1 MB
        head = f.read(VIDEO_HASH_CHUNK)
        h.update(head)

        # Last 1 MB (if file is larger than 1 MB)
        if file_size > VIDEO_HASH_CHUNK:
            f.seek(-VIDEO_HASH_CHUNK, 2)  # seek from end
            tail = f.read(VIDEO_HASH_CHUNK)
            h.update(tail)

    return h.hexdigest()


def compute_perceptual_hash(image_path: str | Path) -> str:
    """
    Compute a perceptual hash (average hash) of an image using PIL.
    Returns a hex string of the hash bits.
    """
    if Image is None:
        raise RuntimeError("PIL (Pillow) is required for perceptual hashing")

    image_path = Path(image_path)
    if not image_path.exists():
        raise FileNotFoundError(f"Image file not found: {image_path}")

    img = (
        Image.open(image_path)
        .convert("L")
        .resize((PERCEPTUAL_HASH_SIZE, PERCEPTUAL_HASH_SIZE), Image.LANCZOS)
    )
    pixels = list(img.get_flattened_data())
    avg = sum(pixels) / len(pixels)
    bits = "".join("1" if p > avg else "0" for p in pixels)

    # Convert bit string to hex
    hex_hash = ""
    for i in range(0, len(bits), 4):
        nibble = bits[i : i + 4]
        hex_hash += format(int(nibble, 2), "x")

    return hex_hash


def compute_input_hash(events_path: str | Path) -> str:
    """
    SHA-256 of the input event sequence file.
    Returns hex digest string.
    """
    events_path = Path(events_path)
    if not events_path.exists():
        raise FileNotFoundError(f"Events file not found: {events_path}")

    h = hashlib.sha256()
    with open(events_path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _hash_similarity(hash_a: str, hash_b: str) -> float:
    """
    Compute similarity between two perceptual hashes (hex strings).
    Returns a float between 0.0 and 1.0 (1.0 = identical).
    """
    # Convert hex to bit strings
    bits_a = _hex_to_bits(hash_a)
    bits_b = _hex_to_bits(hash_b)

    if len(bits_a) != len(bits_b):
        return 0.0

    matching = sum(1 for a, b in zip(bits_a, bits_b, strict=True) if a == b)
    return matching / len(bits_a)


def _hex_to_bits(hex_str: str) -> str:
    """Convert a hex string to a binary string."""
    return "".join(format(int(c, 16), "04b") for c in hex_str)


# ---------------------------------------------------------------------------
# Replay log
# ---------------------------------------------------------------------------


def log_rejection(
    session_id: str,
    reason: str,
    details: dict[str, Any] | None = None,
    log_path: Path = REPLAY_LOG_PATH,
) -> None:
    """Append a rejection entry to the replay attacks log."""
    log_path.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "reason": reason,
        "details": details or {},
    }

    entries: list[dict[str, Any]] = []
    if log_path.exists():
        try:
            with open(log_path, "r") as f:
                entries = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.debug("Failed to load replay log %s: %s", log_path, e)
            entries = []

    entries.append(entry)

    with open(log_path, "w") as f:
        json.dump(entries, f, indent=2)


# ---------------------------------------------------------------------------
# Main check logic
# ---------------------------------------------------------------------------


def check_session(
    session_id: str,
    session_dir: str | Path,
    store: SessionStore,
    video_filename: str = "recording.mp4",
    frame_filename: str = "frame_0001.png",
    events_filename: str = "input_events.json",
) -> int:
    """
    Run all anti-replay checks on a session.

    Returns:
        0 – accepted
        1 – rejected (exact duplicate)
        2 – flagged (near-match)
    """
    session_dir = Path(session_dir)

    # --- Check 1: session_id duplicate ---
    if store.add_session_id(session_id):
        log_rejection(session_id, "duplicate_session_id", {"session_id": session_id})
        logger.warning("REJECT: duplicate session_id %s", session_id)
        return 1

    # --- Check 2: video hash ---
    video_path = session_dir / video_filename
    if video_path.exists():
        try:
            vhash = compute_video_hash(video_path)
            if store.add_video_hash(vhash, session_id):
                log_rejection(
                    session_id,
                    "duplicate_video_hash",
                    {"video_hash": vhash, "video_file": str(video_path)},
                )
                logger.warning("REJECT: duplicate video hash for %s", session_id)
                return 1
        except (FileNotFoundError, IOError) as e:
            logger.error("Error computing video hash: %s", e)

    # --- Check 3: perceptual hash ---
    frame_path = session_dir / frame_filename
    if frame_path.exists():
        try:
            phash = compute_perceptual_hash(frame_path)
            is_near_match, matched_id = store.add_perceptual_hash(phash, session_id)
            if is_near_match:
                log_rejection(
                    session_id,
                    "perceptual_near_match",
                    {
                        "perceptual_hash": phash,
                        "matched_session_id": matched_id,
                        "frame_file": str(frame_path),
                    },
                )
                logger.warning(
                    "FLAG: perceptual near-match for %s (matched %s)",
                    session_id,
                    matched_id,
                )
                return 2
        except (FileNotFoundError, IOError, RuntimeError) as e:
            logger.error("Error computing perceptual hash: %s", e)

    # --- Check 4: input event sequence hash ---
    events_path = session_dir / events_filename
    if events_path.exists():
        try:
            ihash = compute_input_hash(events_path)
            if store.add_input_hash(ihash, session_id):
                log_rejection(
                    session_id,
                    "duplicate_input_hash",
                    {"input_hash": ihash, "events_file": str(events_path)},
                )
                logger.warning("REJECT: duplicate input hash for %s", session_id)
                return 1
        except (FileNotFoundError, IOError) as e:
            logger.error("Error computing input hash: %s", e)

    logger.info("ACCEPT: session %s passed all anti-replay checks", session_id)
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Anti-replay check for uploaded sessions"
    )
    parser.add_argument(
        "session_id",
        help="Unique session identifier",
    )
    parser.add_argument(
        "session_dir",
        help="Path to the session directory",
    )
    parser.add_argument(
        "--video",
        default="recording.mp4",
        help="Video filename within session dir (default: recording.mp4)",
    )
    parser.add_argument(
        "--frame",
        default="frame_0001.png",
        help="First frame filename (default: frame_0001.png)",
    )
    parser.add_argument(
        "--events",
        default="input_events.json",
        help="Input events filename (default: input_events.json)",
    )
    parser.add_argument(
        "--log-path",
        default=str(REPLAY_LOG_PATH),
        help="Path to replay attacks log (default: dashboard/replay_attacks.json)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    # Use a global store so repeated calls in the same process share state
    global _global_store
    if "_global_store" not in globals():
        _global_store = SessionStore()

    result = check_session(
        session_id=args.session_id,
        session_dir=args.session_dir,
        store=_global_store,
        video_filename=args.video,
        frame_filename=args.frame,
        events_filename=args.events,
    )

    return result


if __name__ == "__main__":
    sys.exit(main())
