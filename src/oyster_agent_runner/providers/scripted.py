"""ScriptedProvider — deterministic random-walk Mineflayer driver.

Why this exists
---------------
The 100-iter brute-force sprint surfaced that ``MockLLMProvider``'s
``noop`` actions never move the bot. Every output bundle in the sprint
had ``unique_camera_positions=1`` — schema-conformant but training-
useless. ScriptedProvider closes that gap by emitting randomized walk
+ look + dig commands so the bot actually moves through the world,
producing non-degenerate ``player_position`` / ``camera_position``
trajectories.

Design constraints
------------------
1. **Zero LLM cost** — runs offline, no API calls.
2. **Deterministic** — same seed → same trajectory, byte-for-byte.
   Lets QA / sprint validation be reproducible.
3. **Drop-in for `mock`** — same ``LLMProvider`` Protocol contract,
   works with any ``oyster-agent run-mc`` invocation by passing
   ``--provider scripted``.
4. **Minecraft-aware** — emits actions ``bot.js`` understands
   (``move_to``, ``look``, ``dig``, ``noop``), pulled from the
   observation's ``bot.position`` so moves are local-relative, not
   absolute teleports.

Action mix (round-robin, weighted)
----------------------------------
* 60% ``move_to`` — random target within a 3-block radius of the
  bot's current XZ position (Y stays the same).
* 25% ``look`` — random yaw/pitch within reasonable view bounds.
* 10% ``noop`` — single-tick pause; useful so trajectories aren't
  100% movement.
* 5%  ``dig`` — random adjacent block; usually fails (bot might not
  be next to a diggable block) but exercises the dig pathway.

Failure handling
----------------
Mineflayer ``move_to`` may fail if the target is unreachable
(pathfinder timeout, blocked path). ScriptedProvider doesn't care —
the bot will simply not move that step. This is OK because the next
step will pick a different random target.
"""

from __future__ import annotations

import json
import random
import re
from typing import Any

# Match the OBSERVATION-format the runner injects as the latest user
# message: ``[step N] observation:\n{...}``. We parse out the JSON
# body and pull ``bot.position`` (steady-state) or ``position``
# (spawn) as the anchor for the next move.
_OBS_LINE_RE = re.compile(r"\[step \d+\] observation:\n(.+)", re.DOTALL)


def _extract_bot_xyz(text: str) -> tuple[float, float, float] | None:
    match = _OBS_LINE_RE.search(text)
    if match is None:
        return None
    try:
        obs = json.loads(match.group(1).strip())
    except json.JSONDecodeError:
        return None
    bot = obs.get("bot")
    if isinstance(bot, dict):
        pos = bot.get("position")
        if isinstance(pos, list) and len(pos) >= 3:
            try:
                return float(pos[0]), float(pos[1]), float(pos[2])
            except (TypeError, ValueError):
                return None
        if isinstance(pos, dict):
            try:
                return float(pos["x"]), float(pos["y"]), float(pos["z"])
            except (KeyError, TypeError, ValueError):
                return None
    pos = obs.get("position")
    if isinstance(pos, list) and len(pos) >= 3:
        try:
            return float(pos[0]), float(pos[1]), float(pos[2])
        except (TypeError, ValueError):
            return None
    return None


class ScriptedProvider:
    """Deterministic randomized Mineflayer action sequence.

    Parameters
    ----------
    seed : int
        RNG seed; identical seeds produce byte-identical action sequences.
    move_radius : float
        Max XZ offset for ``move_to`` targets, in blocks. Default 3.0.
    """

    def __init__(self, seed: int = 0, move_radius: float = 3.0) -> None:
        self._rng = random.Random(seed)
        self._move_radius = float(move_radius)
        self.call_count = 0

    def chat(self, system: str, messages: list[dict], temperature: float) -> str:
        del system, temperature  # unused — keeps Protocol contract
        self.call_count += 1
        latest_user_text = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                content = m.get("content", "")
                latest_user_text = content if isinstance(content, str) else str(content)
                break
        action = self._next_action(latest_user_text)
        return f"scripted action #{self.call_count}\n<action>{json.dumps(action)}</action>"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _next_action(self, latest_user_text: str) -> dict[str, Any]:
        roll = self._rng.random()
        if roll < 0.60:
            return self._move_to_action(latest_user_text)
        if roll < 0.85:
            return self._look_action()
        if roll < 0.95:
            return {"op": "noop"}
        return self._dig_action(latest_user_text)

    def _move_to_action(self, latest_user_text: str) -> dict[str, Any]:
        pos = _extract_bot_xyz(latest_user_text)
        if pos is None:
            return {"op": "noop"}
        x, y, z = pos
        dx = self._rng.uniform(-self._move_radius, self._move_radius)
        dz = self._rng.uniform(-self._move_radius, self._move_radius)
        return {"op": "move_to", "target": [round(x + dx, 2), y, round(z + dz, 2)]}

    def _look_action(self) -> dict[str, Any]:
        # Yaw [-pi, pi], pitch [-0.5, 0.5] rad — keeps look reasonable
        # and exercises the rotation pathway in the buyer-spec record.
        yaw = self._rng.uniform(-3.14159, 3.14159)
        pitch = self._rng.uniform(-0.5, 0.5)
        return {"op": "look", "yaw": round(yaw, 4), "pitch": round(pitch, 4)}

    def _dig_action(self, latest_user_text: str) -> dict[str, Any]:
        pos = _extract_bot_xyz(latest_user_text)
        if pos is None:
            return {"op": "noop"}
        x, y, z = pos
        # Adjacent block one step in a random cardinal direction; usually
        # not a diggable block, but the bot.js handler handles failure
        # cleanly.
        direction = self._rng.choice([(1, 0), (-1, 0), (0, 1), (0, -1)])
        return {
            "op": "dig",
            "target": [
                int(round(x + direction[0])),
                int(round(y) - 1),
                int(round(z + direction[1])),
            ],
        }


__all__ = ["ScriptedProvider"]
