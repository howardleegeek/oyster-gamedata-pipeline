from __future__ import annotations

import json
from typing import Any

import pytest

from oyster_agent_runner.environments.factorio import (
    OBSERVATION_SOURCE,
    OBSERVE_COMMAND,
    FactorioAction,
    FactorioEnvironment,
    FactorioObservation,
    FactorioProtocolError,
    InvalidFactorioAction,
)


def _observation_payload(tick: int = 42) -> str:
    return json.dumps(
        {
            "tick": tick,
            "player_position": {"x": 10.5, "y": -3.25},
            "surface": "nauvis",
            "inventory": {"iron-plate": 12, "burner-mining-drill": 1},
            "entities_near": [
                {"name": "stone-furnace", "position": {"x": 11, "y": -3}},
                {"name": "iron-ore", "amount": 1000},
            ],
            "source": OBSERVATION_SOURCE,
        }
    )


class FakeRconClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.commands: list[str] = []
        self.closed = False

    def send_command(self, command: str) -> str:
        self.commands.append(command)
        if not self.responses:
            raise AssertionError(f"Unexpected command: {command}")
        return self.responses.pop(0)

    def close(self) -> None:
        self.closed = True


class LegacyFakeRconClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.commands: list[str] = []

    def command(self, command: str) -> str:
        self.commands.append(command)
        return self.response


def test_factorio_observation_schema_normalizes_mod_payload() -> None:
    obs = FactorioObservation.from_payload(_observation_payload()).to_observation()

    assert obs == {
        "tick": 42,
        "player_position": {"x": 10.5, "y": -3.25},
        "surface": "nauvis",
        "inventory": {"iron-plate": 12, "burner-mining-drill": 1},
        "entities_near": [
            {"name": "stone-furnace", "position": {"x": 11.0, "y": -3.0}},
            {"name": "iron-ore", "amount": 1000},
        ],
        "source": OBSERVATION_SOURCE,
    }


def test_factorio_observation_rejects_missing_contract_fields() -> None:
    with pytest.raises(FactorioProtocolError, match="missing required fields"):
        FactorioObservation.from_payload({"tick": 1, "surface": "nauvis"})


def test_reset_uses_fake_rcon_client_and_returns_observation() -> None:
    client = FakeRconClient([_observation_payload()])
    env = FactorioEnvironment(rcon_client=client)

    obs = env.reset(seed=123)

    assert client.commands == [OBSERVE_COMMAND]
    assert obs["tick"] == 42
    assert obs["source"] == OBSERVATION_SOURCE


def test_step_validates_action_sends_mod_payload_and_observes_post_state() -> None:
    client = FakeRconClient(
        [_observation_payload(tick=42), json.dumps({"ok": True}), _observation_payload(tick=43)]
    )
    env = FactorioEnvironment(rcon_client=client)
    env.reset()
    client.commands.clear()

    obs, reward, done, info = env.step({"op": "craft", "recipe": "iron-gear-wheel", "count": 2})

    assert len(client.commands) == 2
    assert 'remote.call("oyster_recorder", "act"' in client.commands[0]
    assert '\\"op\\":\\"craft\\"' in client.commands[0]
    assert '\\"recipe\\":\\"iron-gear-wheel\\"' in client.commands[0]
    assert client.commands[1] == OBSERVE_COMMAND
    assert obs["tick"] == 43
    assert reward == 0.0
    assert done is False
    assert info["action"] == {"op": "craft", "recipe": "iron-gear-wheel", "count": 2}
    assert info["action_response"] == {"ok": True}


def test_step_requires_reset_even_with_fake_client() -> None:
    env = FactorioEnvironment(rcon_client=FakeRconClient([]))

    with pytest.raises(RuntimeError, match="step called before reset"):
        env.step({"op": "noop"})


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"op": "noop"}, {"op": "noop"}),
        (
            {"op": "move", "direction": "north", "ticks": 60},
            {"op": "move", "direction": "north", "ticks": 60},
        ),
        (
            {"op": "craft", "recipe": "iron-plate", "count": 3},
            {"op": "craft", "recipe": "iron-plate", "count": 3},
        ),
        (
            {"op": "place", "entity": "stone-furnace", "x": 1, "y": 2.5},
            {"op": "place", "entity": "stone-furnace", "x": 1.0, "y": 2.5},
        ),
        ({"op": "mine", "entity_id": 99}, {"op": "mine", "entity_id": 99}),
        ({"op": "mine", "x": 4, "y": 5}, {"op": "mine", "x": 4.0, "y": 5.0}),
    ],
)
def test_factorio_action_validation_accepts_contract_ops(
    raw: dict[str, Any], expected: dict[str, Any]
) -> None:
    assert FactorioAction.validate(raw).to_mod_payload() == expected


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"op": "teleport"},
        {"op": "move", "direction": "northwest"},
        {"op": "move", "direction": "north", "ticks": "bad"},
        {"op": "move", "direction": "north", "ticks": 0},
        {"op": "craft", "recipe": "", "count": 1},
        {"op": "craft", "recipe": "iron-plate", "count": "bad"},
        {"op": "craft", "recipe": "iron-plate", "count": 0},
        {"op": "place", "entity": "stone-furnace", "x": 1},
        {"op": "mine"},
        {"op": "mine", "entity_id": "bad"},
        {"op": "mine", "entity_id": 0},
    ],
)
def test_factorio_action_validation_rejects_bad_actions(raw: dict[str, Any]) -> None:
    with pytest.raises(InvalidFactorioAction):
        FactorioAction.validate(raw)


def test_legacy_fake_client_with_command_method_is_supported() -> None:
    client = LegacyFakeRconClient(_observation_payload())
    env = FactorioEnvironment(rcon_client=client)

    obs = env.reset()

    assert obs["tick"] == 42
    assert client.commands == [OBSERVE_COMMAND]


def test_real_rcon_path_fails_clearly_when_factorio_is_not_running() -> None:
    env = FactorioEnvironment(host="127.0.0.1", rcon_port=1, timeout=0.05)

    with pytest.raises(ConnectionError, match="Factorio RCON connection failed"):
        env.reset()
