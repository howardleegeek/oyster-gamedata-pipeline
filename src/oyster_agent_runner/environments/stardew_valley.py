# -*- coding: utf-8 -*-
"""Stardew Valley environment for Oyster Agent Runner.

Extractor that communicates with a SMAPI (Stardew Modding API) mod relay
over HTTP/JSON.  Provides player position, facing direction, current map
name, and action-key state at up to 60 Hz.

Usage
-----
    python -m oyster_agent_runner.environments.stardew_valley \\
        --host 127.0.0.1 --port 24600 --fps 60

The SMAPI mod must be running on the target machine and exposing the
documented JSON endpoints (see ``docs/PRD.md`` for the relay protocol).

Endpoints (consumed by this module)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
- ``GET /state``  → ``{"x": float, "y": float, "facing": str, "map": str,
  "keys": {"up": bool, "down": bool, "left": bool, "right": bool,
  "use_tool": bool, "do_action": bool, "cancel": bool, "run": bool}}``
- ``POST /press`` → ``{"key": "<action>"}``  (send a key press to the game)
- ``GET /health`` → ``{"ok": true, "fps": int}``  (health-check)

Author  : Oyster Agent Runner team
License : MIT
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Tuple

from oyster_agent_runner.environments.base import Action, Environment, Observation

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_HOST: str = "127.0.0.1"
DEFAULT_PORT: int = 24600
DEFAULT_FPS: int = 60
DEFAULT_TIMEOUT: float = 1.0  # seconds per HTTP request
ACTION_KEYS: Tuple[str, ...] = (
    "up",
    "down",
    "left",
    "right",
    "use_tool",
    "do_action",
    "cancel",
    "run",
)
NOOP_ACTION = "noop"
VALID_ACTIONS: Tuple[str, ...] = (*ACTION_KEYS, NOOP_ACTION)
FACING_DIRS: Tuple[str, ...] = ("up", "down", "left", "right")
DEFAULT_SOURCE = "smapi_relay"

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class PlayerState:
    """Snapshot of the player's in-game state at a single frame."""

    x: float = 0.0
    y: float = 0.0
    facing: str = "down"
    map_name: str = ""
    keys: Dict[str, bool] = field(default_factory=lambda: dict.fromkeys(ACTION_KEYS, False))
    timestamp: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Return a plain-dict representation (JSON-serialisable)."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlayerState":
        """Construct from a JSON-parsed dict, filling missing fields."""
        keys = {k: data.get("keys", {}).get(k, False) for k in ACTION_KEYS}
        return cls(
            x=float(data.get("x", 0.0)),
            y=float(data.get("y", 0.0)),
            facing=data.get("facing", "down"),
            map_name=data.get("map", data.get("map_name", "")),
            keys=keys,
            timestamp=data.get("timestamp", time.time()),
        )


@dataclass
class EnvConfig:
    """Configuration for the Stardew Valley environment."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    fps: int = DEFAULT_FPS
    timeout: float = DEFAULT_TIMEOUT
    base_url: str = ""

    def __post_init__(self) -> None:
        if not self.base_url:
            self.base_url = f"http://{self.host}:{self.port}"


# ---------------------------------------------------------------------------
# HTTP relay client
# ---------------------------------------------------------------------------


class SMAPIRelayClient:
    """Thin HTTP client for the SMAPI mod relay.

    All methods raise ``ConnectionError`` on network failure so the
    caller can decide whether to retry or abort.
    """

    def __init__(self, config: EnvConfig) -> None:
        self._config = config
        self._base = config.base_url

    # -- public API ---------------------------------------------------------

    def get_state(self) -> PlayerState:
        """Fetch the current player state from the relay."""
        url = f"{self._base}/state"
        raw = self._get_json(url)
        return PlayerState.from_dict(raw)

    def press_key(self, key: str) -> Dict[str, Any]:
        """Send a single key-press action to the game.

        Parameters
        ----------
        key : str
            One of the recognised action keys (see ``ACTION_KEYS``).

        Returns
        -------
        dict
            The relay's JSON response.
        """
        if key not in ACTION_KEYS:
            raise ValueError(f"Unknown action key: {key!r}")
        url = f"{self._base}/press"
        payload = json.dumps({"key": key}).encode("utf-8")
        return self._post_json(url, payload)

    def health_check(self) -> bool:
        """Return ``True`` if the relay is reachable and healthy."""
        try:
            url = f"{self._base}/health"
            resp = self._get_json(url)
            return bool(resp.get("ok", False))
        except ConnectionError:
            return False

    # -- internals ----------------------------------------------------------

    def _get_json(self, url: str) -> Dict[str, Any]:
        """Perform a GET request and return parsed JSON."""
        req = urllib.request.Request(url, method="GET")
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self._config.timeout) as resp:
                body = resp.read()
        except (urllib.error.URLError, OSError) as exc:
            raise ConnectionError(f"GET {url} failed: {exc}") from exc
        return json.loads(body)

    def _post_json(self, url: str, payload: bytes) -> Dict[str, Any]:
        """Perform a POST request with a JSON body and return parsed JSON."""
        req = urllib.request.Request(url, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self._config.timeout) as resp:
                body = resp.read()
        except (urllib.error.URLError, OSError) as exc:
            raise ConnectionError(f"POST {url} failed: {exc}") from exc
        return json.loads(body)


# ---------------------------------------------------------------------------
# Environment wrapper
# ---------------------------------------------------------------------------


class _RelayClient(Protocol):
    def get_state(self) -> PlayerState: ...
    def press_key(self, key: str) -> Dict[str, Any]: ...


class StardewValleyEnv:
    """High-level environment that polls the SMAPI relay at a fixed rate.

    Typical usage::

        env = StardewValleyEnv(host="127.0.0.1", port=24600, fps=60)
        env.reset()
        for _ in range(300):          # ~5 seconds at 60 Hz
            state = env.step()
            print(state.map_name, state.x, state.y)
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        fps: int = DEFAULT_FPS,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._config = EnvConfig(host=host, port=port, fps=fps, timeout=timeout)
        self._client = SMAPIRelayClient(self._config)
        self._running = False
        self._frame_count: int = 0
        self._last_state: Optional[PlayerState] = None

    # -- lifecycle ----------------------------------------------------------

    def reset(self) -> PlayerState:
        """Reset the environment and return the initial state."""
        self._frame_count = 0
        self._running = True
        self._last_state = self._client.get_state()
        logger.info(
            "StardewValleyEnv reset – map=%s pos=(%.1f, %.1f)",
            self._last_state.map_name,
            self._last_state.x,
            self._last_state.y,
        )
        return self._last_state

    def step(self) -> PlayerState:
        """Advance one frame and return the latest player state.

        Blocks just long enough to honour the target FPS.
        """
        if not self._running:
            raise RuntimeError("Call reset() before step()")

        t0 = time.perf_counter()
        self._last_state = self._client.get_state()
        self._frame_count += 1

        # Sleep to maintain target frame rate
        elapsed = time.perf_counter() - t0
        target_dt = 1.0 / self._config.fps
        sleep_time = max(0.0, target_dt - elapsed)
        if sleep_time > 0:
            time.sleep(sleep_time)

        return self._last_state

    def press(self, key: str) -> Dict[str, Any]:
        """Send a key press to the game."""
        return self._client.press_key(key)

    def close(self) -> None:
        """Shut down the environment."""
        self._running = False
        logger.info("StardewValleyEnv closed after %d frames", self._frame_count)

    # -- properties ---------------------------------------------------------

    @property
    def frame_count(self) -> int:
        return self._frame_count

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def config(self) -> EnvConfig:
        return self._config


class StardewValleyEnvironment(Environment):
    """Environment-protocol wrapper backed by the SMAPI HTTP relay.

    This is the plug-and-play adapter used by the generic runner. The older
    ``StardewValleyEnv`` poller remains available for CLI capture loops, while
    this wrapper exposes the standard ``reset``/``step``/``shutdown`` contract.
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        *,
        fps: int = DEFAULT_FPS,
        timeout: float = DEFAULT_TIMEOUT,
        client: _RelayClient | None = None,
        source: str = DEFAULT_SOURCE,
    ) -> None:
        self._config = EnvConfig(host=host, port=port, fps=fps, timeout=timeout)
        self._client: _RelayClient = (
            client if client is not None else SMAPIRelayClient(self._config)
        )
        self._source = source
        self._running = False
        self._frame_count = 0
        self._last_state: PlayerState | None = None
        self._last_frame: bytes | None = None

    def reset(self, seed: int | None = None) -> Observation:
        """Read ``/state`` and return the initial protocol observation."""
        del seed  # Stardew world determinism is controlled by the save file.
        self._frame_count = 0
        self._running = True
        self._last_state = self._client.get_state()
        return self._observation_from_state(self._last_state)

    def step(self, action: Action) -> tuple[Observation, float, bool, dict[str, Any]]:
        """Apply one allowed key action, then read ``/state`` again."""
        if not self._running:
            raise RuntimeError("StardewValleyEnvironment.step called before reset()")

        key = _validate_action(action)
        relay_response: Dict[str, Any] = {"ok": True, "skipped": True}
        if key != NOOP_ACTION:
            relay_response = self._client.press_key(key)

        self._last_state = self._client.get_state()
        self._frame_count += 1
        observation = self._observation_from_state(self._last_state)
        info: dict[str, Any] = {
            "action": key,
            "frame_count": self._frame_count,
            "relay_response": relay_response,
            "source": self._source,
        }
        return observation, 0.0, False, info

    def render_frame(self) -> bytes | None:
        return None

    def last_frame(self) -> bytes | None:
        return self._last_frame

    def shutdown(self) -> None:
        self._running = False
        for method_name in ("shutdown", "close"):
            method = getattr(self._client, method_name, None)
            if callable(method):
                method()
                break

    @property
    def frame_count(self) -> int:
        return self._frame_count

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def config(self) -> EnvConfig:
        return self._config

    def _observation_from_state(self, state: PlayerState) -> Observation:
        return {
            "timestamp": float(state.timestamp),
            "map_name": state.map_name,
            "player_position": {
                "x": state.x,
                "y": state.y,
            },
            "facing": state.facing,
            "keys": {key: bool(state.keys.get(key, False)) for key in ACTION_KEYS},
            "source": self._source,
        }


def _validate_action(action: Action) -> str:
    if not isinstance(action, dict):
        raise ValueError("Stardew action must be a dict")

    raw_key = action.get("key", action.get("action", action.get("op", NOOP_ACTION)))
    if not isinstance(raw_key, str):
        raise ValueError("Stardew action key must be a string")

    key = raw_key.strip().lower()
    if key not in VALID_ACTIONS:
        allowed = ", ".join(VALID_ACTIONS)
        raise ValueError(f"Unsupported Stardew action {raw_key!r}. Allowed: {allowed}")
    return key


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Stardew Valley SMAPI relay extractor (60 Hz)",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"Relay host (default: {DEFAULT_HOST})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Relay port (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--fps",
        type=int,
        default=DEFAULT_FPS,
        help=f"Target frame rate (default: {DEFAULT_FPS})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"HTTP request timeout in seconds (default: {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--frames",
        type=int,
        default=0,
        help="Number of frames to capture (0 = run until Ctrl-C)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output each frame as a single JSON line",
    )
    parser.add_argument(
        "--health-only",
        action="store_true",
        help="Run a single health check and exit",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Entry-point for the CLI.

    Parameters
    ----------
    argv : list[str] | None
        Command-line arguments (defaults to ``sys.argv[1:]``).

    Returns
    -------
    int
        Exit code (0 = success, non-zero = failure).
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(name)-12s  %(levelname)-8s  %(message)s",
    )

    env = StardewValleyEnv(
        host=args.host,
        port=args.port,
        fps=args.fps,
        timeout=args.timeout,
    )

    # -- health-only mode ---------------------------------------------------
    if args.health_only:
        ok = env._client.health_check()
        status = "healthy" if ok else "unreachable"
        print(f"Relay at {env.config.base_url}: {status}")
        return 0 if ok else 1

    # -- main capture loop --------------------------------------------------
    try:
        state = env.reset()
        if args.json:
            print(json.dumps(state.to_dict()))
        else:
            logger.info(
                "Capturing at %d FPS – map=%s pos=(%.1f, %.1f) facing=%s",
                args.fps,
                state.map_name,
                state.x,
                state.y,
                state.facing,
            )

        frame_limit = args.frames if args.frames > 0 else float("inf")
        while env.frame_count < frame_limit:
            state = env.step()
            if args.json:
                print(json.dumps(state.to_dict()))
            else:
                logger.debug(
                    "frame=%-6d map=%-20s pos=(%8.1f, %8.1f) facing=%-5s keys=%s",
                    env.frame_count,
                    state.map_name,
                    state.x,
                    state.y,
                    state.facing,
                    {k: v for k, v in state.keys.items() if v},
                )

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except ConnectionError as exc:
        logger.error("Connection lost: %s", exc)
        return 2
    finally:
        env.close()

    return 0


# ---------------------------------------------------------------------------
# Module entry
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sys.exit(main())
