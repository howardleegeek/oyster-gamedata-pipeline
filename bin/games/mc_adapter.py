"""Minecraft game adapter."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from bin.games.base_adapter import BaseAdapter


class MinecraftAdapter(BaseAdapter):
    """Adapter for Minecraft Java Edition."""

    GAME_NAME = "mc"
    _PROCESS_NAMES = ("java", "java.exe", "javaw.exe", "minecraft")

    @classmethod
    def detect(cls, process_name: str, process_exe: str) -> bool:
        """Detect Minecraft from Java launcher process info."""
        name_lower = process_name.lower()
        exe_lower = process_exe.lower()
        if name_lower in cls._PROCESS_NAMES and "minecraft" in exe_lower:
            return True
        return "minecraft" in name_lower or "minecraft" in exe_lower

    def extract_metadata(self, settings_path: Optional[str] = None) -> Dict[str, Any]:
        """Return basic Minecraft metadata."""
        return {"game_name": self.GAME_NAME}

    def get_recording_hooks(self) -> List[Dict[str, Any]]:
        """Return recording hook configurations for Minecraft."""
        return [
            {
                "name": "minecraft_gameplay",
                "event": "client_ready",
                "filter_fn": "record_gameplay_only",
            }
        ]
