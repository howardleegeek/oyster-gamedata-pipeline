#!/usr/bin/env python3
"""
PII Redactor - Automatically redact PII from session data.
Replaces player username with player_<hash8>, masks chat, masks IPs.
Post-recording redact PII from screen captures (frames containing Discord
names, emails, phone numbers) via OCR + regex on rgb/ frames.
"""

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy import of pytesseract – degrades gracefully if missing
# ---------------------------------------------------------------------------
_pytesseract = None
_pytesseract_error: Optional[str] = None


def _get_pytesseract():
    """Return pytesseract module or None if unavailable."""
    global _pytesseract, _pytesseract_error
    if _pytesseract is not None or _pytesseract_error is not None:
        return _pytesseract
    try:
        import pytesseract as _pt

        _pytesseract = _pt
    except ImportError as exc:
        _pytesseract_error = str(exc)
        logger.warning("pytesseract not available (%s); OCR redaction disabled", exc)
    except Exception as exc:
        _pytesseract_error = str(exc)
        logger.warning("pytesseract failed to load (%s); OCR redaction disabled", exc)
    return _pytesseract


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------


def sha8(text: str, salt: str = "oyster_salt") -> str:
    """Generate 8-character hash of text."""
    combined = f"{text}{salt}"
    return hashlib.sha256(combined.encode()).hexdigest()[:8]


def pseudonymize_username(username: str) -> str:
    """Replace username with pseudonymized version."""
    if not username:
        return "player_unknown"
    if username.startswith("player_") and len(username) > 8:
        return username
    return f"player_{sha8(username)}"


def is_already_pseudonymized(username: str) -> bool:
    """Check if username is already pseudonymized."""
    return username and username.startswith("player_") and len(username) > 8


def mask_ip(ip: str) -> str:
    """Mask IP address to xxx.xxx.xxx.0"""
    parts = ip.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.{parts[2]}.0"
    return "xxx.xxx.xxx.0"


def redact_chat_message(message: str) -> str:
    """Replace chat message with redaction marker."""
    return "[redacted]"


# ---------------------------------------------------------------------------
# PII regex patterns
# ---------------------------------------------------------------------------

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
PHONE_RE = re.compile(
    r"(?:\+?1[-.\s]?)?" r"(?:\(?\d{3}\)?[-.\s]?)" r"\d{3}[-.\s]?\d{4}"
)
DISCORD_TAG_RE = re.compile(r"@\w+#\d{4}")
DISCORD_NAME_RE = re.compile(r"@\w+")


def find_pii_in_text(text: str) -> List[Tuple[str, int, int]]:
    """Return list of (matched_text, start, end) for all PII found."""
    results: List[Tuple[str, int, int]] = []
    for pattern in (EMAIL_RE, PHONE_RE, DISCORD_TAG_RE, DISCORD_NAME_RE):
        for m in pattern.finditer(text):
            results.append((m.group(), m.start(), m.end()))
    # Deduplicate overlapping matches (keep longest)
    results.sort(key=lambda x: (x[1], -(x[2] - x[1])))
    deduped: List[Tuple[str, int, int]] = []
    last_end = -1
    for text_match, start, end in results:
        if start >= last_end:
            deduped.append((text_match, start, end))
            last_end = end
    return deduped


# ---------------------------------------------------------------------------
# Frame-level OCR redaction
# ---------------------------------------------------------------------------


def _ocr_text_with_boxes(image) -> List[Tuple[str, Tuple[int, int, int, int]]]:
    """
    Run pytesseract OCR and return list of (text, (x0, y0, x1, y1)) boxes.
    Requires pytesseract and tesseract-ocr binary.
    """
    pt = _get_pytesseract()
    if pt is None:
        return []

    data = pt.image_to_data(image, output_type=pt.Output.DICT)
    boxes = []
    n = len(data["text"])
    for i in range(n):
        word = data["text"][i].strip()
        if not word:
            continue
        conf = int(data["conf"][i])
        if conf < 30:
            continue
        x = int(data["left"][i])
        y = int(data["top"][i])
        w = int(data["width"][i])
        h = int(data["height"][i])
        boxes.append((word, (x, y, x + w, y + h)))
    return boxes


def _merge_adjacent_boxes(
    boxes: List[Tuple[str, Tuple[int, int, int, int]]],
    gap: int = 10,
) -> List[Tuple[str, Tuple[int, int, int, int]]]:
    """Merge boxes that are on the same line and close together."""
    if not boxes:
        return []
    # Sort by y then x
    boxes.sort(key=lambda b: (b[1][1], b[1][0]))
    merged: List[Tuple[str, Tuple[int, int, int, int]]] = []
    cur_text, (cx0, cy0, cx1, cy1) = boxes[0]
    for text, (x0, y0, x1, y1) in boxes[1:]:
        same_line = abs(y0 - cy0) < gap
        close_x = x0 - cx1 < gap
        if same_line and close_x:
            cur_text += " " + text
            cx1 = max(cx1, x1)
            cy1 = max(cy1, y1)
        else:
            merged.append((cur_text, (cx0, cy0, cx1, cy1)))
            cur_text, cx0, cy0, cx1, cy1 = text, x0, y0, x1, y1
    merged.append((cur_text, (cx0, cy0, cx1, cy1)))
    return merged


def redact_frame(image, padding: int = 4) -> Tuple[Any, int]:
    """
    Redact PII regions in a single PIL Image.

    Returns (redacted_image, redaction_count).
    """
    from PIL import ImageDraw

    boxes = _ocr_text_with_boxes(image)
    merged = _merge_adjacent_boxes(boxes)

    count = 0
    draw = ImageDraw.Draw(image)

    for text, (x0, y0, x1, y1) in merged:
        pii_matches = find_pii_in_text(text)
        if not pii_matches:
            continue

        # Expand box slightly for padding
        rx0 = max(0, x0 - padding)
        ry0 = max(0, y0 - padding)
        rx1 = min(image.width, x1 + padding)
        ry1 = min(image.height, y1 + padding)

        # Draw black box over the region
        draw.rectangle([rx0, ry0, rx1, ry1], fill="black")
        count += 1

    return image, count


def redact_frame_file(
    frame_path: Path,
    output_path: Optional[Path] = None,
    padding: int = 4,
) -> int:
    """
    Redact PII in a single frame image file.

    Returns redaction count.
    """
    from PIL import Image

    img = Image.open(frame_path).convert("RGB")
    img, count = redact_frame(img, padding=padding)

    dest = output_path or frame_path
    img.save(dest)
    return count


def redact_rgb_directory(
    session_dir: Path,
    rgb_subdir: str = "rgb",
    padding: int = 4,
) -> Dict[str, Any]:
    """
    Redact PII from all frames in session_dir/rgb/.

    Returns stats dict with per-frame redaction counts.
    """
    rgb_dir = session_dir / rgb_subdir
    stats: Dict[str, Any] = {
        "frames_processed": 0,
        "total_redactions": 0,
        "per_frame": {},
        "ocr_available": _get_pytesseract() is not None,
    }

    if not rgb_dir.is_dir():
        logger.info("No rgb/ directory at %s; skipping frame redaction", rgb_dir)
        return stats

    image_extensions = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp"}
    frames = sorted(
        p for p in rgb_dir.iterdir() if p.suffix.lower() in image_extensions
    )

    for frame_path in frames:
        count = redact_frame_file(frame_path, padding=padding)
        stats["frames_processed"] += 1
        stats["total_redactions"] += count
        stats["per_frame"][frame_path.name] = count
        if count > 0:
            logger.info("Redacted %d PII region(s) in %s", count, frame_path.name)

    return stats


# ---------------------------------------------------------------------------
# Text / JSON redaction (existing functionality)
# ---------------------------------------------------------------------------


def redact_file_content(
    content: str, player_username: str, pseudonymized_name: str
) -> str:
    """Redact PII from file content."""

    # Replace player username
    if player_username and player_username in content:
        content = content.replace(player_username, pseudonymized_name)

    # Replace email addresses
    content = EMAIL_RE.sub("[email_redacted]", content)

    # Replace phone numbers
    content = PHONE_RE.sub("[phone_redacted]", content)

    # Replace Discord tags and names
    content = DISCORD_TAG_RE.sub("[discord_redacted]", content)
    content = DISCORD_NAME_RE.sub("[discord_redacted]", content)

    # Replace SSNs
    ssn_pattern = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
    content = ssn_pattern.sub("[ssn_redacted]", content)

    # Replace credit card numbers
    cc_pattern = re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b")
    content = cc_pattern.sub("[cc_redacted]", content)

    # Replace public IP addresses (not private)
    private_ip_pattern = re.compile(
        r"\b(?:10\.|172\.(?:1[6-9]|2\d|3[01])\.|192\.168\.|127\.|169\.254\.)"
    )

    def replace_ip(match):
        ip = match.group()
        if private_ip_pattern.match(ip):
            return ip  # Keep private IPs
        return mask_ip(ip)

    ip_pattern = re.compile(
        r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b"
    )
    content = ip_pattern.sub(replace_ip, content)

    # Replace real names (simple pattern)
    name_pattern = re.compile(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b")
    content = name_pattern.sub("[name_redacted]", content)

    return content


def redact_jsonl_file(
    filepath: Path, player_username: str, pseudonymized_name: str
) -> int:
    """Redact PII from a JSONL file. Returns count of redacted entries."""
    if not filepath.exists():
        return 0

    redacted_count = 0
    lines = []

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                original_line = line
                line = redact_file_content(line, player_username, pseudonymized_name)

                # Check if chat messages should be redacted
                try:
                    data = json.loads(line)
                    if "chat" in data and isinstance(data["chat"], str):
                        data["chat"] = redact_chat_message(data["chat"])
                        line = json.dumps(data) + "\n"
                    if "message" in data and isinstance(data["message"], str):
                        data["message"] = redact_chat_message(data["message"])
                        line = json.dumps(data) + "\n"
                    if "messages" in data and isinstance(data["messages"], list):
                        data["messages"] = ["[redacted]" for _ in data["messages"]]
                        line = json.dumps(data) + "\n"
                except json.JSONDecodeError:
                    # Malformed JSONL line — log at debug level so operators
                    # tailing logs can see the skip. Control flow unchanged:
                    # we still leave `line` as-is and continue.
                    logger.debug(
                        "Skipping malformed JSONL line in %s: %s",
                        filepath,
                        line.rstrip("\n"),
                        exc_info=True,
                    )

                if line != original_line:
                    redacted_count += 1
                lines.append(line)
    except Exception as e:
        logger.error("Error reading %s: %s", filepath, e)
        return 0

    with open(filepath, "w", encoding="utf-8") as f:
        f.writelines(lines)

    return redacted_count


def redact_json_file(
    filepath: Path, player_username: str, pseudonymized_name: str
) -> int:
    """Redact PII from a JSON file. Returns count of redacted fields."""
    if not filepath.exists():
        return 0

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError:
        return 0

    original_data = json.dumps(data)
    data = redact_json_data(data, player_username, pseudonymized_name)
    new_data = json.dumps(data)

    if new_data != original_data:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return 1

    return 0


def redact_json_data(data: Any, player_username: str, pseudonymized_name: str) -> Any:
    """Recursively redact PII from JSON data."""
    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            if key in ["chat", "message", "messages", "chat_message"]:
                if isinstance(value, str):
                    result[key] = redact_chat_message(value)
                elif isinstance(value, list):
                    result[key] = ["[redacted]" for _ in value]
                else:
                    result[key] = redact_json_data(
                        value, player_username, pseudonymized_name
                    )
            elif key in ["player", "username", "player_username", "user"]:
                if isinstance(value, str) and value == player_username:
                    result[key] = pseudonymized_name
                else:
                    result[key] = redact_json_data(
                        value, player_username, pseudonymized_name
                    )
            else:
                result[key] = redact_json_data(
                    value, player_username, pseudonymized_name
                )
        return result
    elif isinstance(data, list):
        return [
            redact_json_data(item, player_username, pseudonymized_name) for item in data
        ]
    elif isinstance(data, str):
        return redact_file_content(data, player_username, pseudonymized_name)
    else:
        return data


def redact_session(session_dir: Path, dry_run: bool = False) -> Dict[str, Any]:
    """Redact all PII from a session directory (text + screen captures)."""

    # Find player username
    player_username = None
    game_state_file = session_dir / "game_state.jsonl"
    if game_state_file.exists():
        with open(game_state_file, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    data = json.loads(line)
                    if "player" in data:
                        player = data["player"]
                        if isinstance(player, str):
                            player_username = player
                        elif isinstance(player, dict):
                            player_username = player.get("username") or player.get(
                                "name"
                            )
                        if player_username:
                            break
                except json.JSONDecodeError:
                    continue

    if not player_username:
        player_username = "unknown_player"

    if is_already_pseudonymized(player_username):
        pseudonymized_name = player_username
    else:
        pseudonymized_name = pseudonymize_username(player_username)

    stats: Dict[str, Any] = {
        "player_username": player_username,
        "pseudonymized_to": pseudonymized_name,
        "files_redacted": 0,
        "dry_run": dry_run,
    }

    if dry_run:
        print(
            f"DRY RUN: Would pseudonymize '{player_username}' -> '{pseudonymized_name}'"
        )
        return stats

    # Process JSONL files
    for jsonl_file in session_dir.glob("*.jsonl"):
        count = redact_jsonl_file(jsonl_file, player_username, pseudonymized_name)
        if count > 0:
            print(f"Redacted {count} entries in {jsonl_file.name}")
            stats["files_redacted"] += 1

    # Process JSON files
    for json_file in session_dir.glob("*.json"):
        count = redact_json_file(json_file, player_username, pseudonymized_name)
        if count > 0:
            print(f"Redacted {json_file.name}")
            stats["files_redacted"] += 1

    # Post-recording: redact PII from screen captures in rgb/
    frame_stats = redact_rgb_directory(session_dir)
    stats["frame_redactions"] = frame_stats

    # Create redaction log entry
    log_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_dir": str(session_dir),
        "original_username": player_username,
        "pseudonymized_to": pseudonymized_name,
        "action": "redact_pii",
        "frame_redactions": frame_stats["total_redactions"],
    }

    log_file = session_dir / "redaction_log.jsonl"
    with open(log_file, "a") as f:
        f.write(json.dumps(log_entry) + "\n")

    print(f"Redaction complete: {pseudonymized_name}")
    return stats


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="PII Redactor - Redact PII from session data"
    )
    parser.add_argument("session_dir", type=Path, help="Session directory to redact")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be redacted without making changes",
    )

    args = parser.parse_args()

    if not args.session_dir.exists():
        print(f"Error: Session directory {args.session_dir} does not exist")
        return 1

    redact_session(args.session_dir, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    exit(main())
