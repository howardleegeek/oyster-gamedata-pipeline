"""Tests for environment adapters — vision extension, RCON parsing, gym stub behavior."""

from __future__ import annotations

import pytest

from oyster_agent_runner.environments.base import (
    MockEnvironment,
    VisionCapableEnvironment,
    has_vision,
)
from oyster_agent_runner.environments.factorio import (
    DEFAULT_RCON_PORT,
    FactorioEnvironment,
    RconConnection,
)
from oyster_agent_runner.environments.gym_env import GymEnvironment
from oyster_agent_runner.environments.minecraft import MinecraftEnvironment

# --- Vision extension --------------------------------------------------------


def test_mock_env_is_vision_capable() -> None:
    env = MockEnvironment(done_after_steps=3)
    assert has_vision(env)
    # runtime-checkable isinstance also works since MockEnvironment
    # implements `last_frame()` structurally.
    assert isinstance(env, VisionCapableEnvironment)


def test_mock_env_last_frame_starts_none_then_populated() -> None:
    env = MockEnvironment(done_after_steps=3)
    assert env.last_frame() is None
    env.reset()
    # After reset, still None until the first render.
    assert env.last_frame() is None
    frame = env.render_frame()
    assert frame is not None
    # last_frame returns the exact same bytes as the most recent render.
    assert env.last_frame() == frame
    # PNG header check.
    assert env.last_frame()[:8] == b"\x89PNG\r\n\x1a\n"


def test_mock_env_reset_clears_last_frame() -> None:
    env = MockEnvironment(done_after_steps=3)
    env.reset()
    env.render_frame()
    assert env.last_frame() is not None
    env.reset()
    assert env.last_frame() is None


def test_has_vision_is_false_for_non_implementing_env() -> None:
    class PlainEnv:
        def reset(self, seed=None):
            return {}

        def step(self, action):
            return {}, 0.0, False, {}

        def render_frame(self):
            return None

        def shutdown(self):
            pass

    assert not has_vision(PlainEnv())


# --- Factorio RCON parsing ---------------------------------------------------


def test_rcon_connection_parse_full_uri() -> None:
    conn = RconConnection.parse("rcon://:secret@factorio.example:25575")
    assert conn.host == "factorio.example"
    assert conn.port == 25575
    assert conn.password == "secret"


def test_rcon_connection_parse_host_only() -> None:
    conn = RconConnection.parse("rcon://localhost")
    assert conn.host == "localhost"
    assert conn.port == DEFAULT_RCON_PORT
    assert conn.password == ""


def test_rcon_connection_parse_host_and_port() -> None:
    conn = RconConnection.parse("rcon://host.example.com:25000")
    assert conn.host == "host.example.com"
    assert conn.port == 25000
    assert conn.password == ""


@pytest.mark.parametrize(
    "bad_uri",
    [
        "http://localhost",  # wrong scheme
        "rcon://",  # no host
        "localhost",  # no scheme at all
        "",
        "rcon://host:not-a-port",
    ],
)
def test_rcon_connection_parse_rejects_malformed(bad_uri: str) -> None:
    with pytest.raises(ValueError, match="Invalid RCON URI"):
        RconConnection.parse(bad_uri)


def test_factorio_env_accepts_rcon_uri_kwarg() -> None:
    env = FactorioEnvironment(rcon_uri="rcon://:pw@host:30000")
    assert env.host == "host"
    assert env.rcon_port == 30000
    assert env.password == "pw"
    assert env.connection == RconConnection(host="host", port=30000, password="pw")


def test_factorio_env_discrete_params_still_work() -> None:
    """Backcompat — existing callsites using host/port/password keep working."""
    env = FactorioEnvironment(host="h", rcon_port=9999, password="p")
    assert env.connection == RconConnection(host="h", port=9999, password="p")


def test_factorio_env_step_still_raises_not_implemented() -> None:
    env = FactorioEnvironment(rcon_uri="rcon://localhost")
    with pytest.raises(NotImplementedError, match="scaffold stub"):
        env.step({"op": "noop"})


# --- Minecraft path selection ------------------------------------------------


def test_minecraft_env_defaults_to_mineflayer_path() -> None:
    env = MinecraftEnvironment()
    assert env.path == "mineflayer"


def test_minecraft_env_accepts_minerl_path() -> None:
    env = MinecraftEnvironment(path="minerl", minerl_env_id="MineRLObtainDiamond-v0")
    assert env.path == "minerl"
    assert env.minerl_env_id == "MineRLObtainDiamond-v0"


# --- Gym conditional wrapper -------------------------------------------------


def test_gym_env_stub_mode_when_gymnasium_missing() -> None:
    """If gymnasium isn't installed, is_stub is True and reset raises."""
    env = GymEnvironment("MountainCar-v0")
    if env.is_stub:
        with pytest.raises(NotImplementedError, match="scaffold stub"):
            env.reset()
    else:
        pytest.skip("gymnasium is installed — stub-mode test does not apply")


def test_gym_env_real_mode_roundtrip() -> None:
    """If gymnasium IS installed, verify the real wrapper works end-to-end."""
    gymnasium = pytest.importorskip("gymnasium")
    # Use a classic-control env with pure-python deps so this works on bare
    # CI (no Box2D / Atari ROMs).
    try:
        gymnasium.make("CartPole-v1")
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"CartPole-v1 unavailable: {exc}")

    env = GymEnvironment("CartPole-v1", render_mode=None)
    assert not env.is_stub
    obs = env.reset(seed=42)
    assert isinstance(obs, dict)
    # CartPole action space is Discrete(2) → we wrap via {"value": int}.
    next_obs, reward, done, info = env.step({"value": 0})
    assert isinstance(next_obs, dict)
    assert isinstance(reward, float)
    assert isinstance(done, bool)
    env.shutdown()
