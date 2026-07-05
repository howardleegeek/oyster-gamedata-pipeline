"""Roblox game adapter.

Detects a running Roblox client by process name (``RobloxPlayerBeta.exe``
on Windows, ``RobloxPlayer.app`` on macOS) and extracts ``place_id`` /
``universe_id`` from the local Roblox log files.

The ``pre_record_hook`` injects a small overlay marker string
("Recording for Oyster") — this is a metadata-only hook; no actual
in-game mod injection is performed.
"""

from __future__ import annotations

import logging
import os
import platform
import re
from pathlib import Path
from typing import Optional

import psutil

from bin.games.base_adapter import BaseAdapter, GameAdapter, GameMetadata, GameSession

logger = logging.getLogger(__name__)

# Process names used to identify a running Roblox client.
_ROBLOX_EXE_WIN = "RobloxPlayerBeta.exe"
_ROBLOX_EXE_MAC = "RobloxPlayer.app"

# Regex patterns for extracting IDs from Roblox local logs.
# Allow optional whitespace between the key and the separator.
_PLACE_ID_RE = re.compile(r"place[_-]?id\s*[=:]\s*(\d+)", re.IGNORECASE)
_UNIVERSE_ID_RE = re.compile(r"universe[_-]?id\s*[=:]\s*(\d+)", re.IGNORECASE)

# Overlay marker injected by the pre-record hook.
OVERLAY_MARKER = "Recording for Oyster"


def _roblox_exe_name() -> str:
    """Return the platform-specific Roblox executable name."""
    if platform.system() == "Windows":
        return _ROBLOX_EXE_WIN
    return _ROBLOX_EXE_MAC


def _find_roblox_process() -> Optional[psutil.Process]:
    """Search all running processes for the Roblox client.

    Returns the first matching ``psutil.Process`` or ``None``.
    Never raises — any ``psutil`` access errors are logged and swallowed.
    """
    target = _roblox_exe_name()
    try:
        for proc in psutil.process_iter(["pid", "name", "exe"]):
            try:
                name = proc.info.get("name") or ""
                exe = proc.info.get("exe") or ""
                # Match by process name or full exe path basename
                if name == target or os.path.basename(exe) == target:
                    return proc
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
    except Exception as exc:
        logger.debug("Failed to iterate processes: %s", exc)
    return None


def _roblox_log_dir() -> Path:
    """Return the platform-specific Roblox log directory."""
    system = platform.system()
    if system == "Windows":
        return Path(os.environ.get("LOCALAPPDATA", "")) / "Roblox" / "logs"
    # macOS
    return Path.home() / "Library" / "Logs" / "Roblox"


def _extract_ids_from_logs(log_dir: Path) -> dict[str, str]:
    """Scan Roblox log files for place_id and universe_id.

    Returns a dict with keys ``place_id`` and ``universe_id`` (empty
    strings if not found).
    """
    place_id = ""
    universe_id = ""

    if not log_dir.is_dir():
        return {"place_id": place_id, "universe_id": universe_id}

    try:
        log_files = sorted(log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return {"place_id": place_id, "universe_id": universe_id}

    for log_file in log_files[:5]:  # check the 5 most recent logs
        try:
            content = log_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        m_place = _PLACE_ID_RE.search(content)
        m_universe = _UNIVERSE_ID_RE.search(content)

        if m_place and not place_id:
            place_id = m_place.group(1)
        if m_universe and not universe_id:
            universe_id = m_universe.group(1)

        if place_id and universe_id:
            break

    return {"place_id": place_id, "universe_id": universe_id}


class RobloxAdapter(GameAdapter, BaseAdapter):
    """Adapter for the Roblox game client."""

    GAME_NAME = "roblox"

    @property
    def game_name(self) -> str:
        return self.GAME_NAME

    @classmethod
    def detect_by_process(cls, process_name: str, process_exe: str) -> bool:
        """Return True if the given process belongs to Roblox."""
        name_lower = process_name.lower()
        exe_lower = process_exe.lower()
        for target in (_ROBLOX_EXE_WIN, _ROBLOX_EXE_MAC):
            target_lower = target.lower()
            if name_lower == target_lower:
                return True
            if target_lower in exe_lower:
                return True
        return False

    def detect(self) -> Optional[GameSession]:
        """Detect a running Roblox client process.

        Returns ``None`` when Roblox is not running (no error).
        """
        proc = _find_roblox_process()
        if proc is None:
            return None

        try:
            exe_path = proc.exe() or ""
        except Exception as exc:
            logger.debug("Failed to get exe path for Roblox process %s: %s", proc.pid, exc)
            return None
        if not isinstance(exe_path, str):
            return None

        try:
            window_title = proc.name() or "Roblox"
        except Exception as exc:
            logger.debug("Failed to get window title for Roblox process %s: %s", proc.pid, exc)
            window_title = "Roblox"
        if not isinstance(window_title, str):
            window_title = "Roblox"

        return GameSession(
            pid=proc.pid,
            window_title=window_title,
            exe_path=exe_path,
        )

    def extract_metadata(self, pid: int) -> GameMetadata:
        """Extract Roblox metadata (place_id, universe_id) from local logs."""
        ids = _extract_ids_from_logs(_roblox_log_dir())
        return GameMetadata(
            game_name="roblox",
            place_id=ids["place_id"],
            universe_id=ids["universe_id"],
        )

    def pre_record_hook(self, session: GameSession) -> None:
        """Inject an overlay marker before recording starts.

        In a real implementation this would communicate with an overlay
        process.  Here we just log the marker for traceability.
        """
        logger.info(
            "Roblox pre-record hook: overlay marker '%s' for PID %d",
            OVERLAY_MARKER,
            session.pid,
        )

    def post_record_hook(self, session: GameSession) -> None:
        """No-op cleanup after recording."""
        logger.info("Roblox post-record hook for PID %d", session.pid)

    def get_recording_hooks(self) -> list[dict[str, str]]:
        """Return recording hook configurations for Roblox."""
        return [
            {
                "name": "roblox_overlay_marker",
                "event": "pre_record",
                "filter_fn": "inject_overlay_marker",
                "description": "Tag recordings with the Oyster overlay marker",
            }
        ]
