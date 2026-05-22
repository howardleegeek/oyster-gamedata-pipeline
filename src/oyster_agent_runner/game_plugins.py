"""Plug-and-play game plugin profiles.

This module is the small contract layer between the production recorder and
per-game integrations. A profile does not start a game or talk to an SDK; it
declares how a game is allowed to provide state, input, video, and depth data.

The goal is boring on purpose: adding a game should mean adding one profile,
one adapter, and one runbook while keeping the validator/backend/release path
unchanged.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from typing import Literal

SupportTier = Literal["production", "scaffold", "planned", "degraded", "unsupported"]
AntiCheatPolicy = Literal["official-only", "single-player-only", "replay-only", "unsupported"]


@dataclass(frozen=True)
class GameDataSource:
    """One approved source of game-specific data."""

    name: str
    kind: Literal["video", "input", "state", "depth", "telemetry"]
    transport: str
    required: bool = True
    official_channel: bool = True
    notes: str = ""

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.name:
            errors.append("data source name is required")
        if not self.transport:
            errors.append(f"{self.name or '<unnamed>'}: transport is required")
        if self.required and not self.official_channel:
            errors.append(f"{self.name}: required source must use an official/blessed channel")
        return errors


@dataclass(frozen=True)
class GamePluginProfile:
    """Declarative contract for a game integration."""

    game_id: str
    display_name: str
    support_tier: SupportTier
    anti_cheat_policy: AntiCheatPolicy
    process_names: tuple[str, ...]
    modes: tuple[str, ...]
    data_sources: tuple[GameDataSource, ...]
    output_streams: tuple[str, ...]
    runbook: str
    adapter_module: str
    setup_minutes: int
    notes: str = ""
    metadata: dict[str, str] = field(default_factory=dict)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.game_id:
            errors.append("game_id is required")
        if not self.display_name:
            errors.append(f"{self.game_id or '<unknown>'}: display_name is required")
        if not self.process_names:
            errors.append(f"{self.game_id}: at least one process name is required")
        if not self.modes:
            errors.append(f"{self.game_id}: at least one mode is required")
        if self.anti_cheat_policy == "unsupported" and self.support_tier != "unsupported":
            errors.append(
                f"{self.game_id}: unsupported anti-cheat policy requires unsupported tier"
            )
        if self.setup_minutes < 0:
            errors.append(f"{self.game_id}: setup_minutes cannot be negative")

        kinds = {source.kind for source in self.data_sources if source.required}
        for required_kind in ("video", "input", "state"):
            if required_kind not in kinds:
                errors.append(f"{self.game_id}: missing required {required_kind} data source")

        if "manifest.json" not in self.output_streams:
            errors.append(f"{self.game_id}: output_streams must include manifest.json")

        for source in self.data_sources:
            errors.extend(f"{self.game_id}: {error}" for error in source.validate())
        return errors

    @property
    def plug_and_play_ready(self) -> bool:
        """True when the profile can be dispatched to adapter implementation."""

        return self.support_tier != "unsupported" and not self.validate()

    def to_dict(self) -> dict:
        return asdict(self)


COMMON_OUTPUT_STREAMS = (
    "recording.mp4",
    "game_state.jsonl",
    "inputs.jsonl",
    "manifest.json",
)


BUILTIN_PROFILES: dict[str, GamePluginProfile] = {
    "minecraft": GamePluginProfile(
        game_id="minecraft",
        display_name="Minecraft Java",
        support_tier="production",
        anti_cheat_policy="official-only",
        process_names=("java.exe", "javaw.exe", "java", "minecraft"),
        modes=("private-server", "single-player-lan"),
        data_sources=(
            GameDataSource("obs-video", "video", "OBS/game-window capture"),
            GameDataSource("raw-input", "input", "RawInput JSONL"),
            GameDataSource("mineflayer-state", "state", "Mineflayer JSON-line protocol"),
            GameDataSource(
                "depthanything",
                "depth",
                "RGB-to-depth inference",
                required=False,
                official_channel=True,
            ),
        ),
        output_streams=COMMON_OUTPUT_STREAMS,
        runbook="docs/PHASE1_RUNBOOK.md",
        adapter_module="src/oyster_agent_runner/environments/minecraft.py",
        setup_minutes=10,
        notes="Current reference implementation for the P0 plugin pattern.",
    ),
    "beamng": GamePluginProfile(
        game_id="beamng",
        display_name="BeamNG.drive",
        support_tier="scaffold",
        anti_cheat_policy="official-only",
        process_names=("BeamNG.drive.exe", "BeamNG.drive"),
        modes=("single-player", "scenario"),
        data_sources=(
            GameDataSource("obs-video", "video", "OBS/game-window capture"),
            GameDataSource("raw-input", "input", "RawInput JSONL"),
            GameDataSource("beamngpy-state", "state", "BeamNGpy official Python API"),
            GameDataSource("beamng-camera-depth", "depth", "BeamNG Camera sensor depth"),
        ),
        output_streams=COMMON_OUTPUT_STREAMS,
        runbook="docs/runbooks/BEAMNG_RUNBOOK.md",
        adapter_module="src/oyster_agent_runner/environments/beamng_drive.py",
        setup_minutes=20,
        notes="Best next vertical after Minecraft because native depth/pose are available.",
    ),
    "factorio": GamePluginProfile(
        game_id="factorio",
        display_name="Factorio",
        support_tier="scaffold",
        anti_cheat_policy="official-only",
        process_names=("factorio.exe", "factorio"),
        modes=("single-player", "private-headless-server"),
        data_sources=(
            GameDataSource("obs-video", "video", "OBS/game-window capture"),
            GameDataSource("raw-input", "input", "RawInput JSONL"),
            GameDataSource("rcon-mod-state", "state", "RCON + official Lua mod API"),
            GameDataSource(
                "flat-orthographic-depth",
                "depth",
                "synthetic flat-Z plane for 2D orthographic scene",
                required=False,
            ),
        ),
        output_streams=COMMON_OUTPUT_STREAMS,
        runbook="docs/SINGLE_PLAYER_GAMES.md#3-factorio--scaffolded",
        adapter_module="src/oyster_agent_runner/environments/factorio.py",
        setup_minutes=20,
    ),
    "stardew_valley": GamePluginProfile(
        game_id="stardew_valley",
        display_name="Stardew Valley",
        support_tier="scaffold",
        anti_cheat_policy="official-only",
        process_names=("Stardew Valley.exe", "Stardew Valley"),
        modes=("single-player",),
        data_sources=(
            GameDataSource("obs-video", "video", "OBS/game-window capture"),
            GameDataSource("raw-input", "input", "RawInput JSONL"),
            GameDataSource("smapi-relay-state", "state", "SMAPI HTTP relay"),
            GameDataSource(
                "flat-orthographic-depth",
                "depth",
                "synthetic flat-Z plane for 2D orthographic scene",
                required=False,
            ),
        ),
        output_streams=COMMON_OUTPUT_STREAMS,
        runbook="docs/runbooks/STARDEW_RUNBOOK.md",
        adapter_module="src/oyster_agent_runner/environments/stardew_valley.py",
        setup_minutes=15,
    ),
    "cyberpunk_2077": GamePluginProfile(
        game_id="cyberpunk_2077",
        display_name="Cyberpunk 2077",
        support_tier="scaffold",
        anti_cheat_policy="single-player-only",
        process_names=("Cyberpunk2077.exe",),
        modes=("single-player",),
        data_sources=(
            GameDataSource("obs-video", "video", "OBS/game-window capture"),
            GameDataSource("raw-input", "input", "RawInput JSONL"),
            GameDataSource("cet-lua-state", "state", "Cyber Engine Tweaks Lua websocket"),
            GameDataSource(
                "depthanything",
                "depth",
                "RGB-to-depth inference until REDmod depth path is validated",
                required=False,
            ),
        ),
        output_streams=COMMON_OUTPUT_STREAMS,
        runbook="docs/SINGLE_PLAYER_GAMES.md#5-cyberpunk-2077--new-single-player-only",
        adapter_module="src/oyster_agent_runner/environments/cyberpunk_2077.py",
        setup_minutes=30,
    ),
    "cities_skylines": GamePluginProfile(
        game_id="cities_skylines",
        display_name="Cities: Skylines",
        support_tier="scaffold",
        anti_cheat_policy="official-only",
        process_names=("Cities.exe", "Cities2.exe"),
        modes=("single-player", "editor"),
        data_sources=(
            GameDataSource("obs-video", "video", "OBS/game-window capture"),
            GameDataSource("raw-input", "input", "RawInput JSONL"),
            GameDataSource("mod-pipe-state", "state", "Mod API + named-pipe relay"),
            GameDataSource(
                "depthanything",
                "depth",
                "RGB-to-depth inference for aerial/city scenes",
                required=False,
            ),
        ),
        output_streams=COMMON_OUTPUT_STREAMS,
        runbook="docs/SINGLE_PLAYER_GAMES.md#6-cities-skylines-1-or-2--new",
        adapter_module="src/oyster_agent_runner/environments/cities_skylines.py",
        setup_minutes=20,
    ),
}


def list_profiles() -> list[GamePluginProfile]:
    """Return built-in profiles in deterministic P0 rollout order."""

    return [BUILTIN_PROFILES[name] for name in sorted(BUILTIN_PROFILES)]


def get_profile(game_id: str) -> GamePluginProfile:
    """Return a built-in game profile by id."""

    key = game_id.strip().lower().replace("-", "_")
    try:
        return BUILTIN_PROFILES[key]
    except KeyError as exc:
        available = ", ".join(sorted(BUILTIN_PROFILES))
        raise KeyError(f"Unknown game plugin {game_id!r}. Available: {available}") from exc


def validate_all_profiles() -> dict[str, list[str]]:
    """Return validation errors for every built-in profile."""

    return {profile.game_id: profile.validate() for profile in list_profiles()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect Oyster game plugin profiles.")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("list", help="List built-in game plugin profiles")
    show = sub.add_parser("show", help="Show one profile as JSON")
    show.add_argument("game_id")
    sub.add_parser("validate", help="Validate all built-in profiles")

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "list":
        for profile in list_profiles():
            ready = "ready" if profile.plug_and_play_ready else "blocked"
            print(f"{profile.game_id}\t{profile.support_tier}\t{ready}\t{profile.display_name}")
        return 0

    if args.command == "show":
        print(json.dumps(get_profile(args.game_id).to_dict(), indent=2, sort_keys=True))
        return 0

    if args.command == "validate":
        errors = validate_all_profiles()
        failed = {game_id: items for game_id, items in errors.items() if items}
        if failed:
            print(json.dumps(failed, indent=2, sort_keys=True))
            return 1
        print("All built-in game plugin profiles are valid.")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
