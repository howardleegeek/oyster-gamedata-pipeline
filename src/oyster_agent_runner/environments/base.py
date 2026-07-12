"""Environment protocol + a deterministic MockEnvironment for tests.

The `Environment` protocol is the single adapter surface every game/sim
plugin must implement. The runner is written entirely against this
protocol — real environments (MineRL, Factorio, gym) are out-of-process
integrations wrapped in sub-modules.

`MockEnvironment` is a pure-Python fake that lets the whole runner be
integration-tested end-to-end without any game installed.

Vision support
--------------
The optional `last_frame()` method exposes the most recent rendered
frame as raw PNG bytes. Vision-aware LLM providers (see
`providers.claude_vision` / `providers.openai_vision`) call this *after*
`reset()` / `step()` so they can include the image in the next
`messages.create` call. Environments that don't implement `last_frame`
are still fully supported — the runner only calls it when the provider
opts in via `wants_vision = True`.
"""

from __future__ import annotations

import struct
import zlib
from typing import Any, Protocol, runtime_checkable

# Type aliases — keep observations/actions as plain dicts so LLM providers
# can serialize them without custom encoders.
Observation = dict[str, Any]
Action = dict[str, Any]


@runtime_checkable
class Environment(Protocol):
    """Adapter surface for a game / simulator.

    Implementations MUST be single-threaded: the runner never calls
    `step` concurrently with `reset` or `shutdown`.
    """

    def reset(self, seed: int | None = None) -> Observation:
        """Start a fresh episode, optionally seeded for determinism.

        Returns the initial observation.
        """
        ...

    def step(self, action: Action) -> tuple[Observation, float, bool, dict[str, Any]]:
        """Apply `action`, advance one timestep.

        Returns
        -------
        observation:
            The post-action environment state.
        reward:
            Scalar reward for this step (0.0 if not applicable).
        done:
            True iff the episode has terminated (success, failure, or
            environment-defined cutoff).
        info:
            Free-form dict for debugging; must be JSON-serializable.
        """
        ...

    def render_frame(self) -> bytes | None:
        """Return a PNG-encoded frame for the current state, or None.

        Real game envs should capture the game's render surface; pure
        simulators may return None and rely on downstream consumers to
        synthesize frames from observations.
        """
        ...

    def shutdown(self) -> None:
        """Release resources. Runner calls this in a finally block."""
        ...


@runtime_checkable
class VisionCapableEnvironment(Environment, Protocol):
    """Optional extension — env can surface its last rendered frame.

    Implemented by `MockEnvironment` and any real env that wants to
    participate in vision-based agent loops. Consumers should use
    `has_vision(env)` rather than `isinstance(env, VisionCapableEnvironment)`
    since not every real env subclasses Protocols explicitly.
    """

    def last_frame(self) -> bytes | None:
        """Return the most recently rendered frame, or None if unavailable."""
        ...


def has_vision(env: object) -> bool:
    """True if `env` exposes a callable `last_frame()` method.

    Structural check — Python Protocols don't enforce at runtime unless
    decorated with `@runtime_checkable` AND the callsite uses
    `isinstance`, but that still requires explicit inheritance for
    non-Protocol subclasses. `hasattr` is the cheapest, most permissive
    check and matches the duck-typing spirit of the rest of the package.
    """
    return callable(getattr(env, "last_frame", None))


# --- MockEnvironment ----------------------------------------------------------


def _tiny_png(
    width: int = 2, height: int = 2, color: tuple[int, int, int] = (128, 128, 128)
) -> bytes:
    """Hand-build a minimal valid PNG — avoids pulling Pillow into the core.

    Deterministic: same (width, height, color) -> identical bytes.
    """
    # PNG signature
    signature = b"\x89PNG\r\n\x1a\n"

    def _chunk(tag: bytes, data: bytes) -> bytes:
        length = struct.pack(">I", len(data))
        crc = struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        return length + tag + data + crc

    # IHDR: width, height, bit_depth=8, color_type=2(RGB), compression=0, filter=0, interlace=0
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)

    # IDAT: one filter byte (0=None) per row + RGB pixels, then zlib-compressed
    r, g, b = color
    raw_rows = b""
    for _ in range(height):
        raw_rows += b"\x00" + bytes([r, g, b]) * width
    idat = zlib.compress(raw_rows, level=9)

    return signature + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


class MockEnvironment:
    """Deterministic fake for tests and CI.

    Observation is `{"step": int, "last_action": dict | None, "seed": int}`.
    Each `step` advances a counter and returns a small reward; `done`
    flips to True after `done_after_steps` steps.
    """

    def __init__(self, done_after_steps: int = 10, reward_per_step: float = 0.1) -> None:
        self.done_after_steps = done_after_steps
        self.reward_per_step = reward_per_step
        self._step_count = 0
        self._seed: int = 0
        self._last_action: Action | None = None
        self._is_shutdown = False
        # Cache last rendered frame so VisionCapableEnvironment protocol works
        # without a fresh render call (and without re-encoding a PNG).
        self._last_frame: bytes | None = None

    def reset(self, seed: int | None = None) -> Observation:
        """Start a fresh episode, optionally seeded for determinism.

        Args:
            seed: Optional seed for reproducible episodes. Defaults to 0.

        Returns:
            Initial observation dict with {"step": 0, "last_action": None, "seed": seed}.
        """
        if self._is_shutdown:
            raise RuntimeError("MockEnvironment is shut down; create a new instance.")
        self._step_count = 0
        self._seed = seed if seed is not None else 0
        self._last_action = None
        self._last_frame = None
        return self._observation()

    def step(self, action: Action) -> tuple[Observation, float, bool, dict[str, Any]]:
        if self._is_shutdown:
            raise RuntimeError("MockEnvironment is shut down; create a new instance.")
        self._step_count += 1
        self._last_action = dict(action)
        done = self._step_count >= self.done_after_steps
        info: dict[str, Any] = {"step_count": self._step_count}
        return self._observation(), self.reward_per_step, done, info

    def render_frame(self) -> bytes | None:
        """Render a deterministic test frame.

        Generates a tiny 2x2 PNG where the grey channel encodes progress
        through done_after_steps. Allows visual pipelines to be tested
        without real screenshots.

        Returns:
            PNG bytes, or None if no frame has been rendered yet.
        """
        # Deterministic tiny PNG whose grey channel encodes progress — lets
        # visual pipelines be tested without real screenshots.
        progress = min(255, int(255 * self._step_count / max(1, self.done_after_steps)))
        self._last_frame = _tiny_png(width=2, height=2, color=(progress, progress, progress))
        return self._last_frame

    def last_frame(self) -> bytes | None:
        """Return the most recently rendered frame (or None if never rendered)."""
        return self._last_frame

    def shutdown(self) -> None:
        """Shut down the environment, marking it as terminated.

        After shutdown, any calls to reset() or step() will raise a
        RuntimeError. This allows explicit cleanup of resources and
        signals to any dependent code that the environment is no longer
        usable.

        Returns:
            None.
        """
        self._is_shutdown = True

    # Internals ---------------------------------------------------------------

    def _observation(self) -> Observation:
        return {
            "step": self._step_count,
            "last_action": self._last_action,
            "seed": self._seed,
        }


__all__ = [
    "Action",
    "Environment",
    "MockEnvironment",
    "Observation",
    "VisionCapableEnvironment",
    "has_vision",
]
