#!/usr/bin/env python3
"""
cs2_demo_to_engine_telemetry.py — CS2 .dem → engine-fields sidecar.

Parses a Counter-Strike 2 demo file with ``demoparser2`` and writes an
``engine_telemetry.json`` sidecar in the buyer-spec engine-fields shape
that ``bin/convert_to_buyer_spec.py --engine-fields-from`` consumes.

Mirrors the BeamNG path (``beamng_telemetry_capture.py``) — same output
contract, different upstream source. Where BeamNG is a *live* capture
over network, CS2 is a *post-hoc* parse of a saved replay, so this
script does no network I/O and runs deterministically against any
``.dem`` byte-for-byte.

Usage::

    python bin/cs2_demo_to_engine_telemetry.py \\
        --demo /path/to/match.dem \\
        --output engine_telemetry.json \\
        [--player-steam-id 76561198000000000] \\
        [--frame-rate 30] \\
        [--max-frames 9000]

Coordinate conversion (CS2 → buyer-spec)
----------------------------------------
CS2 uses the Source engine convention: right-handed, Z-up, units in
inches (1 unit = 0.0254 m).

* X = forward
* Y = left (positive)
* Z = up

Buyer-spec is right-handed, Y-up, metres:

* X = right
* Y = up
* Z = forward (into screen)

Conversion matrix::

    buyer_x = -cs2_y * 0.0254     (CS2 +Y "left" → buyer -X "left of right")
    buyer_y =  cs2_z * 0.0254     (CS2 +Z up → buyer +Y up)
    buyer_z =  cs2_x * 0.0254     (CS2 +X forward → buyer +Z forward)

CS2 view angles are in degrees:

* m_angEyeAngles[0] = pitch (positive = look down — flipped vs buyer)
* m_angEyeAngles[1] = yaw (degrees, 0 = looking +X)

Player speed is computed from the per-tick velocity vector
(``m_vecVelocity[0..2]`` in inches/s, magnitude in metres/s).

Output schema (matches BeamNG / Mineflayer adapters)
----------------------------------------------------
::

    {
      "frames": [
        {
          "frame": 0,
          "player_position": [x, y, z],
          "player_rotation_quaternion": [x, y, z, w],
          "player_rotation_oula": [pitch_deg, yaw_deg, roll_deg],
          "player_speed": [vx, vy, vz],
          "metric_scale": 1.0
        },
        ...
      ]
    }

``demoparser2`` is an *optional* runtime dep — the import is lazy so
the CLI loads (and ``--help`` works) on hosts that have not installed
it. When it is missing the script exits 2 with a one-line install
hint.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: 1 Source engine unit = 0.0254 m (1 inch).
_SOURCE_UNIT_METRES = 0.0254

#: CS2 default tick rate is 64Hz; we subsample to 30fps for the buyer
#: by default to align with video frame rate.
_DEFAULT_FRAME_RATE = 30

#: 5 minutes × 30fps = 9000; matches buyer's minimum video-duration
#: requirement at 30 fps.
_DEFAULT_MAX_FRAMES = 9000

#: Engine props we ask demoparser2 to extract per tick. These names map
#: to the CS2 schema; ``demoparser2.list_updated_fields`` exposes them
#: at runtime.
_WANTED_PROPS = (
    "X",
    "Y",
    "Z",
    "m_angEyeAngles",
    "m_vecVelocity",
)


# ---------------------------------------------------------------------------
# Coordinate conversion
# ---------------------------------------------------------------------------


def _cs2_pos_to_buyer(cs2_xyz_units: tuple[float, float, float]) -> list[float]:
    """CS2 (X=fwd, Y=left, Z=up; inches) → buyer (X=right, Y=up, Z=fwd; metres)."""
    x_units, y_units, z_units = cs2_xyz_units
    return [
        -y_units * _SOURCE_UNIT_METRES,
        z_units * _SOURCE_UNIT_METRES,
        x_units * _SOURCE_UNIT_METRES,
    ]


def _cs2_eye_angles_to_buyer_oula(pitch_deg: float, yaw_deg: float) -> list[float]:
    """CS2 view angles (deg) → buyer Euler (pitch, yaw, roll) in deg.

    CS2 pitch is positive=look down; buyer pitch is positive=look up,
    so we flip the sign. Yaw passes through unchanged (both
    counter-clockwise from +X). Roll is always 0 in CS2 first-person.
    """
    return [-float(pitch_deg), float(yaw_deg), 0.0]


def _euler_to_quat_xyzw(pitch_deg: float, yaw_deg: float, roll_deg: float) -> list[float]:
    """Y-X-Z extrinsic Euler → quaternion (x, y, z, w).

    Matches ``oyster_enrichment.quaternion_utils.euler_to_quat_xyzw``
    so multi-game adapters produce identical orientations.
    """
    half_p = math.radians(pitch_deg) * 0.5
    half_y = math.radians(yaw_deg) * 0.5
    half_r = math.radians(roll_deg) * 0.5
    cp, sp = math.cos(half_p), math.sin(half_p)
    cy, sy = math.cos(half_y), math.sin(half_y)
    cr, sr = math.cos(half_r), math.sin(half_r)
    # q = q_roll * q_pitch * q_yaw (Y-X-Z order)
    qx = sp * cy * cr + cp * sy * sr
    qy = cp * sy * cr - sp * cy * sr
    qz = cp * cy * sr - sp * sy * cr
    qw = cp * cy * cr + sp * sy * sr
    return [qx, qy, qz, qw]


def _cs2_velocity_to_buyer(
    cs2_vel_units_per_s: tuple[float, float, float],
) -> list[float]:
    """CS2 velocity (X_fwd, Y_left, Z_up; inches/s) → buyer velocity
    (X_right, Y_up, Z_fwd; m/s) Vector3.
    """
    vx, vy, vz = cs2_vel_units_per_s
    return [
        -vy * _SOURCE_UNIT_METRES,
        vz * _SOURCE_UNIT_METRES,
        vx * _SOURCE_UNIT_METRES,
    ]


# ---------------------------------------------------------------------------
# Demo parsing
# ---------------------------------------------------------------------------


def _import_demoparser2() -> Any:
    try:
        from demoparser2 import DemoParser  # type: ignore[import-not-found]

        return DemoParser
    except ImportError:
        sys.stderr.write("demoparser2 is required; install with: " "pip install demoparser2\n")
        sys.exit(2)


def _select_target_player(parser: Any, requested_steam_id: str | None) -> str | None:
    """Pick the SteamID of the player whose perspective we want."""
    info = parser.parse_player_info()
    if not len(info):
        return None
    if requested_steam_id is not None:
        # demoparser2 returns a polars DataFrame — filter for matching ID.
        try:
            match = info.filter(info["steamid"] == int(requested_steam_id))
            if len(match):
                return str(match["steamid"][0])
        except Exception as exc:
            logger.warning(
                "cs2_demo_parser: steam-id filter failed for requested_steam_id=%r; "
                "falling back to non-bot scan: %s: %s",
                requested_steam_id,
                type(exc).__name__,
                exc,
            )
    # Default: first non-bot player.
    try:
        humans = info.filter(~info["is_bot"])
        if len(humans):
            return str(humans["steamid"][0])
    except Exception as exc:
        logger.warning(
            "cs2_demo_parser: non-bot filter failed; falling back to first steamid "
            "(may be a bot if the demo's is_bot column is missing or all players are bots): "
            "%s: %s",
            type(exc).__name__,
            exc,
        )
    return str(info["steamid"][0])


def _build_frames_from_ticks(
    ticks_df: Any,
    *,
    frame_rate: int,
    max_frames: int,
    tick_interval: float,
) -> list[dict[str, Any]]:
    """Walk per-tick rows from ``parse_ticks`` and emit buyer-spec frames.

    Subsamples from the demo's tick rate (typically 64Hz) down to
    ``frame_rate`` (default 30) by picking every ``round(tick_rate / fps)``th
    row. Capped at ``max_frames`` (default 9000) to cover 5 minutes.
    """
    n_total = len(ticks_df)
    if n_total == 0:
        return []
    stride = max(1, int(round(1.0 / (frame_rate * tick_interval))))
    frames: list[dict[str, Any]] = []
    for i, row_idx in enumerate(range(0, n_total, stride)):
        if i >= max_frames:
            break
        row = ticks_df.row(row_idx, named=True)
        # Position
        try:
            cs2_pos = (float(row["X"]), float(row["Y"]), float(row["Z"]))
            buyer_pos = _cs2_pos_to_buyer(cs2_pos)
        except (KeyError, TypeError, ValueError):
            buyer_pos = None
        # Eye angles (m_angEyeAngles is a list[2] of pitch, yaw)
        try:
            ang = row["m_angEyeAngles"]
            pitch_deg = float(ang[0])
            yaw_deg = float(ang[1])
            buyer_oula = _cs2_eye_angles_to_buyer_oula(pitch_deg, yaw_deg)
            buyer_quat = _euler_to_quat_xyzw(*buyer_oula)
        except (KeyError, TypeError, ValueError, IndexError):
            buyer_oula = None
            buyer_quat = None
        # Velocity
        try:
            v = row["m_vecVelocity"]
            buyer_speed = _cs2_velocity_to_buyer((float(v[0]), float(v[1]), float(v[2])))
        except (KeyError, TypeError, ValueError, IndexError):
            buyer_speed = None
        frames.append(
            {
                "frame": i,
                "player_position": buyer_pos,
                "player_rotation_quaternion": buyer_quat,
                "player_rotation_oula": buyer_oula,
                "player_speed": buyer_speed,
                "camera_Follow Offset": None,
                "metric_scale": 1.0,
            }
        )
    return frames


def parse_demo_to_engine_telemetry(
    demo_path: Path,
    output_path: Path,
    *,
    player_steam_id: str | None = None,
    frame_rate: int = _DEFAULT_FRAME_RATE,
    max_frames: int = _DEFAULT_MAX_FRAMES,
    tick_interval: float = 1.0 / 64.0,
) -> int:
    """Parse a CS2 ``.dem`` and write engine_telemetry.json. Returns frame count."""
    demo_parser_cls = _import_demoparser2()
    parser = demo_parser_cls(str(demo_path))
    target = _select_target_player(parser, player_steam_id)
    players = [target] if target else None
    ticks_df = parser.parse_ticks(list(_WANTED_PROPS), players=players)
    frames = _build_frames_from_ticks(
        ticks_df,
        frame_rate=frame_rate,
        max_frames=max_frames,
        tick_interval=tick_interval,
    )
    output_path.write_text(json.dumps({"frames": frames}, indent=2) + "\n")
    return len(frames)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Parse a CS2 .dem demo into the buyer-spec engine-fields sidecar."
    )
    parser.add_argument("--demo", type=Path, required=True, help="Path to a CS2 .dem file.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("engine_telemetry.json"),
        help="Output JSON path (default: ./engine_telemetry.json).",
    )
    parser.add_argument(
        "--player-steam-id",
        type=str,
        default=None,
        help=(
            "SteamID64 of the player whose POV to extract. Defaults to the "
            "first non-bot player in the demo."
        ),
    )
    parser.add_argument(
        "--frame-rate",
        type=int,
        default=_DEFAULT_FRAME_RATE,
        help=f"Output frame rate, fps (default: {_DEFAULT_FRAME_RATE}).",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=_DEFAULT_MAX_FRAMES,
        help=f"Hard cap on frames emitted (default: {_DEFAULT_MAX_FRAMES} = 5min @ 30fps).",
    )
    parser.add_argument(
        "--tick-rate",
        type=int,
        default=64,
        help="CS2 server tick rate (default: 64; matchmaking uses 64).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)
    if not args.demo.is_file():
        sys.stderr.write(f"demo file not found: {args.demo}\n")
        return 2
    n = parse_demo_to_engine_telemetry(
        args.demo,
        args.output,
        player_steam_id=args.player_steam_id,
        frame_rate=args.frame_rate,
        max_frames=args.max_frames,
        tick_interval=1.0 / float(args.tick_rate),
    )
    print(f"wrote {args.output} ({n} frames)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
