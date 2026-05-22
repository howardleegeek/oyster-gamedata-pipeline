from __future__ import annotations

import json

from oyster_agent_runner.game_plugins import (
    BUILTIN_PROFILES,
    GameDataSource,
    GamePluginProfile,
    get_profile,
    list_profiles,
    main,
    validate_all_profiles,
)


def test_builtin_profiles_validate() -> None:
    errors = validate_all_profiles()
    assert all(not items for items in errors.values())


def test_p0_profiles_include_minecraft_and_single_player_scaffolds() -> None:
    names = {profile.game_id for profile in list_profiles()}
    assert {
        "minecraft",
        "beamng",
        "factorio",
        "stardew_valley",
        "cyberpunk_2077",
        "cities_skylines",
    }.issubset(names)


def test_profiles_have_required_state_video_input_sources() -> None:
    for profile in BUILTIN_PROFILES.values():
        required_kinds = {source.kind for source in profile.data_sources if source.required}
        assert {"video", "input", "state"}.issubset(required_kinds), profile.game_id
        assert "manifest.json" in profile.output_streams
        assert profile.plug_and_play_ready is True


def test_get_profile_normalises_hyphenated_ids() -> None:
    assert get_profile("cyberpunk-2077").game_id == "cyberpunk_2077"


def test_profile_validation_rejects_unofficial_required_source() -> None:
    profile = GamePluginProfile(
        game_id="bad_game",
        display_name="Bad Game",
        support_tier="scaffold",
        anti_cheat_policy="official-only",
        process_names=("bad.exe",),
        modes=("single-player",),
        data_sources=(
            GameDataSource("video", "video", "OBS/game-window capture"),
            GameDataSource("input", "input", "RawInput JSONL"),
            GameDataSource(
                "memory-reader",
                "state",
                "process memory read",
                required=True,
                official_channel=False,
            ),
        ),
        output_streams=("recording.mp4", "game_state.jsonl", "inputs.jsonl", "manifest.json"),
        runbook="docs/BAD.md",
        adapter_module="src/bad.py",
        setup_minutes=1,
    )

    errors = profile.validate()
    assert any("official/blessed" in error for error in errors)
    assert profile.plug_and_play_ready is False


def test_cli_list_and_validate(capsys) -> None:
    assert main(["list"]) == 0
    list_out = capsys.readouterr().out
    assert "minecraft\tproduction\tready" in list_out

    assert main(["validate"]) == 0
    validate_out = capsys.readouterr().out
    assert "All built-in game plugin profiles are valid." in validate_out


def test_cli_show_outputs_json(capsys) -> None:
    assert main(["show", "minecraft"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["game_id"] == "minecraft"
    assert data["support_tier"] == "production"
