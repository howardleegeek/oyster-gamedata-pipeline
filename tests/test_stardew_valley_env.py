from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from oyster_agent_runner.environments.stardew_valley import (
    ACTION_KEYS,
    PlayerState,
    StardewValleyEnvironment,
)


class _FakeSMAPIRelay(ThreadingHTTPServer):
    state: dict[str, Any]
    calls: list[str]


class _FakeSMAPIHandler(BaseHTTPRequestHandler):
    server: _FakeSMAPIRelay

    def do_GET(self) -> None:
        if self.path != "/state":
            self.send_error(404)
            return

        self.server.calls.append("GET /state")
        self._write_json(self.server.state)

    def do_POST(self) -> None:
        if self.path != "/press":
            self.send_error(404)
            return

        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        payload = json.loads(body.decode("utf-8"))
        key = payload["key"]
        self.server.calls.append(f"POST /press:{key}")

        keys = dict.fromkeys(ACTION_KEYS, False)
        keys[key] = True
        self.server.state = {
            **self.server.state,
            "x": self.server.state["x"] + (1.0 if key == "right" else 0.0),
            "y": self.server.state["y"] - (1.0 if key == "up" else 0.0),
            "facing": key if key in {"up", "down", "left", "right"} else "down",
            "keys": keys,
            "timestamp": self.server.state["timestamp"] + 1.0,
        }
        self._write_json({"ok": True, "pressed": key})

    def log_message(self, format: str, *args: Any) -> None:
        return None

    def _write_json(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def fake_relay() -> _FakeSMAPIRelay:
    server = _FakeSMAPIRelay(("127.0.0.1", 0), _FakeSMAPIHandler)
    server.state = {
        "x": 12.5,
        "y": 44.0,
        "facing": "down",
        "map": "Farm",
        "keys": dict.fromkeys(ACTION_KEYS, False),
        "timestamp": 1779469000.0,
    }
    server.calls = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1.0)


def _new_env(server: _FakeSMAPIRelay) -> StardewValleyEnvironment:
    return StardewValleyEnvironment(
        host="127.0.0.1",
        port=server.server_address[1],
        timeout=0.5,
    )


def test_reset_reads_state_and_returns_protocol_observation(fake_relay: _FakeSMAPIRelay) -> None:
    env = _new_env(fake_relay)

    observation = env.reset(seed=123)

    assert fake_relay.calls == ["GET /state"]
    assert observation == {
        "timestamp": 1779469000.0,
        "map_name": "Farm",
        "player_position": {"x": 12.5, "y": 44.0},
        "facing": "down",
        "keys": dict.fromkeys(ACTION_KEYS, False),
        "source": "smapi_relay",
    }
    assert env.is_running is True


def test_step_presses_key_then_reads_state(fake_relay: _FakeSMAPIRelay) -> None:
    env = _new_env(fake_relay)
    env.reset()

    observation, reward, done, info = env.step({"key": "right"})

    assert fake_relay.calls == ["GET /state", "POST /press:right", "GET /state"]
    assert observation["player_position"] == {"x": 13.5, "y": 44.0}
    assert observation["facing"] == "right"
    assert observation["keys"]["right"] is True
    assert reward == 0.0
    assert done is False
    assert info["action"] == "right"
    assert info["frame_count"] == 1
    assert info["relay_response"] == {"ok": True, "pressed": "right"}


def test_step_noop_skips_press_but_refreshes_state(fake_relay: _FakeSMAPIRelay) -> None:
    env = _new_env(fake_relay)
    env.reset()

    observation, _, _, info = env.step({"action": "noop"})

    assert fake_relay.calls == ["GET /state", "GET /state"]
    assert observation["player_position"] == {"x": 12.5, "y": 44.0}
    assert info["action"] == "noop"
    assert info["relay_response"] == {"ok": True, "skipped": True}


def test_invalid_action_rejected_before_press(fake_relay: _FakeSMAPIRelay) -> None:
    env = _new_env(fake_relay)
    env.reset()

    with pytest.raises(ValueError, match="Unsupported Stardew action"):
        env.step({"key": "open_inventory"})

    assert fake_relay.calls == ["GET /state"]


def test_shutdown_calls_injected_relay_close() -> None:
    class _ClosableRelay:
        def __init__(self) -> None:
            self.closed = False

        def get_state(self) -> PlayerState:
            return PlayerState(map_name="Farm", timestamp=1.0)

        def press_key(self, key: str) -> dict[str, Any]:
            return {"ok": True, "pressed": key}

        def close(self) -> None:
            self.closed = True

    relay = _ClosableRelay()
    env = StardewValleyEnvironment(client=relay)
    env.reset()

    env.shutdown()

    assert relay.closed is True
    assert env.is_running is False


def test_reset_step_outputs_are_json_serializable(fake_relay: _FakeSMAPIRelay) -> None:
    env = _new_env(fake_relay)

    reset_observation = env.reset()
    step_observation, reward, done, info = env.step({"op": "use_tool"})

    json.dumps(reset_observation)
    json.dumps(
        {
            "observation": step_observation,
            "reward": reward,
            "done": done,
            "info": info,
        }
    )
