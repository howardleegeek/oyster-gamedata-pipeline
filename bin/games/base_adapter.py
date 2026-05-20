"""Game adapter base class.

All game-specific adapters inherit from ``GameAdapter`` and implement
``detect()`` and ``extract_metadata()``.  Optional hooks
``pre_record_hook()`` / ``post_record_hook()`` can be overridden for
per-game lifecycle actions (e.g. injecting an overlay marker).
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class GameSession:
    """Minimal handle to a running game process."""

    pid: int
    window_title: str
    exe_path: str
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class GameMetadata:
    """Structured metadata extracted from a running game session."""

    game_name: str
    version: str = ""
    current_world: str = ""
    place_id: str = ""
    universe_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class GameAdapter(abc.ABC):
    """Abstract base class for game-specific adapters.

    Subclasses must implement ``detect()`` and ``extract_metadata()``.
    The optional ``pre_record_hook()`` / ``post_record_hook()`` methods
    are called before and after a recording session.
    """

    @property
    @abc.abstractmethod
    def game_name(self) -> str:
        """Canonical game identifier (e.g. ``'roblox'``)."""
        ...

    @abc.abstractmethod
    def detect(self) -> Optional[GameSession]:
        """Detect whether the game is currently running.

        Returns a ``GameSession`` if the game process is found, or
        ``None`` if the game is not running.  Must never raise on a
        missing process.
        """
        ...

    @abc.abstractmethod
    def extract_metadata(self, pid: int) -> GameMetadata:
        """Extract game-specific metadata for the given PID.

        Called only after ``detect()`` returns a non-None session.
        """
        ...

    def pre_record_hook(self, session: GameSession) -> None:
        """Called before recording starts.  Override for per-game setup."""
        pass

    def post_record_hook(self, session: GameSession) -> None:
        """Called after recording ends.  Override for per-game cleanup."""
        pass
