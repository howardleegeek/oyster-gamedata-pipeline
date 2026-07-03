"""Game-state overlay — consume the Fabric mod's JSONL and patch real
position/rotation/velocity into action_camera records.

Howard 2026-05-07: this is the consumer half of the contract with
mc-mod/. The mod streams per-tick game state to
``~/Documents/OysterClips/active_session/game_state.jsonl``; this module
loads that file (if present) and produces a function that overrides the
placeholder camera/player fields in each ``action_camera.json`` record.

If the JSONL is missing (mod not installed): ``load`` returns ``None`` and
the recorder uses its existing placeholder paths. The README in the
tarball gets a tag indicating whether real game state was used.

Schema reference (mod side: GameStateSample.toJsonLine):
    {tick, timestamp_ms, x, y, z, yaw_deg, pitch_deg,
     look_x, look_y, look_z, velocity_x, velocity_y, velocity_z,
     on_ground, sneaking, sprinting, dimension, game_mode}
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def jsonl_path() -> Path:
    """The canonical path the mod writes to. Must match SessionDir.java."""
    home = Path.home()
    return home / "Documents" / "OysterClips" / "active_session" / "game_state.jsonl"


def load(jsonl: Path | None = None) -> list[dict[str, Any]] | None:
    """Load the JSONL into a sorted (by timestamp_ms) list of sample dicts.

    Returns ``None`` if the file doesn't exist, is empty, or fails to parse.
    Never raises — callers fall back to placeholder cleanly.
    """
    if jsonl is None:
        jsonl = jsonl_path()
    try:
        if not jsonl.exists() or jsonl.stat().st_size == 0:
            return None
        samples: list[dict[str, Any]] = []
        with jsonl.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    samples.append(json.loads(line))
                except json.JSONDecodeError:
                    # tolerate trailing partial line if MC crashed mid-write
                    continue
        if not samples:
            return None
        samples.sort(key=lambda s: s.get("timestamp_ms", 0))
        return samples
    except Exception:
        logger.warning("Failed to load game_state.jsonl", exc_info=True)
        return None


def _euler_to_quaternion(yaw_deg: float, pitch_deg: float) -> list[float]:
    """Convert MC yaw/pitch (degrees) to a (x, y, z, w) quaternion.

    MC convention: yaw 0° = -Z (south), positive = CCW from above. Pitch
    +90° looks straight down. We follow the standard XYZ ordering.
    """
    yaw_rad = math.radians(yaw_deg)
    pitch_rad = math.radians(pitch_deg)
    cy = math.cos(yaw_rad * 0.5)
    sy = math.sin(yaw_rad * 0.5)
    cp = math.cos(pitch_rad * 0.5)
    sp = math.sin(pitch_rad * 0.5)
    # roll = 0 — Minecraft camera doesn't roll
    qx = sp * cy
    qy = sy * cp
    qz = -sp * sy
    qw = cy * cp
    return [qx, qy, qz, qw]


def lookup_at_ms(samples: list[dict[str, Any]], frame_ms: int) -> dict[str, Any] | None:
    """Return the sample whose ``timestamp_ms`` is closest to ``frame_ms``.

    Linear scan — for 1801 frames × thousands of samples it's still <200 ms
    on real data; not worth bisecting. Returns ``None`` if list empty.
    """
    if not samples:
        return None
    if len(samples) == 1:
        return samples[0]
    # Anchor first sample's timestamp_ms to t=0 for the recording so the
    # mod's wall-clock timestamps line up with frame_ms (which is recording-
    # relative ms-since-record-start).
    base = samples[0]["timestamp_ms"]
    target = base + frame_ms
    # Linear scan; samples are sorted ascending.
    best = samples[0]
    best_dist = abs(best["timestamp_ms"] - target)
    for s in samples[1:]:
        d = abs(s["timestamp_ms"] - target)
        if d < best_dist:
            best, best_dist = s, d
        else:
            # since sorted ascending, once distance starts growing we can
            # stop scanning further
            break
    return best


def apply_to_record(record: dict[str, Any], sample: dict[str, Any]) -> None:
    """Overwrite the placeholder camera/player fields on ``record`` in
    place, using real values from ``sample``.

    Caller must ensure ``record`` is a fresh dict that's about to be
    appended to ``action_records``. Field set must match
    ``buyer_spec_adapter._build_buyer_records`` to avoid lint failures.
    """
    px = sample["x"]
    py = sample["y"]
    pz = sample["z"]
    yaw = sample["yaw_deg"]
    pitch = sample["pitch_deg"]
    vx = sample["velocity_x"]
    vy = sample["velocity_y"]
    vz = sample["velocity_z"]

    quat = _euler_to_quaternion(yaw, pitch)

    # MC third-person follow camera offset: 4 blocks behind, 1.6 above.
    # Computed per-frame as player_position + offset_in_world_frame.
    # Recorder's existing offset convention is (x_offset, y_offset, z_offset)
    # in player-local frame; for MC we hard-code follow=(0, 1.6, -3).
    follow_offset = [0.0, 1.6, -3.0]
    cx = px + follow_offset[0]
    cy = py + follow_offset[1]
    cz = pz + follow_offset[2]

    # Override placeholders
    record["camera_position"] = [cx, cy, cz]
    record["camera_rotation_oula"] = [pitch, yaw, 0.0]  # roll=0 for MC
    record["camera_rotation_quaternion"] = quat
    record["camera_Follow Offset"] = follow_offset
    record["camera_speed"] = [vx, vy, vz]
    record["player_position"] = [px, py, pz]
    record["player_rotation_oula"] = [pitch, yaw, 0.0]
    record["player_rotation_quaternion"] = quat
    record["player_speed"] = [vx, vy, vz]
    # Tag sample so D5 can confirm real game-state vs placeholder
    record["_real_game_state"] = True
    record["_dimension"] = sample.get("dimension", "")
    record["_on_ground"] = bool(sample.get("on_ground", True))
