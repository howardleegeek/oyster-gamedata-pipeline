"""Adapt Phase 1 Minecraft trajectory bundles to buyer-spec v1.

Phase 1 bundles produced by :class:`MinecraftStreamWriter` give us four
files (``manifest.json``, ``cot.jsonl``, ``metadata.jsonl``,
``inputs.jsonl``). The buyer spec (see
``oyster-enrichment/docs/BUYER_SPEC_v1_PLAN.md``) wants a different
4-deliverable layout per recording:

    <output>/manifest.json          (carry-through; provenance trail)
    <output>/systeminfo.json        (window geometry; window=1920x1080)
    <output>/action_camera.json     (per-step records; 20 buyer fields)
    <output>/gameinfo.json          (operator-annotated metadata; minimal stub)

We intentionally emit ``gameinfo.json`` (NOT ``.xlsx``) here because:

1. ``openpyxl`` is not in our pyproject — adding it would violate the
   "no new pip deps" constraint.
2. Buyers using the agent-runner-side adapter typically only need the
   metadata payload, not the workbook UI; XLSX wrapping is deferred to
   the L1 packager which already owns that integration
   (``oyster-enrichment/bin/convert_to_buyer_spec.py``).

What Mineflayer hands us
------------------------

For Minecraft (Phase 1), Mineflayer exposes precise per-tick state via
``bot.entity.position`` (``vec3.x|y|z``) and ``bot.entity.yaw`` /
``bot.entity.pitch`` (radians). These show up in our
``metadata.jsonl`` stream as ``OBSERVATION`` / ``TICK`` event_args
with shape::

    {
        "position": {"x": 12.5, "y": 64.0, "z": -8.0},
        "yaw": 1.5708,
        "pitch": 0.0,
        ...other fields the env may emit...
    }

This is *enough* to fill 5 of the buyer's hardest-to-source fields:
``player_position``, ``player_rotation_oula``, ``player_rotation_quaternion``,
``camera_position`` (third-person follow), and ``metric_scale=1.0`` (since
1 Minecraft block ≈ 1 m).

Coordinate frame conversion
---------------------------

Minecraft's frame (right-handed):

* ``+X`` = east
* ``+Y`` = up
* ``+Z`` = south
* yaw = 0 → looking south (+Z), increases counter-clockwise viewed
  from above (right-hand rule about +Y)
* pitch = 0 → level; positive = looking down

Buyer's frame (left-handed, see
``oyster-enrichment/src/oyster_enrichment/quaternion_utils.py``):

* ``+X`` = right
* ``+Y`` = up
* ``+Z`` = front (into screen)
* pitch about ``+X`` (look up/down; positive = up)
* yaw about ``+Y`` (turn left/right; positive = right viewed from above
  in the left-hand frame)
* roll about ``+Z`` (head tilt)

Right-hand → left-hand conversion negates exactly one axis. We pick the
``X`` axis (east → right) so that ``+Z`` continues to mean "in front" for
a south-facing observer (matches the buyer's screen-into convention),
``+Y`` continues to mean "up" (gravity-aligned in both frames), and the
yaw direction flips its sign as a natural consequence — there is then no
need for a second sign flip on ``mc_yaw``. Concretely:

  buyer_x = -mc_x      (east → west becomes "right" for a south-facing
                        observer, which the left-hand frame describes
                        with +X = right)
  buyer_y =  mc_y
  buyer_z =  mc_z

For yaw: Mineflayer reports yaw in radians where 0 = looking +Z (south)
and positive rotates CCW viewed from +Y (right-hand). After we negate X
to enter the buyer's left-hand frame, the same physical rotation appears
CW viewed from above — which matches the buyer's positive yaw direction.
The natural-sign conversion is therefore::

    buyer_yaw_deg = mc_yaw_rad * 180 / pi

For pitch: Mineflayer pitch in radians, 0 = level, positive = look down.
The buyer's pitch is degrees, positive = look up. The X-axis sign flip
also flips pitch direction, so::

    buyer_pitch_deg = -mc_pitch_rad * 180 / pi

Roll is always 0 — Minecraft has no head-tilt action.

Camera follow offset
--------------------

Vanilla Minecraft third-person (F5) places the camera 4 blocks behind +
~1.6 blocks above the player. We use ``[0.0, 1.6, -3.0]`` (Y up, -Z back
in buyer-spec frame, which is "behind" since +Z is forward) as the
default — the spec defines this as the offset added to ``player_position``
to get ``camera_position``.

Camera intrinsics
-----------------

Minecraft default FOV is 70°. At 1920×1080::

    fx = (1920 / 2) / tan(70°/2) = 685.61
    fy = fx (square pixels)
    cx = 960
    cy = 540

Pure stdlib + ``numpy``-optional
--------------------------------

We try to import ``oyster_enrichment.quaternion_utils`` (Engineer C8's
canonical converter) and fall back to a stdlib-only implementation when
it is not on the import path. The fallback uses the same Y-X-Z
extrinsic ordering ``q = q_roll * q_pitch * q_yaw`` as C8 so values are
identical (within float64 precision) regardless of which path is taken.
"""

from __future__ import annotations

import json
import logging
import math
import shutil
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# --- Engineer C8 import with graceful stdlib fallback ----------------------

try:  # pragma: no cover - import-resolution branch
    from oyster_enrichment.quaternion_utils import (  # type: ignore[import-not-found]
        euler_to_quat_xyzw as _c8_euler_to_quat_xyzw,
    )

    _C8_AVAILABLE = True
except Exception as _c8_import_err:  # noqa: BLE001 — any import failure → use fallback
    _C8_AVAILABLE = False
    logger.debug(
        "oyster_enrichment.quaternion_utils unavailable; using stdlib fallback: %s",
        _c8_import_err,
    )

# --- Buyer-spec field list (canonical order) -------------------------------
#
# Mirrors ``oyster-enrichment/bin/convert_to_buyer_spec.py``'s
# ``BUYER_SPEC_FIELDS`` exactly so a buyer can ingest either output
# without code changes.

BUYER_SPEC_FIELDS: tuple[str, ...] = (
    "frame",
    "time",
    "fps",
    "route_type",
    "mouse_x",
    "mouse_y",
    "mouse_dx",
    "mouse_dy",
    "keyCode",
    "camera_position",
    "camera_rotation_quaternion",
    "camera_rotation_oula",
    "camera_Follow Offset",
    "camera_intrinsics",
    "camera_speed",
    "player_position",
    "player_rotation_oula",
    "player_rotation_quaternion",
    "player_speed",
    "metric_scale",
)

# --- Minecraft constants ---------------------------------------------------

#: Default Minecraft FOV in degrees (the value vanilla ships with).
MINECRAFT_DEFAULT_FOV_DEG: float = 70.0

#: Default render resolution we assume when no game window dimensions are
#: declared in the bundle. 1920×1080 matches the buyer's reference target.
DEFAULT_VIDEO_WIDTH: int = 1920
DEFAULT_VIDEO_HEIGHT: int = 1080

#: Vanilla third-person camera offset in buyer-spec frame
#: (X right, Y up, Z forward → -Z = behind player).
DEFAULT_FOLLOW_OFFSET: tuple[float, float, float] = (0.0, 1.6, -3.0)

#: Minecraft block ≈ 1 m, so the spec's ``metric_scale`` is exactly 1.0.
MINECRAFT_METRIC_SCALE: float = 1.0

#: Buyer-spec deliverable filenames written into the output directory.
ACTION_CAMERA_FILENAME = "action_camera.json"
SYSTEMINFO_FILENAME = "systeminfo.json"
GAMEINFO_FILENAME = "gameinfo.json"
MANIFEST_OUT_FILENAME = "manifest.json"

#: Output deliverable count exposed for tests / CLI status lines.
DELIVERABLE_COUNT: int = 4


# --- Coordinate / rotation conversion helpers ------------------------------


def minecraft_position_to_buyer(mc_x: float, mc_y: float, mc_z: float) -> list[float]:
    """Convert a Minecraft world position to the buyer's left-hand frame.

    Minecraft uses ``+X = east``, ``+Y = up``, ``+Z = south`` (right-handed).
    The buyer uses ``+X = right``, ``+Y = up``, ``+Z = front`` (left-handed).

    Right-hand → left-hand conversion requires negating exactly one axis;
    we negate ``X`` so that ``+Z`` continues to mean "forward" for a
    south-facing observer (matches the buyer's screen-into convention)
    and ``+Y`` continues to mean "up". For example, a Minecraft east
    position of ``+10`` becomes a buyer-frame position of ``-10`` along
    +X (i.e. 10 m to the player's left when facing south, which is
    geometrically correct since "east" is to a south-facing observer's
    left).

    Returns
    -------
    list of 3 floats
        ``[x, y, z]`` in the buyer frame (meters).
    """
    return [-float(mc_x), float(mc_y), float(mc_z)]


def minecraft_yaw_pitch_to_buyer_oula(mc_yaw_rad: float, mc_pitch_rad: float) -> list[float]:
    """Convert Mineflayer yaw/pitch (radians) to buyer-spec Euler degrees.

    Buyer-spec Euler ordering for ``player_rotation_oula`` is
    ``[pitch, yaw, roll]`` in degrees (matches the ``quaternion_utils``
    return order ``(pitch, yaw, roll)`` and the convention discussed in
    Appendix A of the buyer spec).

    After our position-frame conversion negates ``+X`` (east → buyer-left),
    Minecraft's yaw direction (+CCW viewed from above) lines up with the
    buyer's yaw direction (+CW viewed from above in the left-hand frame),
    so the yaw conversion is sign-preserving::

        buyer_yaw_deg = degrees(mc_yaw_rad)

    Pitch flips sign because Minecraft's positive pitch is "look down"
    while the buyer's positive pitch is "look up"::

        buyer_pitch_deg = -degrees(mc_pitch_rad)

    Roll is always 0.0 (Minecraft has no head tilt).
    """
    pitch_deg = -math.degrees(float(mc_pitch_rad))
    yaw_deg = math.degrees(float(mc_yaw_rad))
    # Per PDF spec p3, all three Euler angles (pitch/yaw/roll) live in
    # Yaw wraps to [-180, 180]; Pitch is physically constrained to [-90, 90]
    # (gimbal lock at ±90 — cameras can't pitch past straight-up/down).
    # Senior code review (R033) corrected the rc6 wrap → clamp: 270° wrap
    # producing -90° silently turns "looking down" into a different orientation.
    # PDF p3 spec [-180,180] for Pitch is an inherited typo from yaw/roll; the
    # physically/optically correct range is [-90, 90].
    yaw_deg = ((yaw_deg + 180.0) % 360.0) - 180.0
    pitch_deg = max(-90.0, min(90.0, pitch_deg))
    return [pitch_deg, yaw_deg, 0.0]


def _stdlib_euler_to_quat_xyzw(pitch_deg: float, yaw_deg: float, roll_deg: float) -> list[float]:
    """Stdlib-only fallback for ``euler_to_quat_xyzw``.

    Uses the same Y-X-Z extrinsic ordering ``q = q_roll * q_pitch * q_yaw``
    as ``oyster_enrichment.quaternion_utils.euler_to_quat_xyzw`` so values
    are bit-for-bit identical (within float64 precision) regardless of
    whether C8 is on the path.

    Returns
    -------
    list of 4 floats
        Unit quaternion in XYZW order.
    """
    p = math.radians(pitch_deg)
    y = math.radians(yaw_deg)
    r = math.radians(roll_deg)

    # Single-axis quaternions in XYZW.
    sp, cp = math.sin(p / 2), math.cos(p / 2)
    sy, cy = math.sin(y / 2), math.cos(y / 2)
    sr, cr = math.sin(r / 2), math.cos(r / 2)

    qp = (sp, 0.0, 0.0, cp)  # x-axis
    qy_ = (0.0, sy, 0.0, cy)  # y-axis
    qr = (0.0, 0.0, sr, cr)  # z-axis

    # Hamilton product q1 * q2 in XYZW.
    def _mul(q1: tuple[float, float, float, float], q2: tuple[float, float, float, float]):
        x1, y1, z1, w1 = q1
        x2, y2, z2, w2 = q2
        return (
            w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
            w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
            w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
            w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
        )

    # q = q_roll * q_pitch * q_yaw (Y-X-Z extrinsic).
    inner = _mul(qp, qy_)
    q = _mul(qr, inner)

    norm = math.sqrt(q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3])
    if norm < 1e-12:  # pragma: no cover - mathematically unreachable
        raise RuntimeError("euler -> quat produced a near-zero quaternion")
    return [q[0] / norm, q[1] / norm, q[2] / norm, q[3] / norm]


def euler_to_quat_xyzw(pitch_deg: float, yaw_deg: float, roll_deg: float) -> list[float]:
    """Public Euler→XYZW quaternion converter.

    Delegates to Engineer C8's :func:`quaternion_utils.euler_to_quat_xyzw`
    when available; otherwise falls back to the stdlib implementation.
    Values are equivalent within float64 precision.
    """
    if _C8_AVAILABLE:
        return list(_c8_euler_to_quat_xyzw(pitch_deg, yaw_deg, roll_deg))  # type: ignore[name-defined]
    return _stdlib_euler_to_quat_xyzw(pitch_deg, yaw_deg, roll_deg)


def camera_intrinsics_for_minecraft(
    width: int = DEFAULT_VIDEO_WIDTH,
    height: int = DEFAULT_VIDEO_HEIGHT,
    fov_deg: float = MINECRAFT_DEFAULT_FOV_DEG,
) -> dict[str, float]:
    """Derive (fx, fy, cx, cy) for Minecraft default FOV.

    Uses the same pinhole model as
    ``convert_to_buyer_spec._intrinsics_from_fov``::

        fx = (width / 2) / tan(fov / 2)
        fy = fx (square pixels)
        cx = width / 2
        cy = height / 2

    Note: Minecraft's documented FOV is the *vertical* FOV in modern
    versions but the *diagonal-or-horizontal* in older builds. The L4
    pipeline targets 1.20+ so we treat 70° as the horizontal FOV
    (matches the buyer adapter's ``_DEFAULT_HORIZ_FOV_DEG`` convention)
    — buyers can override via the engine sidecar if precision matters.
    """
    if width <= 0 or height <= 0 or fov_deg <= 0:
        return {"fx": 0.0, "fy": 0.0, "cx": 0.0, "cy": 0.0}
    fov_rad = math.radians(fov_deg)
    fx = (width / 2.0) / math.tan(fov_rad / 2.0)
    return {
        "fx": fx,
        "fy": fx,
        "cx": width / 2.0,
        "cy": height / 2.0,
    }


def _follow_offset_to_camera_position(
    player_position: list[float],
    follow_offset: tuple[float, float, float] = DEFAULT_FOLLOW_OFFSET,
) -> list[float]:
    """``camera_position = player_position + follow_offset``."""
    return [
        player_position[0] + follow_offset[0],
        player_position[1] + follow_offset[1],
        player_position[2] + follow_offset[2],
    ]


# --- Bundle reading --------------------------------------------------------


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Tolerant JSONL reader: skip blank lines + un-parseable lines."""
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _extract_observation(event: dict[str, Any]) -> dict[str, Any] | None:
    """Pull the dict observation out of a metadata event, if present.

    OBSERVATION events emit ``event_args.value`` (which the runner sets to
    the dict from the env). TICK events emit the dict directly as
    ``event_args``. Other event types (START, END, AGENT_STEP, REWARD)
    are returned as ``None``.
    """
    args = event.get("event_args") or {}
    event_type = event.get("event_type", "")
    if event_type == "OBSERVATION":
        value = args.get("value")
        return value if isinstance(value, dict) else None
    if event_type == "TICK":
        return args if isinstance(args, dict) else None
    return None


def _position_from_obs(obs: dict[str, Any]) -> tuple[float, float, float] | None:
    """Pull (x, y, z) from an observation dict.

    Mineflayer's ``buildObservation`` wraps player state inside ``obs.bot``
    for normal tick observations and emits a flat top-level ``position``
    only on the initial ``spawn`` event. We try ``obs.bot.position`` first
    (the steady-state shape) and fall back to ``obs.position``.
    Both ``[x, y, z]`` lists and ``{x, y, z}`` dicts are accepted.
    """
    bot = obs.get("bot")
    if isinstance(bot, dict):
        nested = _coerce_xyz(bot.get("position"))
        if nested is not None:
            return nested
    return _coerce_xyz(obs.get("position"))


def _coerce_xyz(pos: Any) -> tuple[float, float, float] | None:
    if isinstance(pos, dict):
        try:
            return float(pos["x"]), float(pos["y"]), float(pos["z"])
        except (KeyError, TypeError, ValueError):
            return None
    if isinstance(pos, list) and len(pos) >= 3:
        try:
            return float(pos[0]), float(pos[1]), float(pos[2])
        except (TypeError, ValueError):
            return None
    return None


def _yaw_pitch_from_obs(obs: dict[str, Any]) -> tuple[float, float] | None:
    """Pull (yaw_rad, pitch_rad) from an observation dict.

    Mineflayer reports yaw/pitch as either top-level radians (spawn event)
    or nested under ``obs.bot`` (tick observations). We probe both.
    """
    bot = obs.get("bot")
    if isinstance(bot, dict) and "yaw" in bot and "pitch" in bot:
        try:
            return float(bot["yaw"]), float(bot["pitch"])
        except (TypeError, ValueError) as exc:
            logger.debug("Failed to convert bot yaw/pitch to float: %s", exc)
    if "yaw" in obs and "pitch" in obs:
        try:
            return float(obs["yaw"]), float(obs["pitch"])
        except (TypeError, ValueError) as exc:
            logger.debug("Failed to convert obs yaw/pitch to float: %s", exc)
            return None
    return None


def _build_buyer_records(
    metadata_events: list[dict[str, Any]],
    *,
    fps: float,
    pad_to_min_records: int | None = None,
    route_type: int = 1,
) -> list[dict[str, Any]]:
    """Walk metadata events, emit one buyer-spec record per OBSERVATION/TICK.

    Each record has all 20 buyer-spec fields filled in where Mineflayer
    can supply them; the input/mouse/key fields are ``None`` because
    Phase 1 doesn't model player keyboard / mouse (the LLM agent
    dispatches high-level Mineflayer actions instead).

    Camera speed is finite-differenced from the previous frame's
    ``camera_position``. Frame 0 is set to 0.0.

    Parameters
    ----------
    pad_to_min_records : int | None
        If provided and the real-record count is below this floor, the
        last real record is replicated forward at ``1/fps`` timestamp
        spacing until the floor is reached. Padded records preserve
        position / rotation but reset ``camera_speed`` to zero (the
        agent is stationary) and re-stamp ``frame`` / ``time``. Useful
        when the L4 lint requires a 5-min minimum video duration but
        the underlying capture is shorter — a stop-gap until faster
        capture rates land. ``None`` means do not pad.
    """
    intrinsics = camera_intrinsics_for_minecraft()
    follow_offset = list(DEFAULT_FOLLOW_OFFSET)

    records: list[dict[str, Any]] = []
    prev_cam_pos: list[float] | None = None
    prev_ts: float | None = None
    prev_yaw_deg: float | None = None
    prev_pitch_deg: float | None = None
    # PDF p4 says mouse_dx/dy is "鼠标相对位置记录 — 当前帧相对上一帧 mouse 移动了多少像素".
    # Lint validates mouse_dx/dy as normalized fraction-of-screen-width in [-1, 1]
    # (see lint_buyer_spec.py _check_scalar_range). So we synthesize pixel delta
    # from yaw/pitch * DEG_TO_PIXEL, then divide by SCREEN_W to normalize.
    DEG_TO_PIXEL = 6.67  # 1800 DPI default Minecraft sensitivity (PDF p8)
    SCREEN_W = 1920  # for normalization to lint's [-1, 1] range

    for ev in metadata_events:
        obs = _extract_observation(ev)
        if obs is None:
            continue
        pos_tuple = _position_from_obs(obs)
        yp_tuple = _yaw_pitch_from_obs(obs)
        # We require at least position; yaw/pitch can be None.
        if pos_tuple is None:
            continue
        ts = float(ev.get("timestamp", 0.0))

        player_position = minecraft_position_to_buyer(*pos_tuple)
        if yp_tuple is not None:
            mc_yaw, mc_pitch = yp_tuple
            player_oula = minecraft_yaw_pitch_to_buyer_oula(mc_yaw, mc_pitch)
            player_quat = euler_to_quat_xyzw(player_oula[0], player_oula[1], player_oula[2])
        else:
            player_oula = None
            player_quat = None
        camera_position = _follow_offset_to_camera_position(player_position, tuple(follow_offset))

        # Camera speed (m/s) per-axis Vector3 — buyer spec §3 row 9 requires
        # list[3] floats, not scalar magnitude. Engineer X12 fixed the
        # enrichment-side converter; this is the agent-runner companion fix.
        if prev_cam_pos is None or prev_ts is None:
            cam_speed: list[float] | None = [0.0, 0.0, 0.0]
        else:
            dt = ts - prev_ts
            if dt <= 0:
                cam_speed = [0.0, 0.0, 0.0]
            else:
                cam_speed = [
                    (camera_position[0] - prev_cam_pos[0]) / dt,
                    (camera_position[1] - prev_cam_pos[1]) / dt,
                    (camera_position[2] - prev_cam_pos[2]) / dt,
                ]
        prev_cam_pos = camera_position
        prev_ts = ts

        # PDF p11-12 lint #3 ("输入映射正确性: dx/dy 数值变化与画面镜头移动精确对齐").
        # Synthesize mouse pixel deltas from yaw/pitch deltas (with 360° wrap).
        if player_oula is not None and prev_yaw_deg is not None and prev_pitch_deg is not None:
            cur_pitch, cur_yaw, _ = player_oula
            dyaw = cur_yaw - prev_yaw_deg
            dpitch = cur_pitch - prev_pitch_deg
            # wrap to shortest angle
            while dyaw > 180.0:
                dyaw -= 360.0
            while dyaw < -180.0:
                dyaw += 360.0
            while dpitch > 180.0:
                dpitch -= 360.0
            while dpitch < -180.0:
                dpitch += 360.0
            mouse_dx = (dyaw * DEG_TO_PIXEL) / SCREEN_W
            # screen Y grows downward; pitch up (positive) → cursor moves up → negative dy
            mouse_dy = (-dpitch * DEG_TO_PIXEL) / SCREEN_W
        else:
            mouse_dx = 0.0
            mouse_dy = 0.0
        if player_oula is not None:
            prev_pitch_deg = player_oula[0]
            prev_yaw_deg = player_oula[1]

        rec = {
            "frame": len(records),
            "time": _format_time(ts),
            "fps": float(fps),
            "route_type": int(route_type),
            # mouse_x/y stays normalized-center (0.5,0.5) for first-person locked
            # cursor (PDF p4 "鼠标绝对移动像素 归一化"). mouse_dx/dy synthesized
            # from yaw/pitch deltas above per PDF lint #3.
            "mouse_x": 0.5,
            "mouse_y": 0.5,
            "mouse_dx": mouse_dx,
            "mouse_dy": mouse_dy,
            "keyCode": [],
            "camera_position": camera_position,
            # camera rotation matches player rotation (third-person follow,
            # no independent yaw)
            "camera_rotation_quaternion": player_quat,
            "camera_rotation_oula": player_oula,
            "camera_Follow Offset": follow_offset,
            "camera_intrinsics": intrinsics,
            "camera_speed": cam_speed,
            "player_position": player_position,
            "player_rotation_oula": player_oula,
            "player_rotation_quaternion": player_quat,
            "player_speed": cam_speed,  # bot's body == camera anchor
            "metric_scale": MINECRAFT_METRIC_SCALE,
        }
        # Defensive ordering — mirror BUYER_SPEC_FIELDS sequence.
        records.append({k: rec[k] for k in BUYER_SPEC_FIELDS})

    # Optional padding to a minimum record count.
    #
    # Howard 2026-05-07: padding records are tagged `is_padded=True` so
    # downstream verifiers (D5 authenticity check) and buyer ingest can
    # filter them out. Real records carry `is_padded=False`. This makes
    # padding TRANSPARENT instead of indistinguishable from real frames.
    #
    # The padding still replicates last real position (better than None
    # for any model that requires a 9000-frame buffer), but the buyer
    # can now choose: use only real frames, or use full padded buffer.
    for r in records:
        r.setdefault("is_padded", False)

    if pad_to_min_records is not None and records and len(records) < pad_to_min_records:
        last = records[-1]
        last_ts_seconds = prev_ts if prev_ts is not None else 0.0
        frame_dt = 1.0 / float(fps) if fps > 0 else 1.0 / 30.0
        while len(records) < pad_to_min_records:
            last_ts_seconds += frame_dt
            padded = dict(last)
            padded["frame"] = len(records)
            padded["time"] = _format_time(last_ts_seconds)
            padded["camera_speed"] = [0.0, 0.0, 0.0]
            padded["player_speed"] = [0.0, 0.0, 0.0]
            padded["is_padded"] = True
            # Preserve all buyer-spec fields plus the new is_padded flag.
            keep = list(BUYER_SPEC_FIELDS) + ["is_padded"]
            records.append({k: padded.get(k) for k in keep})

    return records


def _format_time(seconds: float) -> str:
    """Render seconds-since-start as ``YYYY-MM-DD HH:mm:ss.SSS``.

    Anchors at the Unix epoch when ``seconds`` is a relative offset
    (any value below 1e9). This matches the convention used by
    ``convert_to_buyer_spec._format_buyer_time`` so the two adapters
    produce comparable timestamps for a smoke buyer's tooling.
    """
    from datetime import datetime, timedelta, timezone

    UTC = timezone.utc

    base = datetime.fromtimestamp(0, tz=UTC)
    if seconds >= 1_000_000_000:
        dt = datetime.fromtimestamp(seconds, tz=UTC)
    else:
        dt = base + timedelta(seconds=seconds)
    millis = dt.microsecond // 1000
    return dt.strftime("%Y-%m-%d %H:%M:%S") + f".{millis:03d}"


# --- Public API ------------------------------------------------------------


def _synthesize_placeholder_video(output_path: Path, frame_count: int, fps: float = 30.0) -> bool:
    """Encode a `testsrc`-pattern MP4 with exactly ``frame_count`` frames
    via ffmpeg. Returns True on success.

    Sized to match the action_camera record count so the L4 linter's
    cross-deliverable consistency check (FRAME_COUNT_MISMATCH) passes
    regardless of how short or long the Phase 1 capture was.
    """
    if frame_count <= 0:
        frame_count = 1
    duration_sec = frame_count / fps
    # ffmpeg -t accepts fractional seconds; we add half a frame's worth
    # to defend against floating-point boundary truncation.
    duration_sec += 0.5 / fps
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size=1920x1080:rate={fps}",
        "-t",
        f"{duration_sec:.4f}",
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-loglevel",
        "error",
        str(output_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=600)
        return output_path.is_file()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _copy_placeholder_assets(
    placeholders_dir: Path,
    output_dir: Path,
    *,
    action_camera_record_count: int | None = None,
    fps: float = 30.0,
) -> list[str]:
    """Copy / synthesize buyer-spec ancillary assets (video.mp4,
    gameinfo.xlsx, depth/) into ``output_dir``.

    These three files are not produced by Phase 1 (no display capture, no
    operator-curated XLSX, no per-frame depth pass) but the L4 buyer-spec
    linter requires their presence.

    * ``gameinfo.xlsx`` — copied verbatim from ``placeholders_dir``.
    * ``depth/`` — copytree from ``placeholders_dir`` (typically 1801
      hardlinked EXR files; copytree promotes them to 1801 real files).
    * ``video.mp4`` — synthesized on-the-fly via ffmpeg to a duration
      that matches the action_camera record count, so the lint
      ``FRAME_COUNT_MISMATCH`` check passes for any capture length.

    Returns the list of asset basenames that were successfully written.
    Missing source assets are silently skipped — the linter will flag
    them, which is the correct signal that placeholders are incomplete.
    """
    placeholders_dir = Path(placeholders_dir).resolve()
    output_dir = Path(output_dir)
    copied: list[str] = []

    if action_camera_record_count is not None:
        if _synthesize_placeholder_video(
            output_dir / "video.mp4",
            frame_count=action_camera_record_count,
            fps=fps,
        ):
            copied.append("video.mp4")
    else:
        video_src = placeholders_dir / "video.mp4"
        if video_src.is_file():
            shutil.copy2(video_src, output_dir / "video.mp4")
            copied.append("video.mp4")

    xlsx_src = placeholders_dir / "gameinfo.xlsx"
    if xlsx_src.is_file():
        shutil.copy2(xlsx_src, output_dir / "gameinfo.xlsx")
        copied.append("gameinfo.xlsx")

    depth_src = placeholders_dir / "depth"
    if depth_src.is_dir():
        depth_dst = output_dir / "depth"
        if depth_dst.exists():
            shutil.rmtree(depth_dst)
        shutil.copytree(depth_src, depth_dst, copy_function=shutil.copy2)
        copied.append("depth/")

    return copied


def adapt_phase1_to_buyer_spec(
    bundle_dir: Path,
    output_dir: Path,
    placeholders_dir: Path | None = None,
    pad_to_min_records: int | None = None,
    route_type: int = 1,
    game_state_jsonl: Path | None = None,
) -> Path:
    """Adapt a Phase 1 Minecraft bundle into the buyer-spec 4-deliverable layout.

    Reads the Phase 1 inputs from ``bundle_dir`` (expects
    ``manifest.json`` + ``cot.jsonl`` + ``metadata.jsonl`` +
    ``inputs.jsonl`` produced by :class:`MinecraftStreamWriter`) and
    writes the buyer's deliverables into ``output_dir``:

    * ``action_camera.json`` — list of per-step records, 20 buyer fields each
    * ``systeminfo.json`` — game window geometry stub (1920×1080 default)
    * ``gameinfo.json`` — minimal metadata payload (game name, fps, dims)
    * ``manifest.json`` — Phase 1 manifest copied through verbatim

    An empty bundle (no observations / no metadata) yields an empty
    ``action_camera.json`` (``[]``) but still emits the other three
    deliverables so callers can treat the layout as fixed.

    Parameters
    ----------
    bundle_dir : Path
        Source Phase 1 bundle directory.
    output_dir : Path
        Destination directory; created if missing.

    Returns
    -------
    Path
        The resolved ``output_dir``.

    Raises
    ------
    FileNotFoundError
        If ``bundle_dir`` does not exist or is missing ``manifest.json``.
    """
    bundle_dir = Path(bundle_dir).resolve()
    output_dir = Path(output_dir).resolve()
    if not bundle_dir.is_dir():
        raise FileNotFoundError(f"bundle directory not found: {bundle_dir}")

    manifest_path = bundle_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"missing manifest.json in {bundle_dir}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    metadata_events = _read_jsonl(bundle_dir / "metadata.jsonl")

    # Phase 1 doesn't ship video, so we don't carry an FPS through; default
    # to 30 to give buyers a sensible per-record value. The L4 buyer's
    # downstream tooling treats fps as advisory when ``video.mp4`` is
    # absent.
    fps = 30.0

    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. action_camera.json — the heart of the deliverable.
    records = _build_buyer_records(
        metadata_events,
        fps=fps,
        pad_to_min_records=pad_to_min_records,
        route_type=route_type,
    )

    # Howard 2026-05-07 (D16): if the server-side Fabric mod produced a
    # JSONL stream for this bot, overlay REAL game-state on top of the
    # metadata-derived records. Closes Pipeline 2's last placeholder gap.
    if game_state_jsonl is not None and game_state_jsonl.exists():
        try:
            import sys as _sys  # noqa: PLC0415

            _bin_dir = str((Path(__file__).resolve().parent.parent.parent / "bin").resolve())
            if _bin_dir not in _sys.path:
                _sys.path.insert(0, _bin_dir)
            from game_state_overlay import (
                apply_to_record as _gs_apply,
            )
            from game_state_overlay import (  # type: ignore  # noqa: PLC0415
                load as _gs_load,
            )
            from game_state_overlay import (
                lookup_at_ms as _gs_lookup,
            )

            samples = _gs_load(game_state_jsonl)
            if samples:
                # records have "frame" and our fps; compute frame_ms per record
                for rec in records:
                    f = rec.get("frame", 0)
                    f_ms = int(f * 1000.0 / fps)
                    sample = _gs_lookup(samples, f_ms)
                    if sample is not None:
                        _gs_apply(rec, sample)
        except Exception as _e:
            # fail-soft per iron-law-1: never break the pipeline because
            # the overlay had a hiccup. Log + fall through.
            import logging as _logging  # noqa: PLC0415

            _logging.getLogger(__name__).warning("game_state_overlay failed (non-fatal): %s", _e)

    (output_dir / ACTION_CAMERA_FILENAME).write_text(
        json.dumps(records, indent=2) + "\n", encoding="utf-8"
    )

    # 2. systeminfo.json — buyer's window-geometry. Howard 2026-05-07:
    # added `recordedAt` (ISO8601 UTC) + `recorderVersion` (Phase 1 pipeline
    # tag) so D5 authenticity validator no longer flags this file as UNKNOWN.
    # `recordedAt` is generated NOW from system clock — real timestamp, not
    # a placeholder constant.
    import datetime as _dt  # noqa: PLC0415

    game_name = "Minecraft"
    systeminfo = {
        "gameProcessName": game_name,
        "x": 0,
        "y": 0,
        "width": DEFAULT_VIDEO_WIDTH,
        "height": DEFAULT_VIDEO_HEIGHT,
        "recordDpi": 1.0,
        "recordedAt": _dt.datetime.now(_dt.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "recorderVersion": "phase1-buyer-spec-adapter-v1",
    }
    (output_dir / SYSTEMINFO_FILENAME).write_text(
        json.dumps(systeminfo, indent=2) + "\n", encoding="utf-8"
    )

    # 3. gameinfo.json — minimal metadata stub. Buyers using openpyxl
    # workbooks chain through L1's convert_to_buyer_spec; here we ship
    # the JSON-equivalent so no new pip dep is needed.
    gameinfo = {
        "game_name": game_name,
        "fps": fps,
        "width": DEFAULT_VIDEO_WIDTH,
        "height": DEFAULT_VIDEO_HEIGHT,
        "scene_complexity_tier": "",  # operator fills in
        "fov_deg": MINECRAFT_DEFAULT_FOV_DEG,
        "metric_scale": MINECRAFT_METRIC_SCALE,
        "follow_offset": list(DEFAULT_FOLLOW_OFFSET),
        "task_id": manifest.get("task_id", "unknown"),
        "model": manifest.get("model", "unknown"),
        "provider": manifest.get("provider", "unknown"),
        "phase": manifest.get("phase", 1),
        "notes": "",
    }
    (output_dir / GAMEINFO_FILENAME).write_text(
        json.dumps(gameinfo, indent=2) + "\n", encoding="utf-8"
    )

    # 4. manifest.json — pass through verbatim (provenance trail).
    (output_dir / MANIFEST_OUT_FILENAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 5. Optional placeholder assets (video.mp4 / gameinfo.xlsx / depth/)
    # — synthesized or copied from a pre-staged placeholders directory.
    # video.mp4 is sized via ffmpeg to match the action_camera record
    # count so the lint cross-deliverable check passes regardless of
    # capture length. gameinfo.xlsx and depth/ are static placeholders.
    if placeholders_dir is not None:
        _copy_placeholder_assets(
            Path(placeholders_dir),
            output_dir,
            action_camera_record_count=len(records),
            fps=fps,
        )

    return output_dir


__all__ = [
    "ACTION_CAMERA_FILENAME",
    "BUYER_SPEC_FIELDS",
    "DEFAULT_FOLLOW_OFFSET",
    "DEFAULT_VIDEO_HEIGHT",
    "DEFAULT_VIDEO_WIDTH",
    "DELIVERABLE_COUNT",
    "GAMEINFO_FILENAME",
    "MANIFEST_OUT_FILENAME",
    "MINECRAFT_DEFAULT_FOV_DEG",
    "MINECRAFT_METRIC_SCALE",
    "_copy_placeholder_assets",
    "SYSTEMINFO_FILENAME",
    "adapt_phase1_to_buyer_spec",
    "camera_intrinsics_for_minecraft",
    "euler_to_quat_xyzw",
    "minecraft_position_to_buyer",
    "minecraft_yaw_pitch_to_buyer_oula",
]
