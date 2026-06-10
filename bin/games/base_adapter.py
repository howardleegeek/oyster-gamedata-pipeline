"""Base adapter for game integrations."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class GameSession:
    """Represents an active game session."""

    pid: int
    window_title: str
    exe_path: str


@dataclass
class GameMetadata:
    """Container for game-specific metadata extracted from logs/configs."""

    game_name: str
    place_id: str = ""
    universe_id: str = ""
    world_id: str = ""
    instance_id: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


class GameAdapter(ABC):
    """Abstract base class for game adapters using the session/metadata model.

    Subclasses must implement:
      - detect(): find a running game process and return a GameSession
      - extract_metadata(): pull game-specific metadata from logs/configs
      - pre_record_hook(): called before recording starts
      - post_record_hook(): called after recording ends
    """

    @property
    @abstractmethod
    def game_name(self) -> str:
        """Return the canonical game name string."""
        ...

    @abstractmethod
    def detect(self) -> Optional[GameSession]:
        """Detect a running game process.

        Returns a GameSession if found, None otherwise.
        """
        ...

    @abstractmethod
    def extract_metadata(self, pid: int) -> GameMetadata:
        """Extract game metadata from local logs or config files.

        Args:
            pid: The process ID of the running game.

        Returns:
            A GameMetadata instance populated with game-specific fields.
        """
        ...

    @abstractmethod
    def pre_record_hook(self, session: GameSession) -> None:
        """Called before recording starts."""
        ...

    @abstractmethod
    def post_record_hook(self, session: GameSession) -> None:
        """Called after recording ends."""
        ...


class BaseAdapter(ABC):
    """Abstract base class for all game adapters.

    Subclasses must implement:
      - detect(): classmethod to identify the game from process info
      - extract_metadata(): instance method to pull game-specific metadata
      - get_recording_hooks(): instance method to return hook configs
    """

    # Override in subclass
    GAME_NAME: str = ""

    @classmethod
    @abstractmethod
    def detect(cls, process_name: str, process_exe: str) -> bool:
        """Return True if the given process belongs to this game.

        Args:
            process_name: The process name (e.g. 'BeamNG.drive.exe').
            process_exe: Full path to the executable.
        """
        ...

    @abstractmethod
    def extract_metadata(self, settings_path: Optional[str] = None) -> Dict[str, Any]:
        """Extract game metadata from settings/config files.

        Args:
            settings_path: Optional override path to settings file.

        Returns:
            Dict with at least 'game_name' key.
        """
        ...

    @abstractmethod
    def get_recording_hooks(self) -> list[Dict[str, Any]]:
        """Return list of recording hook configurations.

        Each hook dict should contain:
          - name: human-readable hook name
          - event: the event to hook into
          - filter_fn: optional filter description
        """
        ...

    @staticmethod
    def _load_json(path: str) -> Dict[str, Any]:
        """Safely load a JSON file."""
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} game={self.GAME_NAME!r}>"
