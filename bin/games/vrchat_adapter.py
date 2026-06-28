"""VRChat game adapter.

Detects a running VRChat client by process name (``VRChat.exe`` on
Windows, ``vrchat.app`` on macOS) and extracts ``world_id`` from the
local VRChat output log files.

The adapter also provides a ``pre_record_hook`` that checks whether the
current world is private — if so, recording is skipped to protect user
privacy.
"""

from __future__ import annotations

import logging
import platform
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil

from bin.games.base_adapter import BaseAdapter, GameAdapter, GameMetadata, GameSession

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VRCHAT_EXE_WIN = "VRChat.exe"
_VRCHAT_EXE_MAC = "vrchat.app"

# Regex to extract world_id from VRChat output logs.
# Matches patterns like:
#   OnJoinedWorld: wrld_a1b2c3d4-e5f6-7890-abcd-ef1234567890
#   world_id=wrld_a1b2c3d4-e5f6-7890-abcd-ef1234567890
#   world_id: wrld_a1b2c3d4
_WORLD_ID_RE = re.compile(
    r"(?:world[_-]?id|OnJoinedWorld)\s*[=:]\s*(wrld_[a-z0-9_\-]+)",
    re.IGNORECASE,
)

# Regex to extract instance_id from VRChat output logs.
# Matches patterns like:
#   instance_id=12345~hidden(usr_xxx)
#   instance_id=12345~friends
_INSTANCE_ID_RE = re.compile(
    r"instance[_-]?id\s*[=:]\s*(\d+~[a-zA-Z0-9_\-()]+)",
    re.IGNORECASE,
)

# Private world / instance type keywords that should block recording.
_PRIVATE_INSTANCE_KEYWORDS = (
    "~hidden",
    "~friends",
    "~invite",
    "~private",
)

# Private world ID prefixes (worlds that are inherently private).
_PRIVATE_WORLD_PREFIXES = ("wrld_private",)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _vrchat_exe_name() -> str:
    """Return the platform-specific VRChat executable name."""
    if platform.system() == "Windows":
        return _VRCHAT_EXE_WIN
    return _VRCHAT_EXE_MAC


def _basename_cross_platform(path: str) -> str:
    """Get basename handling both Unix (/) and Windows (\\) separators."""
    # Replace backslashes with forward slashes for cross-platform handling
    normalized = path.replace("\\", "/")
    return Path(normalized).name


def _find_vrchat_process() -> Optional[psutil.Process]:
    """Search all running processes for the VRChat client.

    Returns the first matching ``psutil.Process`` or ``None``.
    Never raises — any ``psutil`` access errors are logged and swallowed.
    """
    target = _vrchat_exe_name()
    try:
        for proc in psutil.process_iter(["pid", "name", "exe"]):
            try:
                name = proc.info.get("name") or ""
                exe = proc.info.get("exe") or ""
                exe_basename = _basename_cross_platform(exe)
                if name == target or exe_basename == target:
                    return proc
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
    except Exception:
        logger.debug("Failed to iterate processes", exc_info=True)
    return None


def _vrchat_log_dir() -> Path:
    """Return the platform-specific VRChat log directory."""
    system = platform.system()
    if system == "Windows":
        return Path.home() / "AppData" / "LocalLow" / "VRChat" / "VRChat"
    # macOS
    return Path.home() / "Library" / "Application Support" / "VRChat" / "VRChat"


def _extract_world_id_from_logs(log_dir: Path) -> dict[str, str]:
    """Scan VRChat output log files for world_id and instance_id.

    Returns a dict with keys ``world_id`` and ``instance_id`` (empty
    strings if not found).
    """
    world_id = ""
    instance_id = ""

    if not log_dir.is_dir():
        return {"world_id": world_id, "instance_id": instance_id}

    try:
        log_files = sorted(
            log_dir.glob("output_log_*.txt"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return {"world_id": world_id, "instance_id": instance_id}

    for log_file in log_files[:5]:  # check the 5 most recent logs
        try:
            content = log_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        m_world = _WORLD_ID_RE.search(content)
        m_instance = _INSTANCE_ID_RE.search(content)

        if m_world and not world_id:
            world_id = m_world.group(1)
        if m_instance and not instance_id:
            instance_id = m_instance.group(1)

        if world_id and instance_id:
            break

    return {"world_id": world_id, "instance_id": instance_id}


def _is_private_world(world_id: str, instance_id: str) -> bool:
    """Determine whether the current world/instance is private.

    Returns True if recording should be skipped for privacy reasons.
    """
    # Check world ID prefix
    for prefix in _PRIVATE_WORLD_PREFIXES:
        if world_id.lower().startswith(prefix):
            return True

    # Check instance type keywords
    instance_lower = instance_id.lower()
    return any(keyword in instance_lower for keyword in _PRIVATE_INSTANCE_KEYWORDS)


# ---------------------------------------------------------------------------
# VRChatAdapter (GameAdapter protocol)
# ---------------------------------------------------------------------------


class VRChatAdapter(GameAdapter, BaseAdapter):
    """Adapter for the VRChat game client.

    Inherits from GameAdapter and also provides BaseAdapter-compatible
    methods for the legacy registry.
    """

    GAME_NAME = "vrchat"

    @property
    def game_name(self) -> str:
        return self.GAME_NAME

    # -- GameAdapter protocol ------------------------------------------------

    def detect(self) -> Optional[GameSession]:
        """Detect a running VRChat client process.

        Returns ``None`` when VRChat is not running (no error).
        """
        proc = _find_vrchat_process()
        if proc is None:
            return None

        try:
            exe_path = proc.exe() or ""
        except Exception:
            return None
        if not isinstance(exe_path, str):
            return None

        try:
            window_title = proc.name() or "VRChat"
        except Exception:
            window_title = "VRChat"
        if not isinstance(window_title, str):
            window_title = "VRChat"

        return GameSession(
            pid=proc.pid,
            window_title=window_title,
            exe_path=exe_path,
        )

    def extract_metadata(self, pid: int = 0) -> GameMetadata:
        """Extract VRChat metadata (world_id, instance_id) from local logs."""
        ids = _extract_world_id_from_logs(_vrchat_log_dir())
        return GameMetadata(
            game_name="vrchat",
            world_id=ids["world_id"],
            instance_id=ids["instance_id"],
        )

    def pre_record_hook(self, session: GameSession) -> bool:
        """Check if recording should be allowed.

        Returns True if recording is allowed, False if the world is private
        and recording should be skipped.
        """
        meta = self.extract_metadata(session.pid)
        if _is_private_world(meta.world_id, meta.instance_id):
            logger.warning(
                "VRChat pre-record hook: skipping recording for private "
                "world=%s instance=%s (PID %d)",
                meta.world_id,
                meta.instance_id,
                session.pid,
            )
            return False

        logger.info(
            "VRChat pre-record hook: recording allowed for world=%s (PID %d)",
            meta.world_id,
            session.pid,
        )
        return True

    def post_record_hook(self, session: GameSession) -> None:
        """No-op cleanup after recording."""
        logger.info("VRChat post-record hook for PID %d", session.pid)

    # -- BaseAdapter-compatible methods --------------------------------------

    @classmethod
    def detect_by_process(cls, process_name: str, process_exe: str) -> bool:
        """Return True if the given process belongs to VRChat.

        Checks both the process name and the executable path for
        VRChat identifiers.
        """
        name_lower = process_name.lower()
        exe_lower = process_exe.lower()

        for target in (_VRCHAT_EXE_WIN, _VRCHAT_EXE_MAC):
            target_lower = target.lower()
            if name_lower == target_lower:
                return True
            if target_lower in exe_lower:
                return True

        return False

    def extract_metadata_legacy(self, settings_path: Optional[str] = None) -> Dict[str, Any]:
        """Extract metadata — BaseAdapter-compatible interface.

        Args:
            settings_path: Optional override path to log directory.

        Returns:
            Dict with game_name, world_id, instance_id keys.
        """
        metadata: Dict[str, Any] = {
            "game_name": self.GAME_NAME,
            "world_id": None,
            "instance_id": None,
        }

        if settings_path:
            log_dir = Path(settings_path)
        else:
            log_dir = _vrchat_log_dir()

        ids = _extract_world_id_from_logs(log_dir)
        metadata["world_id"] = ids["world_id"] or None
        metadata["instance_id"] = ids["instance_id"] or None

        return metadata

    def get_recording_hooks(self) -> List[Dict[str, Any]]:
        """Return recording hook configurations for VRChat.

        Includes a privacy filter that skips recording in private worlds.
        """
        return [
            {
                "name": "vrchat_private_world_filter",
                "event": "on_world_join",
                "filter_fn": "skip_private_worlds",
                "description": "Skip recording when user is in a private world or instance",
            },
            {
                "name": "vrchat_world_metadata",
                "event": "on_world_change",
                "filter_fn": "extract_world_id",
                "description": "Tag recordings with world_id and instance_id metadata",
            },
            {
                "name": "vrchat_instance_type",
                "event": "on_instance_join",
                "filter_fn": "check_instance_privacy",
                "description": "Check instance type (public/friends/invite/hidden) before recording",
            },
        ]
