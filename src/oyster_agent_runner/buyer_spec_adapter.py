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
import math
from pathlib import Path
from typing import Any

# --- Engineer C8 import with graceful stdlib fallback ----------------------

try:  # pragma: no cover - import-resolution branch
    from oyster_enrichment.quaternion_utils import (  # type: ignore[import-not-found]
        euler_to_quat_xyzw as _c8_euler_to_quat_xyzw,
    )

    _C8_AVAILABLE = True
except Exception:  # noqa: BLE001 — any import failure → use fallback
    _C8_AVAILABLE = False

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

    Mineflayer-shaped observations have ``position: {x, y, z}``. We also
    accept a flat ``[x, y, z]`` list for robustness.
    """
    pos = obs.get("position")
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

    Mineflayer reports yaw / pitch as separate top-level radians fields.
    """
    if "yaw" not in obs or "pitch" not in obs:
        return None
    try:
        return float(obs["yaw"]), float(obs["pitch"])
    except (TypeError, ValueError):
        return None


def _build_buyer_records(
    metadata_events: list[dict[str, Any]],
    *,
    fps: float,
) -> list[dict[str, Any]]:
    """Walk metadata events, emit one buyer-spec record per OBSERVATION/TICK.

    Each record has all 20 buyer-spec fields filled in where Mineflayer
    can supply them; the input/mouse/key fields are ``None`` because
    Phase 1 doesn't model player keyboard / mouse (the LLM agent
    dispatches high-level Mineflayer actions instead).

    Camera speed is finite-differenced from the previous frame's
    ``camera_position``. Frame 0 is set to 0.0.
    """
    intrinsics = camera_intrinsics_for_minecraft()
    follow_offset = list(DEFAULT_FOLLOW_OFFSET)

    records: list[dict[str, Any]] = []
    prev_cam_pos: list[float] | None = None
    prev_ts: float | None = None

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

        # Camera speed (m/s) finite-differenced.
        if prev_cam_pos is None or prev_ts is None:
            cam_speed: float | None = 0.0
        else:
            dt = ts - prev_ts
            if dt <= 0:
                cam_speed = 0.0
            else:
                dx = camera_position[0] - prev_cam_pos[0]
                dy = camera_position[1] - prev_cam_pos[1]
                dz = camera_position[2] - prev_cam_pos[2]
                cam_speed = math.sqrt(dx * dx + dy * dy + dz * dz) / dt
        prev_cam_pos = camera_position
        prev_ts = ts

        rec = {
            "frame": len(records),
            "time": _format_time(ts),
            "fps": float(fps),
            "route_type": 1,
            "mouse_x": None,
            "mouse_y": None,
            "mouse_dx": None,
            "mouse_dy": None,
            "keyCode": None,
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

    return records


def _format_time(seconds: float) -> str:
    """Render seconds-since-start as ``YYYY-MM-DD HH:mm:ss.SSS``.

    Anchors at the Unix epoch when ``seconds`` is a relative offset
    (any value below 1e9). This matches the convention used by
    ``convert_to_buyer_spec._format_buyer_time`` so the two adapters
    produce comparable timestamps for a smoke buyer's tooling.
    """
    from datetime import UTC, datetime, timedelta

    base = datetime.fromtimestamp(0, tz=UTC)
    if seconds >= 1_000_000_000:
        dt = datetime.fromtimestamp(seconds, tz=UTC)
    else:
        dt = base + timedelta(seconds=seconds)
    millis = dt.microsecond // 1000
    return dt.strftime("%Y-%m-%d %H:%M:%S") + f".{millis:03d}"


# --- Public API ------------------------------------------------------------


def adapt_phase1_to_buyer_spec(bundle_dir: Path, output_dir: Path) -> Path:
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
    records = _build_buyer_records(metadata_events, fps=fps)
    (output_dir / ACTION_CAMERA_FILENAME).write_text(
        json.dumps(records, indent=2) + "\n", encoding="utf-8"
    )

    # 2. systeminfo.json — buyer's window-geometry stub.
    game_name = "Minecraft"
    systeminfo = {
        "gameProcessName": game_name,
        "x": 0,
        "y": 0,
        "width": DEFAULT_VIDEO_WIDTH,
        "height": DEFAULT_VIDEO_HEIGHT,
        "recordDpi": 1.0,
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
    "SYSTEMINFO_FILENAME",
    "adapt_phase1_to_buyer_spec",
    "camera_intrinsics_for_minecraft",
    "euler_to_quat_xyzw",
    "minecraft_position_to_buyer",
    "minecraft_yaw_pitch_to_buyer_oula",
]
