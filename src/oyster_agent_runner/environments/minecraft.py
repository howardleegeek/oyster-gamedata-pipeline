"""Minecraft environment adapter — Mineflayer subprocess + MineRL stub.

Two integration paths are supported:

Path A — Mineflayer (Phase 1 LIVE)
----------------------------------
A Node.js subprocess (`mineflayer/bot.js`) connects to a local Paper /
Spigot 1.20.x server via the Minecraft network protocol. The Python
process drives the bot through a JSON-line stdio protocol documented in
`mineflayer/protocol.md`. The bot exposes:

  - `bot.entity.position`, `bot.health`, `bot.food`
  - `bot.inventory.slots`
  - `bot.findBlocks(...)` for nearby blocks
  - `bot.entities` for nearby mobs

Phase 1 uses four actions: `move_to`, `dig`, `look`, `chat`. The full
spec lives in `docs/MINECRAFT_TRAJECTORY_SPEC.md`.

Path B — MineRL (still stubbed)
-------------------------------
MineRL Gym env (research benchmark, pixel obs). Out of scope for Phase 1
— left as a stub so future work can plug it in without changing the
public Environment interface.

Runtime
-------
The mineflayer path requires:
  1. Node.js >= 18 installed somewhere on `PATH`
  2. `npm install` already run in `mineflayer/` (this module does NOT
     auto-install; the operator does it once per host — see
     `docs/PHASE1_RUNBOOK.md`)
  3. A reachable Minecraft server at `host:port` (default
     `localhost:25565`)

If any of those preconditions are missing, `reset(...)` raises a
`RuntimeError` with a remediation hint — never a silent hang.

For unit tests, the subprocess is *not* spawned. Callers either mock
`MineflayerProcess` directly or use `MockEnvironment` for end-to-end
runner tests. See `tests/test_minecraft_env_protocol.py`.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Literal

from oyster_agent_runner.environments.base import Action, Environment, Observation

MinecraftPath = Literal["minerl", "mineflayer"]

PROTOCOL_VERSION = 1
DEFAULT_BOT_USERNAME = "oyster_bot"
DEFAULT_HELLO_TIMEOUT_SEC = 30.0
DEFAULT_SPAWN_TIMEOUT_SEC = 60.0
DEFAULT_ACTION_TIMEOUT_SEC = 30.0

_NOT_IMPLEMENTED_MSG_MINERL = (
    "MineRL path is not implemented in Phase 1. "
    "Use path='mineflayer' (default) and connect to a local Paper server."
)

# Default location of the Mineflayer subprocess script — lives at the repo
# root next to `pyproject.toml`. Tests override this via the
# `bot_script` kwarg.
_DEFAULT_BOT_SCRIPT = Path(__file__).resolve().parents[3] / "mineflayer" / "bot.js"


# --- Protocol-driven subprocess wrapper -------------------------------------


class MineflayerProcessError(RuntimeError):
    """Raised when the Mineflayer subprocess misbehaves (timeout, fatal error,
    protocol violation). Caller should consider the environment dead."""


class MineflayerProcess:
    """Owns a long-lived Mineflayer Node.js subprocess.

    Pure-Python: no `subprocess.Popen` calls happen in `__init__`. Call
    `start()` to spawn — that way callers can construct + inspect this
    object in tests without touching Node.

    The reader thread continuously drains stdout, splits on `\\n`, parses
    each line as JSON, and routes:
      - `observation` (with matching id) → into `_pending` keyed by id
      - `error`(fatal=True) → records on `_fatal_error` so subsequent calls
        raise instead of hanging
      - `spawn` → into `_spawn_event`
      - everything else → into `_other_messages` (debug surface for tests)

    The writer side is just `_send(...)`, which holds a lock so concurrent
    senders can't interleave a half-line.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 25565,
        *,
        username: str = DEFAULT_BOT_USERNAME,
        version: str | None = None,
        bot_script: Path | None = None,
        node_executable: str | None = None,
        hello_timeout_sec: float = DEFAULT_HELLO_TIMEOUT_SEC,
        spawn_timeout_sec: float = DEFAULT_SPAWN_TIMEOUT_SEC,
        action_timeout_sec: float = DEFAULT_ACTION_TIMEOUT_SEC,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.version = version
        self.bot_script = Path(bot_script) if bot_script is not None else _DEFAULT_BOT_SCRIPT
        self.node_executable = node_executable
        self.hello_timeout_sec = hello_timeout_sec
        self.spawn_timeout_sec = spawn_timeout_sec
        self.action_timeout_sec = action_timeout_sec

        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._lock = threading.Lock()
        self._cv = threading.Condition()  # signals when new data arrived

        # State updated by the reader thread.
        self._hello_ack: dict[str, Any] | None = None
        self._spawn_event: dict[str, Any] | None = None
        # Pending observations keyed by request id. The dispatcher picks
        # them up via `wait_for_response(id)`.
        self._pending: dict[int, dict[str, Any]] = {}
        # Asynchronous non-fatal/fatal errors emitted out-of-band.
        self._error_log: deque[dict[str, Any]] = deque(maxlen=100)
        self._fatal_error: dict[str, Any] | None = None
        # Other messages we don't yet handle (forward-compat surface).
        self._other_messages: deque[dict[str, Any]] = deque(maxlen=100)
        self._next_action_id = 1
        self._closed = False

    # Lifecycle ---------------------------------------------------------------

    def start(self) -> dict[str, Any]:
        """Spawn the Node subprocess, perform handshake + spawn wait.

        Returns the `spawn` payload (initial Observation). Raises
        `MineflayerProcessError` if anything fails — the caller should
        consider this object dead and not retry.
        """
        if self._proc is not None:
            raise MineflayerProcessError("MineflayerProcess.start called twice")
        self._validate_preconditions()

        node = self.node_executable or shutil.which("node") or "node"
        cmd = [
            node,
            str(self.bot_script),
            "--host",
            str(self.host),
            "--port",
            str(self.port),
            "--username",
            str(self.username),
        ]
        if self.version:
            cmd.extend(["--version", str(self.version)])

        try:
            self._proc = subprocess.Popen(  # noqa: S603 — args are not user input
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
                bufsize=0,
                env=os.environ.copy(),
            )
        except FileNotFoundError as exc:
            raise MineflayerProcessError(
                f"Could not exec Node ({node!r}). Install Node 18+ and ensure it's on PATH."
            ) from exc
        except OSError as exc:
            raise MineflayerProcessError(f"Failed to spawn bot subprocess: {exc}") from exc

        # Drain stdout in a background thread.
        self._reader = threading.Thread(
            target=self._read_loop, name="mineflayer-reader", daemon=True
        )
        self._reader.start()

        # Handshake.
        self._send({"type": "hello"})
        ack = self._wait_for_hello_ack(self.hello_timeout_sec)
        self._hello_ack = ack

        # Wait for the bot to actually spawn in the world.
        spawn = self._wait_for_spawn(self.spawn_timeout_sec)
        self._spawn_event = spawn
        return spawn

    def shutdown(self) -> None:
        """Send `shutdown`, wait briefly for `goodbye`, then terminate."""
        if self._closed:
            return
        self._closed = True
        if self._proc is None:
            return
        # Best-effort: try the polite path.
        with contextlib.suppress(Exception):
            self._send({"type": "shutdown"})
        try:
            self._proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        # Reader thread will exit naturally once stdout closes.

    # Public messaging --------------------------------------------------------

    def send_action(self, action: dict[str, Any]) -> dict[str, Any]:
        """Dispatch an action, block until its observation comes back.

        Returns the parsed observation dict. Raises
        `MineflayerProcessError` on timeout or fatal error.
        """
        action_id = self._next_action_id
        self._next_action_id += 1
        self._send({"type": "action", "id": action_id, "action": action})
        return self.wait_for_response(action_id, self.action_timeout_sec)

    def wait_for_response(self, action_id: int, timeout_sec: float) -> dict[str, Any]:
        """Block until the observation for `action_id` arrives or we time out.

        Public for tests: a fake reader thread can populate `_pending`
        directly and the rest of the env will work.
        """
        deadline = time.monotonic() + timeout_sec
        with self._cv:
            while True:
                if self._fatal_error is not None:
                    raise MineflayerProcessError(
                        f"bot reported fatal error: {self._fatal_error.get('error')}"
                    )
                if action_id in self._pending:
                    return self._pending.pop(action_id)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MineflayerProcessError(
                        f"timeout waiting for action {action_id} (waited {timeout_sec}s)"
                    )
                self._cv.wait(timeout=remaining)

    # Inspection (tests use these) -------------------------------------------

    @property
    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def hello_ack(self) -> dict[str, Any] | None:
        return self._hello_ack

    @property
    def spawn_event(self) -> dict[str, Any] | None:
        return self._spawn_event

    def drain_errors(self) -> list[dict[str, Any]]:
        """Return + clear the async error log (for diagnostics)."""
        out = list(self._error_log)
        self._error_log.clear()
        return out

    # Reader thread internals -------------------------------------------------

    def _read_loop(self) -> None:
        """Consume stdout line-by-line, route messages, signal cv on update."""
        assert self._proc is not None and self._proc.stdout is not None
        try:
            for raw in self._proc.stdout:
                if not raw:
                    continue
                try:
                    line = raw.decode("utf-8").rstrip("\r\n")
                except UnicodeDecodeError:
                    continue
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg, dict):
                    continue
                self._route(msg)
        finally:
            # Stdout closed → wake any waiters so they can fail fast.
            with self._cv:
                self._cv.notify_all()

    def _route(self, msg: dict[str, Any]) -> None:
        mtype = msg.get("type")
        with self._cv:
            if mtype == "observation" and isinstance(msg.get("id"), int):
                self._pending[msg["id"]] = msg
            elif mtype == "spawn":
                self._spawn_event = msg
            elif mtype == "hello_ack":
                self._hello_ack = msg
            elif mtype == "error":
                self._error_log.append(msg)
                if msg.get("fatal"):
                    self._fatal_error = msg
            elif mtype == "goodbye":
                self._closed = True
            else:
                self._other_messages.append(msg)
            self._cv.notify_all()

    def _send(self, message: dict[str, Any]) -> None:
        """Write one JSON-line to the bot's stdin. Thread-safe."""
        if self._proc is None or self._proc.stdin is None:
            raise MineflayerProcessError("subprocess not started")
        wire = {"v": PROTOCOL_VERSION, **message}
        line = json.dumps(wire, separators=(",", ":")) + "\n"
        with self._lock:
            try:
                self._proc.stdin.write(line.encode("utf-8"))
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise MineflayerProcessError(f"failed to write to bot stdin: {exc}") from exc

    def _wait_for_hello_ack(self, timeout_sec: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_sec
        with self._cv:
            while self._hello_ack is None:
                if self._fatal_error is not None:
                    raise MineflayerProcessError(
                        f"bot died before hello_ack: {self._fatal_error.get('error')}"
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MineflayerProcessError(
                        f"timeout waiting for hello_ack ({timeout_sec}s). "
                        f"Is the bot script at {self.bot_script}?"
                    )
                self._cv.wait(timeout=remaining)
            return self._hello_ack

    def _wait_for_spawn(self, timeout_sec: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_sec
        with self._cv:
            while self._spawn_event is None:
                if self._fatal_error is not None:
                    raise MineflayerProcessError(
                        f"bot died before spawn: {self._fatal_error.get('error')}"
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MineflayerProcessError(
                        f"timeout waiting for spawn ({timeout_sec}s). "
                        f"Is the Minecraft server reachable at {self.host}:{self.port}?"
                    )
                self._cv.wait(timeout=remaining)
            return self._spawn_event

    def _validate_preconditions(self) -> None:
        if not self.bot_script.exists():
            raise MineflayerProcessError(
                f"bot script not found: {self.bot_script}. Did you clone the repo correctly?"
            )
        node_modules = self.bot_script.parent / "node_modules"
        if not node_modules.exists():
            raise MineflayerProcessError(
                f"Mineflayer dependencies not installed. Run "
                f"`cd {self.bot_script.parent} && npm install` once. "
                f"See docs/PHASE1_RUNBOOK.md."
            )
        node = self.node_executable or shutil.which("node")
        if node is None:
            raise MineflayerProcessError(
                "Node.js not found on PATH. Install Node 18+. See docs/PHASE1_RUNBOOK.md."
            )


# --- Environment adapter ----------------------------------------------------


class MinecraftEnvironment(Environment):
    """`Environment` implementation backed by Mineflayer (Phase 1).

    Phase 1 deliberately ships WITHOUT video. `last_frame()` returns
    `None` and `render_frame()` returns `None` — Phase 2 will hook the
    OBS / spectator-client pipeline in.

    Parameters
    ----------
    host / port:
        Server connection params for the Mineflayer path.
    path:
        `"mineflayer"` (default, live) or `"minerl"` (stub).
    process:
        Inject a custom `MineflayerProcess` (used by tests). Default:
        construct a fresh one when `reset()` is called.
    metadata_callback:
        Optional callable invoked on every observation we receive from
        the bot, used by the runner to write `metadata.jsonl`. Receives
        the raw observation dict (after JSON parse).
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 25565,
        *,
        path: MinecraftPath = "mineflayer",
        minerl_env_id: str = "MineRLObtainDiamond-v0",
        process: MineflayerProcess | None = None,
        username: str = DEFAULT_BOT_USERNAME,
        bot_script: Path | None = None,
        action_timeout_sec: float = DEFAULT_ACTION_TIMEOUT_SEC,
        spawn_timeout_sec: float = DEFAULT_SPAWN_TIMEOUT_SEC,
        hello_timeout_sec: float = DEFAULT_HELLO_TIMEOUT_SEC,
        metadata_callback=None,
    ) -> None:
        self.host = host
        self.port = port
        self.path: MinecraftPath = path
        self.minerl_env_id = minerl_env_id
        self.username = username
        self._bot_script = bot_script
        self._action_timeout_sec = action_timeout_sec
        self._spawn_timeout_sec = spawn_timeout_sec
        self._hello_timeout_sec = hello_timeout_sec
        self._injected_process = process
        self._metadata_callback = metadata_callback
        self._proc: MineflayerProcess | None = None
        # Phase 2: video frames. Phase 1 always returns None.
        self._last_frame: bytes | None = None

    # Environment protocol ----------------------------------------------------

    def reset(self, seed: int | None = None) -> Observation:
        """Spawn the Mineflayer bot (or reuse an injected one) and return the
        spawn observation.

        `seed` is currently advisory — the world seed is set on the
        server, not the bot. We surface it on the returned observation
        so trajectory consumers can correlate.
        """
        if self.path == "minerl":
            raise NotImplementedError(_NOT_IMPLEMENTED_MSG_MINERL)
        if self.path != "mineflayer":
            raise ValueError(f"unknown minecraft path: {self.path!r}")

        # Tear down any prior process (idempotent reset).
        self._teardown_process()

        if self._injected_process is not None:
            proc = self._injected_process
            # Allow tests to inject a "pre-started" process.
            spawn = proc.spawn_event if proc.spawn_event is not None else proc.start()
        else:
            proc = MineflayerProcess(
                host=self.host,
                port=self.port,
                username=self.username,
                bot_script=self._bot_script,
                action_timeout_sec=self._action_timeout_sec,
                spawn_timeout_sec=self._spawn_timeout_sec,
                hello_timeout_sec=self._hello_timeout_sec,
            )
            spawn = proc.start()
        self._proc = proc

        observation = _spawn_to_observation(spawn, seed=seed)
        if self._metadata_callback is not None:
            # Never let a logging callback kill the run.
            with contextlib.suppress(Exception):
                self._metadata_callback(observation)
        return observation

    def step(self, action: Action) -> tuple[Observation, float, bool, dict[str, Any]]:
        if self._proc is None:
            raise RuntimeError("MinecraftEnvironment.step called before reset()")
        try:
            response = self._proc.send_action(dict(action))
        except MineflayerProcessError as exc:
            # Surface as a runtime error so the runner's fail-safe can
            # catch it and abort. Don't silently mask.
            raise RuntimeError(f"mineflayer subprocess error: {exc}") from exc

        observation = _observation_message_to_dict(response)
        if self._metadata_callback is not None:
            with contextlib.suppress(Exception):
                self._metadata_callback(observation)

        # Phase 1 has no reward signal and no automatic done detection
        # — the runner relies on `success_criteria` evaluation post-hoc
        # and on `max_steps`. We expose the bot-reported error string in
        # `info` so trajectories stay debuggable.
        info: dict[str, Any] = {
            "ok": bool(response.get("ok")),
            "tick": response.get("tick"),
            "error": response.get("error"),
        }
        return observation, 0.0, False, info

    def render_frame(self) -> bytes | None:
        # Phase 1: no video stream. Phase 2 will plug in the OBS / spectator
        # pipeline. Return None so the runner's `_safe_render` path stays
        # silent (it tolerates None gracefully).
        return None

    def last_frame(self) -> bytes | None:
        return self._last_frame

    def shutdown(self) -> None:
        self._teardown_process()

    # Internals ---------------------------------------------------------------

    def _teardown_process(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.shutdown()
        finally:
            self._proc = None


# --- Helpers ---------------------------------------------------------------


def _spawn_to_observation(spawn: dict[str, Any], *, seed: int | None) -> Observation:
    """Convert the bot's `spawn` payload into a serializable Observation."""
    return {
        "kind": "spawn",
        "position": spawn.get("position"),
        "yaw": spawn.get("yaw"),
        "pitch": spawn.get("pitch"),
        "health": spawn.get("health"),
        "food": spawn.get("food"),
        "xp": spawn.get("xp"),
        "game_mode": spawn.get("game_mode"),
        "dimension": spawn.get("dimension"),
        "world_seed": spawn.get("world_seed"),
        "seed_hint": seed,
    }


def _observation_message_to_dict(msg: dict[str, Any]) -> Observation:
    """Project a bot `observation` message into the Environment Observation
    contract expected by the runner. We pass through the major fields verbatim
    because downstream consumers (CoT viewer, alignment proof) want them all.
    """
    return {
        "kind": "observation",
        "tick": msg.get("tick"),
        "ok": bool(msg.get("ok")),
        "error": msg.get("error"),
        "bot": msg.get("bot"),
        "inventory": msg.get("inventory") or [],
        "blocks_near": msg.get("blocks_near") or [],
        "entities_near": msg.get("entities_near") or [],
        "task_state": msg.get("task_state"),
    }


__all__ = [
    "DEFAULT_ACTION_TIMEOUT_SEC",
    "DEFAULT_BOT_USERNAME",
    "DEFAULT_HELLO_TIMEOUT_SEC",
    "DEFAULT_SPAWN_TIMEOUT_SEC",
    "MinecraftEnvironment",
    "MinecraftPath",
    "MineflayerProcess",
    "MineflayerProcessError",
    "PROTOCOL_VERSION",
]
