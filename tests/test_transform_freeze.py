from __future__ import annotations

import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BIN_DIR = REPO_ROOT / "bin"
sys.path.insert(0, str(BIN_DIR))

from transform_game_state_to_action_camera import (  # noqa: E402
    resample_to_video_grid,
    transform_tick_to_action_camera_row,
)


def _walking_circle_samples(count: int = 7681) -> list[dict[str, float | int | bool | str]]:
    """Synthetic 20 Hz game_state stream with continuous player motion."""
    base_ms = 1_779_977_223_420
    samples: list[dict[str, float | int | bool | str]] = []
    for i in range(count):
        theta = (2.0 * math.pi * i) / (count - 1)
        yaw = (360.0 * i) / (count - 1)
        samples.append(
            {
                "tick": i,
                "timestamp_ms": base_ms + i * 50,
                "x": 10.0 + 5.0 * math.cos(theta),
                "y": 64.0,
                "z": -3.0 + 5.0 * math.sin(theta),
                "yaw_deg": yaw,
                "pitch_deg": 12.0 * math.sin(theta),
                "look_x": math.sin(theta),
                "look_y": 0.0,
                "look_z": math.cos(theta),
                "velocity_x": -math.sin(theta) * 0.05,
                "velocity_y": 0.0,
                "velocity_z": math.cos(theta) * 0.05,
                "on_ground": True,
                "sneaking": False,
                "sprinting": True,
                "dimension": "minecraft:overworld",
                "game_mode": "SURVIVAL",
            }
        )
    return samples


def test_transform_resample_does_not_freeze_moving_game_state() -> None:
    ticks = _walking_circle_samples()

    resampled = resample_to_video_grid(ticks, target_count=10856)
    rows = [transform_tick_to_action_camera_row(tick, i) for i, tick in enumerate(resampled)]

    unique_positions = {
        tuple(round(float(v), 3) for v in row["player_position"])
        for row in rows
    }
    unique_yaws = {round(float(row["camera_rotation_oula"][1]), 3) for row in rows}

    assert len(resampled) == 10856
    assert len(unique_positions) > 100
    assert len(unique_yaws) > 100
