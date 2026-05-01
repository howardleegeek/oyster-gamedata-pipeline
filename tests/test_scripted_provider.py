"""Tests for ScriptedProvider — deterministic randomized Mineflayer driver."""

from __future__ import annotations

import json

from oyster_agent_runner.providers.scripted import ScriptedProvider, _extract_bot_xyz


def _build_user_message(observation: dict) -> dict:
    """Mirror the runner's `_format_observation` shape."""
    return {
        "role": "user",
        "content": f"[step 0] observation:\n{json.dumps(observation)}",
    }


def test_extract_bot_xyz_from_steady_state_observation() -> None:
    """Mineflayer's tick observations wrap position under ``bot.position``."""
    text = "[step 5] observation:\n" + json.dumps({"bot": {"position": [10.0, 64.0, -8.0]}})
    assert _extract_bot_xyz(text) == (10.0, 64.0, -8.0)


def test_extract_bot_xyz_from_spawn_observation() -> None:
    """Spawn events have flat top-level ``position``."""
    text = "[step 0] observation:\n" + json.dumps(
        {"kind": "spawn", "position": [191.5, 66.0, -76.5]}
    )
    assert _extract_bot_xyz(text) == (191.5, 66.0, -76.5)


def test_extract_bot_xyz_returns_none_for_no_position() -> None:
    text = "[step 0] observation:\n{}"
    assert _extract_bot_xyz(text) is None


def test_extract_bot_xyz_returns_none_for_malformed_json() -> None:
    text = "[step 0] observation:\nnot a json"
    assert _extract_bot_xyz(text) is None


def test_scripted_provider_is_deterministic_for_same_seed() -> None:
    """Same seed → byte-identical action sequence; the whole point."""
    obs = {"bot": {"position": [0.0, 64.0, 0.0]}}
    msgs = [_build_user_message(obs)]

    p1 = ScriptedProvider(seed=42)
    p2 = ScriptedProvider(seed=42)
    seq1 = [p1.chat("", msgs, 0.0) for _ in range(20)]
    seq2 = [p2.chat("", msgs, 0.0) for _ in range(20)]
    assert seq1 == seq2


def test_scripted_provider_diverges_for_different_seeds() -> None:
    """Different seeds → different sequences. Sanity check on the RNG."""
    obs = {"bot": {"position": [0.0, 64.0, 0.0]}}
    msgs = [_build_user_message(obs)]

    seq_a = [ScriptedProvider(seed=1).chat("", msgs, 0.0) for _ in range(1)]
    seq_b = [ScriptedProvider(seed=2).chat("", msgs, 0.0) for _ in range(1)]
    # Across many calls they may collide, but a single call from divergent
    # seeds should differ.
    assert seq_a != seq_b


def test_scripted_provider_emits_valid_action_tag() -> None:
    """Every reply must contain a parseable ``<action>{...}</action>`` block."""
    msgs = [_build_user_message({"bot": {"position": [0.0, 64.0, 0.0]}})]
    provider = ScriptedProvider(seed=0)
    for _ in range(10):
        reply = provider.chat("", msgs, 0.0)
        assert "<action>" in reply and "</action>" in reply
        body = reply.split("<action>")[1].split("</action>")[0]
        action = json.loads(body)
        assert "op" in action
        assert action["op"] in {"move_to", "look", "noop", "dig"}


def test_scripted_provider_move_to_target_within_radius() -> None:
    """``move_to`` targets must land within the configured radius of the
    bot's current position. Y stays the same — we don't fly.
    """
    pos = [10.0, 64.0, -8.0]
    msgs = [_build_user_message({"bot": {"position": pos}})]
    provider = ScriptedProvider(seed=0, move_radius=3.0)

    saw_move = False
    for _ in range(50):  # roll enough to hit a move_to (60% probability)
        reply = provider.chat("", msgs, 0.0)
        body = reply.split("<action>")[1].split("</action>")[0]
        action = json.loads(body)
        if action.get("op") == "move_to":
            saw_move = True
            tx, ty, tz = action["target"]
            assert abs(tx - pos[0]) <= 3.0
            assert ty == pos[1]
            assert abs(tz - pos[2]) <= 3.0
    assert saw_move, "expected at least one move_to in 50 rolls (60% probability)"


def test_scripted_provider_look_yaw_pitch_in_range() -> None:
    """``look`` yaw must be in [-pi, pi], pitch in [-0.5, 0.5]."""
    msgs = [_build_user_message({"bot": {"position": [0.0, 64.0, 0.0]}})]
    provider = ScriptedProvider(seed=0)

    looks_seen = 0
    for _ in range(100):
        reply = provider.chat("", msgs, 0.0)
        body = reply.split("<action>")[1].split("</action>")[0]
        action = json.loads(body)
        if action.get("op") == "look":
            looks_seen += 1
            assert -3.14159 <= action["yaw"] <= 3.14159
            assert -0.5 <= action["pitch"] <= 0.5
    assert looks_seen > 0, "expected some look actions in 100 rolls"


def test_scripted_provider_falls_back_to_noop_when_no_position() -> None:
    """If the observation has no usable position, ``move_to`` falls back
    to ``noop`` rather than emitting a malformed teleport target."""
    msgs = [{"role": "user", "content": "[step 0] observation:\n{}"}]
    provider = ScriptedProvider(seed=0)
    saw_noop = False
    saw_move = False
    for _ in range(30):
        reply = provider.chat("", msgs, 0.0)
        body = reply.split("<action>")[1].split("</action>")[0]
        action = json.loads(body)
        if action.get("op") == "noop":
            saw_noop = True
        if action.get("op") == "move_to":
            saw_move = True
    assert saw_noop and not saw_move


def test_scripted_provider_call_count_increments() -> None:
    msgs = [_build_user_message({"bot": {"position": [0.0, 64.0, 0.0]}})]
    provider = ScriptedProvider(seed=0)
    assert provider.call_count == 0
    provider.chat("", msgs, 0.0)
    assert provider.call_count == 1
    provider.chat("", msgs, 0.0)
    assert provider.call_count == 2


def test_scripted_provider_action_distribution_roughly_matches_design() -> None:
    """Over 500 rolls, the action mix should be near 60/25/10/5
    (move/look/noop/dig), within stochastic tolerance.
    """
    msgs = [_build_user_message({"bot": {"position": [0.0, 64.0, 0.0]}})]
    provider = ScriptedProvider(seed=0)
    counts = {"move_to": 0, "look": 0, "noop": 0, "dig": 0}
    for _ in range(500):
        reply = provider.chat("", msgs, 0.0)
        body = reply.split("<action>")[1].split("</action>")[0]
        op = json.loads(body)["op"]
        counts[op] = counts.get(op, 0) + 1
    # Wide tolerance — RNG variance is real
    assert 250 < counts["move_to"] < 350  # design ~300
    assert 100 < counts["look"] < 175  # design ~125
    assert 30 < counts["noop"] < 70  # design ~50
    assert 10 < counts["dig"] < 50  # design ~25
