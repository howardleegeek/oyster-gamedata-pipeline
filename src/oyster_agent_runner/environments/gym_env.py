"""Gymnasium / OpenAI-Gym environment adapter.

Integration target: `gymnasium` (the maintained fork of OpenAI Gym,
https://gymnasium.farama.org). Classic control, Atari (via
`ale-py`), Procgen, MineRL (registered as gym envs), and MiniGrid
are all drop-in once `gymnasium` is pip-installed.

Runtime behavior
----------------
This module prefers the *real* wrapper when `gymnasium` is importable
and gracefully falls back to a `NotImplementedError` stub otherwise,
so the base test suite doesn't require the numpy/pygame/Box2D stack.

Production deployments should `pip install "gymnasium[classic-control]"`
(or the relevant extra) alongside this package.
"""

from __future__ import annotations

import io
import logging
from typing import Any

from oyster_agent_runner.environments.base import Action, Environment, Observation

logger = logging.getLogger(__name__)

# Lazy-import gymnasium — failing to import is NOT an error, it just means
# callers get the scaffold stub. The real wrapper is constructed below when
# the import succeeds.
try:
    import gymnasium  # type: ignore[import-untyped]

    _GYMNASIUM_AVAILABLE = True
except ImportError:
    gymnasium = None  # type: ignore[assignment]
    _GYMNASIUM_AVAILABLE = False


_NOT_IMPLEMENTED_MSG = (
    "GymEnvironment is a scaffold stub. Real integration pending: "
    "`pip install gymnasium[extras]` and wrap `gymnasium.make(env_id)`. "
    "Use MockEnvironment for tests."
)


def _observation_to_dict(obs: Any) -> Observation:
    """Normalize gym observations (np.ndarray / tuple / dict) to a JSON-serializable dict.

    Uses `.tolist()` for numpy arrays when available so the trajectory
    logger can serialize without custom encoders.
    """
    if isinstance(obs, dict):
        return {str(k): _observation_to_dict(v) for k, v in obs.items()}  # type: ignore[misc]
    # numpy array → list (duck-typed — we don't hard-depend on numpy here).
    tolist = getattr(obs, "tolist", None)
    if callable(tolist):
        try:
            return {"array": tolist()}
        except Exception as exc:
            logger.warning("tolist() failed on observation: %s", exc)
    if isinstance(obs, (list, tuple)):
        return {"values": list(obs)}
    if isinstance(obs, (int, float, bool, str)) or obs is None:
        return {"value": obs}
    # Fallback — string repr keeps the trajectory readable even if we don't
    # know the exact type.
    return {"repr": repr(obs)}


def _unwrap_action(action: Action) -> Any:
    """Extract the raw action value from the agent's `{"op": ..., "value": ...}` dict.

    The agent emits structured actions (see runner's `SYSTEM_PROMPT_TEMPLATE`),
    but gym envs expect scalars / arrays. Convention: if the dict has a
    single `"value"` key, pass it through; otherwise pass the whole dict
    and let the caller-side gym wrapper complain.
    """
    if isinstance(action, dict) and "value" in action and len(action) in (1, 2):
        return action["value"]
    # Common alias — many gym envs use `action` as the key name.
    if isinstance(action, dict) and "action" in action and len(action) in (1, 2):
        return action["action"]
    return action


class GymEnvironment(Environment):
    """Gymnasium wrapper — uses the real SDK when installed, stubs otherwise.

    Constructor takes the gymnasium env id (e.g. 'MountainCarContinuous-v0',
    'ALE/Breakout-v5') so the real implementation can be swapped in without
    touching caller code.
    """

    def __init__(self, env_id: str, *, render_mode: str | None = "rgb_array") -> None:
        self.env_id = env_id
        self.render_mode = render_mode
        self._env: Any = None
        self._last_frame: bytes | None = None
        self._is_shutdown = False

        if _GYMNASIUM_AVAILABLE:
            # Lazy-construct on first reset() so import-time errors are
            # surfaced at use-time — matches gymnasium's own ergonomics.
            pass

    # Capability flag so tests (and the runner) can branch cleanly.
    @property
    def is_stub(self) -> bool:
        return not _GYMNASIUM_AVAILABLE

    def reset(self, seed: int | None = None) -> Observation:
        if not _GYMNASIUM_AVAILABLE:
            raise NotImplementedError(_NOT_IMPLEMENTED_MSG)
        if self._is_shutdown:
            raise RuntimeError("GymEnvironment is shut down; create a new instance.")
        if self._env is None:
            self._env = gymnasium.make(self.env_id, render_mode=self.render_mode)
        raw_obs, info = self._env.reset(seed=seed)
        obs = _observation_to_dict(raw_obs)
        obs["_info"] = _observation_to_dict(info) if info else {}
        return obs

    def step(self, action: Action) -> tuple[Observation, float, bool, dict[str, Any]]:
        if not _GYMNASIUM_AVAILABLE:
            raise NotImplementedError(_NOT_IMPLEMENTED_MSG)
        if self._env is None:
            raise RuntimeError("Call reset() before step().")
        unwrapped = _unwrap_action(action)
        raw_obs, reward, terminated, truncated, info = self._env.step(unwrapped)
        done = bool(terminated or truncated)
        obs = _observation_to_dict(raw_obs)
        return (
            obs,
            float(reward),
            done,
            {"terminated": bool(terminated), "truncated": bool(truncated), **(info or {})},
        )

    def render_frame(self) -> bytes | None:
        if not _GYMNASIUM_AVAILABLE or self._env is None:
            return None
        try:
            frame = self._env.render()
        except Exception as exc:
            logger.debug("render_frame: gym render() failed: %s", exc)
            return None
        if frame is None:
            return None
        # `render()` returns np.ndarray under rgb_array mode; encode as PNG.
        png = _array_to_png(frame)
        if png is not None:
            self._last_frame = png
        return png

    def last_frame(self) -> bytes | None:
        return self._last_frame

    def shutdown(self) -> None:
        import contextlib

        self._is_shutdown = True
        if self._env is not None:
            with contextlib.suppress(Exception):
                self._env.close()
            self._env = None


def _array_to_png(arr: Any) -> bytes | None:
    """Encode an H×W×3 uint8 numpy array as PNG. Returns None on failure.

    Tries Pillow first (production path); falls back to the hand-rolled
    tiny-PNG encoder from `base` for 2×2 probe frames if Pillow isn't
    installed. Real gym envs produce H×W arrays too large for the tiny
    encoder, so Pillow is required in production.
    """
    try:
        from PIL import Image  # type: ignore[import-not-found]

        img = Image.fromarray(arr)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except ImportError:
        return None
    except Exception as exc:
        logger.debug("_array_to_png failed: %s", exc)
        return None


__all__ = ["GymEnvironment"]
