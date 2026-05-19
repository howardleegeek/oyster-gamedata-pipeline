"""Unit tests for the Mineflayer JSON-line protocol parser.

These tests do NOT spawn a real Node subprocess and do NOT require a
running Minecraft server. They exercise:

  1. The `MineflayerProcess._route` method by injecting protocol
     messages directly and asserting state transitions.
  2. The full reset() / step() / shutdown() lifecycle of
     `MinecraftEnvironment` with a fake `MineflayerProcess` that pretends
     to be a real subprocess.

Integration tests (real Node, real server) are documented in
`docs/PHASE1_RUNBOOK.md` as a manual operator step — they're out of scope
for CI.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from oyster_agent_runner.environments.minecraft import (
    MinecraftEnvironment,
    MineflayerProcess,
    MineflayerProcessError,
)

# --- Direct _route tests ----------------------------------------------------


def _new_unstarted_process() -> MineflayerProcess:
    """Build an instance without ever calling start()."""
    return MineflayerProcess(host="ignored", port=0)


def test_route_observation_lands_in_pending_keyed_by_id() -> None:
    proc = _new_unstarted_process()
    proc._route(
        {"type": "observation", "id": 7, "ok": True, "tick": 12, "bot": {"position": [1, 2, 3]}}
    )
    assert 7 in proc._pending
    assert proc._pending[7]["ok"] is True


def test_route_hello_ack_sets_state() -> None:
    proc = _new_unstarted_process()
    proc._route({"type": "hello_ack", "bot": "oyster_bot", "mineflayer_version": "4.20.0"})
    assert proc.hello_ack is not None
    assert proc.hello_ack["bot"] == "oyster_bot"


def test_route_spawn_event_sets_state() -> None:
    proc = _new_unstarted_process()
    payload = {
        "type": "spawn",
        "position": [123.5, 64.0, -80.2],
        "yaw": 87.3,
        "pitch": -5.1,
        "health": 20,
        "food": 20,
        "xp": 0,
        "game_mode": "survival",
        "dimension": "overworld",
    }
    proc._route(payload)
    assert proc.spawn_event == payload


def test_route_fatal_error_marks_process_dead() -> None:
    proc = _new_unstarted_process()
    proc._route({"type": "error", "fatal": True, "error": "kicked: server full"})
    # _fatal_error is set; subsequent wait_for_response should raise.
    with pytest.raises(MineflayerProcessError, match="kicked: server full"):
        proc.wait_for_response(action_id=1, timeout_sec=0.05)


def test_route_non_fatal_error_logs_but_does_not_kill() -> None:
    proc = _new_unstarted_process()
    proc._route({"type": "error", "fatal": False, "error": "transient connectivity blip"})
    errors = proc.drain_errors()
    assert len(errors) == 1
    assert "transient" in errors[0]["error"]
    # No fatal flag → _fatal_error stays None.
    assert proc._fatal_error is None


def test_route_unknown_message_type_lands_in_other_messages() -> None:
    proc = _new_unstarted_process()
    proc._route({"type": "future_message_type", "data": [1, 2, 3]})
    assert any(m.get("type") == "future_message_type" for m in proc._other_messages)


def test_wait_for_response_returns_immediately_if_present() -> None:
    proc = _new_unstarted_process()
    proc._route({"type": "observation", "id": 42, "ok": True, "tick": 99})
    obs = proc.wait_for_response(action_id=42, timeout_sec=0.1)
    assert obs["ok"] is True
    assert obs["tick"] == 99
    # Pop semantics — the second wait should time out.
    with pytest.raises(MineflayerProcessError, match="timeout"):
        proc.wait_for_response(action_id=42, timeout_sec=0.05)


def test_wait_for_response_blocks_until_message_arrives() -> None:
    """Reader thread injects an observation while a waiter is parked."""
    proc = _new_unstarted_process()

    def producer() -> None:
        # Tiny sleep so the waiter is definitely parked first.
        import time as _t

        _t.sleep(0.05)
        proc._route({"type": "observation", "id": 1, "ok": True, "tick": 1, "bot": None})

    t = threading.Thread(target=producer, daemon=True)
    t.start()
    obs = proc.wait_for_response(action_id=1, timeout_sec=2.0)
    t.join(timeout=0.5)
    assert obs["ok"] is True


def test_wait_for_response_times_out() -> None:
    proc = _new_unstarted_process()
    with pytest.raises(MineflayerProcessError, match="timeout waiting for action"):
        proc.wait_for_response(action_id=99, timeout_sec=0.05)


# --- MinecraftEnvironment lifecycle with a fake process ---------------------


class _FakeProcess:
    """A drop-in for `MineflayerProcess` that records every interaction.

    Tests inject this via `MinecraftEnvironment(process=fake)` to exercise
    the env's reset/step/shutdown plumbing without ever spawning Node.
    """

    def __init__(self, spawn_payload: dict[str, Any] | None = None) -> None:
        self._spawn_payload = spawn_payload or {
            "position": [0.5, 64.0, 0.5],
            "yaw": 0.0,
            "pitch": 0.0,
            "health": 20,
            "food": 20,
            "xp": 0,
            "game_mode": "survival",
            "dimension": "overworld",
            "world_seed": None,
        }
        self.start_called = False
        self.shutdown_called = False
        self.actions_sent: list[dict[str, Any]] = []
        self._next_response: dict[str, Any] | None = None
        self.spawn_event = None  # forces env.reset() to call start()

    def start(self) -> dict[str, Any]:
        self.start_called = True
        self.spawn_event = self._spawn_payload
        return self._spawn_payload

    def queue_response(self, response: dict[str, Any]) -> None:
        self._next_response = response

    def send_action(self, action: dict[str, Any]) -> dict[str, Any]:
        self.actions_sent.append(action)
        if self._next_response is None:
            # Default reply: ok=True, empty inventory.
            return {
                "type": "observation",
                "id": len(self.actions_sent),
                "ok": True,
                "tick": 100 + len(self.actions_sent),
                "bot": {
                    "position": [1, 64, 1],
                    "yaw": 0,
                    "pitch": 0,
                    "health": 20,
                    "food": 20,
                    "xp": 0,
                },
                "inventory": [],
                "blocks_near": [],
                "entities_near": [],
            }
        out = self._next_response
        self._next_response = None
        return out

    def shutdown(self) -> None:
        self.shutdown_called = True


def test_env_reset_returns_spawn_observation() -> None:
    fake = _FakeProcess()
    env = MinecraftEnvironment(process=fake)
    obs = env.reset(seed=42)
    assert fake.start_called is True
    assert obs["kind"] == "spawn"
    assert obs["position"] == [0.5, 64.0, 0.5]
    assert obs["health"] == 20
    assert obs["seed_hint"] == 42


def test_env_step_dispatches_action_and_returns_observation() -> None:
    fake = _FakeProcess()
    env = MinecraftEnvironment(process=fake)
    env.reset()
    fake.queue_response(
        {
            "type": "observation",
            "id": 1,
            "ok": True,
            "tick": 200,
            "bot": {
                "position": [10, 64, 10],
                "yaw": 0,
                "pitch": 0,
                "health": 20,
                "food": 20,
                "xp": 0,
            },
            "inventory": [{"slot": 0, "name": "oak_log", "count": 1}],
            "blocks_near": [{"pos": [10, 64, 10], "name": "dirt"}],
            "entities_near": [],
        }
    )
    obs, reward, done, info = env.step({"op": "dig", "target": [10, 64, 10]})
    assert fake.actions_sent[-1] == {"op": "dig", "target": [10, 64, 10]}
    assert obs["kind"] == "observation"
    assert obs["inventory"] == [{"slot": 0, "name": "oak_log", "count": 1}]
    assert reward == 0.0
    assert done is False
    assert info["ok"] is True
    assert info["tick"] == 200


def test_env_shutdown_calls_process_shutdown() -> None:
    fake = _FakeProcess()
    env = MinecraftEnvironment(process=fake)
    env.reset()
    env.shutdown()
    assert fake.shutdown_called is True


def test_env_step_before_reset_raises() -> None:
    env = MinecraftEnvironment()  # no injected process; reset() not called
    with pytest.raises(RuntimeError, match="before reset"):
        env.step({"op": "noop"})


def test_env_minerl_path_still_raises() -> None:
    """MineRL stub still raises so callers know it's not Phase 1."""
    env = MinecraftEnvironment(path="minerl")
    with pytest.raises(NotImplementedError, match="MineRL path"):
        env.reset()


def test_env_phase1_render_frame_returns_none() -> None:
    """Phase 1 has no video — render_frame must NOT raise."""
    fake = _FakeProcess()
    env = MinecraftEnvironment(process=fake)
    env.reset()
    assert env.render_frame() is None
    assert env.last_frame() is None


def test_env_metadata_callback_invoked_on_reset_and_step() -> None:
    seen: list[dict[str, Any]] = []

    def cb(observation: dict[str, Any]) -> None:
        seen.append(observation)

    fake = _FakeProcess()
    env = MinecraftEnvironment(process=fake, metadata_callback=cb)
    env.reset()
    env.step({"op": "noop"})
    # One callback for spawn, one for the step.
    assert len(seen) == 2
    assert seen[0]["kind"] == "spawn"
    assert seen[1]["kind"] == "observation"


def test_env_step_surfaces_subprocess_error_as_runtime_error() -> None:
    class _BoomProcess(_FakeProcess):
        def send_action(self, action: dict[str, Any]) -> dict[str, Any]:
            raise MineflayerProcessError("subprocess died mid-step")

    fake = _BoomProcess()
    env = MinecraftEnvironment(process=fake)
    env.reset()
    with pytest.raises(RuntimeError, match="mineflayer subprocess error"):
        env.step({"op": "noop"})
