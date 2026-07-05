#!/usr/bin/env python3
"""
beamng_telemetry_capture.py — Option-C BeamNG.drive capture path.

Connects to a running BeamNG.drive (or BeamNG.tech) instance via the
official ``BeamNGpy`` Python library and writes an
``engine_telemetry.json`` sidecar in the buyer-spec engine-fields shape
that ``bin/convert_to_buyer_spec.py --engine-fields-from`` consumes.

This replaces a per-title engine hook entirely for BeamNG: BeamNGpy is
a vendor-published research SDK (see ``docs/OPTION_C_THIRD_PARTY_SDKS.md``
§4.1) and exposes ``vehicle.poll_sensors()`` directly — no anti-cheat,
no memory scanning, no shader injection.

Usage::

    # Side A: launch BeamNG.drive / BeamNG.tech with research mode enabled
    #         and BeamNGpy listening on its default port (25252).

    # Side B: capture 30 Hz × 300 s = 9000 records into a sidecar:
    python bin/beamng_telemetry_capture.py \\
        --output engine_telemetry.json \\
        [--host 127.0.0.1] [--port 25252] \\
        [--vehicle-id ego] \\
        [--frame-rate 30] [--duration 300] \\
        [--screenshots-dir captures/beamng/cam] \\
        [--cam-resolution 1920x1080] [--cam-fov 90]

The output JSON matches the per-frame engine-fields shape::

    {
      "frames": [
        {
          "frame": 0,
          "player_position": [x, y, z],
          "player_rotation_quaternion": [w, x, y, z],
          "player_speed": float,
          "metric_scale": 1.0,
          ...
        },
        ...
      ]
    }

BeamNGpy is an *optional* runtime dep — the import is lazy so the CLI
loads (and ``--help`` works) on hosts that have not installed it. When
it is missing the script exits with a one-line install hint.

Coordinate conversion (BeamNG → buyer-spec)
-------------------------------------------
BeamNG uses a right-handed, Z-up world axis (per the BeamNG Lua docs:
``getPosition()`` returns metres in world space, Z is the world-up
axis). The buyer-spec follows the Wave-6 ``camera_position`` convention:
right-handed, **Y-up**, metres. We swap (Y, Z) and negate Z to flip
handedness so a BeamNG vehicle climbing a hill (rising +Z) becomes a
record with rising +Y in the sidecar — same convention as the camera
and player_position fields the converter already merges from real
recordings.

    # BeamNG world (X_east, Y_north, Z_up)  ->  Buyer-spec (X_right, Y_up, Z_forward)
    [bx, by, bz]  ->  [bx, bz, -by]

For rotations BeamNG returns quaternion ``(x, y, z, w)`` and the buyer
spec uses ``(w, x, y, z)`` — we re-pack and apply the same axis swap.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import math
import sys
import time
from pathlib import Path
from typing import Any

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy BeamNGpy import — keep CLI usable (incl. ``--help``) without the dep.
# ---------------------------------------------------------------------------


def _require_beamngpy() -> Any:
    """Import ``beamngpy`` lazily.

    Returns the ``beamngpy`` module. Exits with a one-line install hint
    when the package is not present — operators on capture hosts that
    have not yet ``pip install beamngpy``'d should see the hint rather
    than a raw ``ModuleNotFoundError`` traceback.
    """
    try:
        import beamngpy  # noqa: PLC0415 - intentional lazy import
    except ImportError as exc:  # pragma: no cover - exercised on stripped envs
        raise SystemExit(
            "beamngpy is required for BeamNG telemetry capture. "
            "Install with: pip install beamngpy "
            "(see docs/BEAMNG_INTEGRATION.md for the full setup runbook)"
        ) from exc
    return beamngpy


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Default BeamNGpy listener port — matches the BeamNG.tech research-mode
#: default. Override with ``--port`` for non-standard installs.
_DEFAULT_PORT: int = 25252

#: Default capture rate. The buyer-spec sample rate is 30 Hz (matches our
#: recorder's nominal frames-per-second), so the sidecar lines up 1:1 with
#: ``frames.jsonl`` indices.
_DEFAULT_FRAME_RATE: int = 30

#: Default capture duration in seconds. Matches the 5-minute buyer sample
#: bundle (see ``samples/5min-buyer-spec/``).
_DEFAULT_DURATION_SEC: int = 300

#: Default vehicle name to attach to. ``ego`` is the BeamNGpy convention
#: for the player-controlled vehicle in research scenarios.
_DEFAULT_VEHICLE_ID: str = "ego"

#: Default camera resolution / FOV for the optional Camera sensor.
_DEFAULT_CAM_RES: tuple[int, int] = (1920, 1080)
_DEFAULT_CAM_FOV_DEG: float = 90.0


# ---------------------------------------------------------------------------
# Coordinate conversion: BeamNG (Z-up, RH) -> buyer-spec (Y-up, RH)
# ---------------------------------------------------------------------------


def _beamng_pos_to_buyer(pos: tuple[float, float, float] | list[float]) -> list[float]:
    """Convert a BeamNG world position to the buyer-spec axis convention.

    BeamNG: ``(x_east, y_north, z_up)`` metres, right-handed.
    Buyer:  ``(x_right, y_up, z_forward)`` metres, right-handed.

    The mapping ``(bx, by, bz) -> (bx, bz, -by)`` swaps Y/Z and negates
    Z to keep the basis right-handed; this matches the orientation the
    Wave-6 ``camera_position`` field already uses in real bundles.
    """
    bx, by, bz = float(pos[0]), float(pos[1]), float(pos[2])
    return [bx, bz, -by]


def _beamng_quat_to_buyer(
    quat_xyzw: tuple[float, float, float, float] | list[float],
) -> list[float]:
    """Convert a BeamNG ``(x, y, z, w)`` quaternion to buyer-spec
    ``(w, x, y, z)`` with the same axis swap as the position helper.

    The axis swap for the imaginary part mirrors ``_beamng_pos_to_buyer``:
    ``(qx, qy, qz) -> (qx, qz, -qy)``.
    """
    qx, qy, qz, qw = (
        float(quat_xyzw[0]),
        float(quat_xyzw[1]),
        float(quat_xyzw[2]),
        float(quat_xyzw[3]),
    )
    return [qw, qx, qz, -qy]


def _quat_to_oula(q_wxyz: list[float]) -> list[float]:
    """Convert ``(w, x, y, z)`` quaternion to ``[yaw, pitch, roll]`` in
    radians, matching the buyer-spec ``camera_rotation_oula`` shape.

    Uses the standard intrinsic ZYX (yaw-pitch-roll) decomposition.
    """
    w, x, y, z = q_wxyz[0], q_wxyz[1], q_wxyz[2], q_wxyz[3]
    # roll (x-axis rotation)
    sinr_cosp = 2.0 * (w * x + y * z)
    cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
    roll = math.atan2(sinr_cosp, cosr_cosp)
    # pitch (y-axis rotation), clamp to avoid NaN at the poles.
    sinp = 2.0 * (w * y - z * x)
    if sinp > 1.0:
        sinp = 1.0
    elif sinp < -1.0:
        sinp = -1.0
    pitch = math.asin(sinp)
    # yaw (z-axis rotation)
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    yaw = math.atan2(siny_cosp, cosy_cosp)
    return [yaw, pitch, roll]


def _vector_speed_mps(prev: list[float] | None, curr: list[float] | None, dt_sec: float) -> float:
    """Euclidean speed in metres/second between two buyer-spec positions.

    Returns ``0.0`` on the first frame (``prev is None``) or when the
    timestep is non-positive — matches the converter's
    ``_camera_speeds`` zero-on-frame-0 convention.
    """
    if prev is None or curr is None or dt_sec <= 0.0:
        return 0.0
    dx = curr[0] - prev[0]
    dy = curr[1] - prev[1]
    dz = curr[2] - prev[2]
    return math.sqrt(dx * dx + dy * dy + dz * dz) / dt_sec


# ---------------------------------------------------------------------------
# Sensor polling
# ---------------------------------------------------------------------------


def _extract_sensor_values(sensors: Any) -> dict[str, Any]:
    """Pull position / rotation / velocity out of a BeamNGpy sensor poll.

    BeamNGpy 1.x returns sensor data as a dict-like ``{"state": {...}}``
    structure; the exact key path has shifted across versions. We look
    in a few well-known places to be robust to either pre-1.26 (old
    dict) or 1.26+ (``State`` sensor) shapes.
    """
    state: dict[str, Any] = {}
    if hasattr(sensors, "data"):  # pragma: no cover - new style
        state = dict(getattr(sensors, "data", {}) or {})
    elif isinstance(sensors, dict):
        if "state" in sensors and isinstance(sensors["state"], dict):
            state = dict(sensors["state"])
        else:
            state = dict(sensors)
    pos = state.get("pos") or state.get("position")
    rot = state.get("rotation") or state.get("rot_quat") or state.get("rotation_quaternion")
    vel = state.get("vel") or state.get("velocity")
    return {"pos": pos, "rotation": rot, "velocity": vel}


def _build_frame_record(
    *,
    frame_index: int,
    sensor_values: dict[str, Any],
    prev_buyer_pos: list[float] | None,
    dt_sec: float,
    metric_scale: float,
) -> dict[str, Any]:
    """Assemble one buyer-spec engine-fields frame from a sensor poll.

    Returns the dict in the shape ``_load_engine_fields`` expects. Any
    sensor field that BeamNG omitted on this tick lands as ``None``;
    the converter falls back to ``None`` for those keys exactly as it
    does for un-supplied frames.
    """
    raw_pos = sensor_values.get("pos")
    raw_rot = sensor_values.get("rotation")
    raw_vel = sensor_values.get("velocity")

    buyer_pos: list[float] | None = None
    if raw_pos is not None and len(raw_pos) >= 3:
        buyer_pos = _beamng_pos_to_buyer(raw_pos)

    buyer_quat: list[float] | None = None
    buyer_oula: list[float] | None = None
    if raw_rot is not None and len(raw_rot) >= 4:
        buyer_quat = _beamng_quat_to_buyer(raw_rot)
        buyer_oula = _quat_to_oula(buyer_quat)

    if raw_vel is not None and len(raw_vel) >= 3:
        # BeamNG reports velocity in world axes (m/s). Use the magnitude
        # in the *original* basis — speed is rotation-invariant, so
        # axis-swap is unnecessary and the magnitude is the same.
        speed = math.sqrt(float(raw_vel[0]) ** 2 + float(raw_vel[1]) ** 2 + float(raw_vel[2]) ** 2)
    else:
        speed = _vector_speed_mps(prev_buyer_pos, buyer_pos, dt_sec)

    return {
        "frame": frame_index,
        "player_position": buyer_pos,
        "player_rotation_quaternion": buyer_quat,
        "player_rotation_oula": buyer_oula,
        "player_speed": float(speed),
        "camera_Follow Offset": None,
        "metric_scale": float(metric_scale),
    }


# ---------------------------------------------------------------------------
# Connection lifecycle
# ---------------------------------------------------------------------------


def _connect_and_get_vehicle(
    beamngpy_module: Any,
    *,
    host: str,
    port: int,
    vehicle_id: str,
    cam_resolution: tuple[int, int] | None,
    cam_fov_deg: float,
) -> tuple[Any, Any]:
    """Open a BeamNGpy session and return ``(beamng, vehicle)``.

    Uses the public BeamNGpy 1.x surface (``BeamNGpy``, ``Vehicle``,
    ``Camera``) — see ``docs/BEAMNG_INTEGRATION.md`` for the operator
    runbook. The optional ``Camera`` sensor is attached when a
    resolution is supplied so frames can be saved alongside the JSON.
    """
    # NOTE: BeamNGpy uses PascalCase class names by upstream convention. We
    # bind them through dynamic lookups (rather than direct ``import``) so the
    # capture module can be loaded without ``beamngpy`` installed; the
    # ``noqa: N806`` comments silence the PEP-8 lowercase-local check.
    beamng_cls = beamngpy_module.BeamNGpy
    vehicle_cls = beamngpy_module.Vehicle
    beamng = beamng_cls(host, port)
    beamng.open(launch=False)
    vehicle = vehicle_cls(vehicle_id, model="etk800")
    # If the vehicle is already in-scenario this is a no-op attach;
    # otherwise BeamNGpy raises and the operator hint in --help applies.
    with contextlib.suppress(AttributeError):  # pragma: no cover - older BeamNGpy
        # Pre-1.0 used scenario.add_vehicle; we don't drive scenarios here.
        beamng.connect_vehicle(vehicle)
    if cam_resolution is not None:
        camera_cls = getattr(beamngpy_module, "Camera", None)
        if camera_cls is not None:  # pragma: no cover - exercised on real BeamNG
            cam = camera_cls(
                pos=(0, -3, 1),
                direction=(0, 1, 0),
                resolution=cam_resolution,
                fov=cam_fov_deg,
                colour=True,
                depth=False,
                annotation=False,
            )
            with contextlib.suppress(AttributeError):
                vehicle.attach_sensor("camera", cam)
    return beamng, vehicle


def _disconnect(beamng: Any) -> None:
    """Best-effort BeamNGpy disconnect.

    BeamNGpy's ``close()`` raises on stale sockets / partially-open
    sessions; we swallow those so the script always exits cleanly even
    when the simulator has already torn down. The capture file is
    flushed before this call so disconnect failures never lose data.
    """
    try:
        beamng.close()
    except Exception as exc:  # noqa: BLE001 - intentional best-effort
        _LOG.debug("BeamNGpy close() failed during best-effort disconnect: %s", exc)
        return


# ---------------------------------------------------------------------------
# Main capture loop
# ---------------------------------------------------------------------------


def capture_telemetry(
    *,
    output_path: Path,
    host: str = "127.0.0.1",
    port: int = _DEFAULT_PORT,
    vehicle_id: str = _DEFAULT_VEHICLE_ID,
    frame_rate: int = _DEFAULT_FRAME_RATE,
    duration_sec: int = _DEFAULT_DURATION_SEC,
    metric_scale: float = 1.0,
    screenshots_dir: Path | None = None,
    cam_resolution: tuple[int, int] | None = None,
    cam_fov_deg: float = _DEFAULT_CAM_FOV_DEG,
    sleep_fn: Any = time.sleep,
    time_fn: Any = time.monotonic,
) -> Path:
    """Run the BeamNG capture loop and write an engine-fields JSON.

    Parameters
    ----------
    output_path:
        Destination JSON path. Parent directories are created.
    frame_rate:
        Polls per second. ``frame_rate * duration_sec`` records are
        written; with the 30 Hz × 300 s defaults that is exactly 9000
        rows, matching the buyer-spec sample bundle length.
    duration_sec:
        Capture window in seconds.
    screenshots_dir:
        When set, attempts to call ``Camera.poll`` and write per-frame
        PNGs as ``frame_NNNNNN.png``. Silently no-ops when no Camera
        sensor was attached; intentionally non-fatal because operators
        often capture without screenshots.
    sleep_fn / time_fn:
        Injection points for the test suite — defaults are
        ``time.sleep`` / ``time.monotonic``.

    Returns
    -------
    Path
        The output JSON path that was actually written.
    """
    beamngpy_mod = _require_beamngpy()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if screenshots_dir is not None:
        screenshots_dir = Path(screenshots_dir)
        screenshots_dir.mkdir(parents=True, exist_ok=True)

    beamng, vehicle = _connect_and_get_vehicle(
        beamngpy_mod,
        host=host,
        port=port,
        vehicle_id=vehicle_id,
        cam_resolution=cam_resolution,
        cam_fov_deg=cam_fov_deg,
    )

    n_frames = int(frame_rate) * int(duration_sec)
    period_sec = 1.0 / float(frame_rate)
    prev_pos: list[float] | None = None
    records: list[dict[str, Any]] = []
    start_t = float(time_fn())
    try:
        for i in range(n_frames):
            target_t = start_t + i * period_sec
            now_t = float(time_fn())
            wait = target_t - now_t
            if wait > 0.0:
                sleep_fn(wait)
            sensors = vehicle.poll_sensors()
            sensor_values = _extract_sensor_values(sensors)
            rec = _build_frame_record(
                frame_index=i,
                sensor_values=sensor_values,
                prev_buyer_pos=prev_pos,
                dt_sec=period_sec,
                metric_scale=metric_scale,
            )
            records.append(rec)
            prev_pos = rec["player_position"]
            # Optional screenshot side-effect — never lets a camera
            # error kill the telemetry capture.
            if screenshots_dir is not None:
                _maybe_write_screenshot(sensors, screenshots_dir, i)
    finally:
        _disconnect(beamng)
        # Always flush whatever we collected before bubbling exceptions.
        output_path.write_text(json.dumps({"frames": records}, indent=2) + "\n")

    return output_path


def _maybe_write_screenshot(sensors: Any, screenshots_dir: Path, frame_index: int) -> None:
    """Try to extract and save a Camera-sensor frame.

    BeamNGpy returns camera frames as PIL ``Image`` objects keyed under
    ``"camera"``. We look for either the new (object with ``.data``) or
    the old (plain dict) shape and skip silently when neither is found
    or when the underlying object lacks ``.save``.
    """
    cam_data: Any = None
    if hasattr(sensors, "data"):  # pragma: no cover - new style
        cam_data = (getattr(sensors, "data", {}) or {}).get("camera")
    elif isinstance(sensors, dict):
        cam_data = sensors.get("camera")
    if cam_data is None:
        return
    img = cam_data.get("colour") if isinstance(cam_data, dict) else cam_data
    if img is None or not hasattr(img, "save"):
        return
    try:
        img.save(screenshots_dir / f"frame_{frame_index:06d}.png")
    except Exception as exc:  # noqa: BLE001 - best-effort, telemetry must keep going
        _LOG.debug(
            "Failed to save BeamNG screenshot frame=%d dir=%s: %s",
            frame_index,
            screenshots_dir,
            exc,
        )
        return


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_resolution(value: str) -> tuple[int, int]:
    """Parse a ``"WxH"`` string into ``(width, height)``."""
    parts = value.lower().split("x")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"resolution must be WxH, got {value!r}")
    try:
        return int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"non-integer resolution: {value!r}") from exc


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser. Split out so tests can introspect it."""
    p = argparse.ArgumentParser(
        prog="beamng_telemetry_capture",
        description="Capture BeamNG.drive telemetry into a buyer-spec engine-fields JSON.",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path("engine_telemetry.json"),
        help="output JSON path (default: engine_telemetry.json)",
    )
    p.add_argument("--host", default="127.0.0.1", help="BeamNGpy host (default: 127.0.0.1)")
    p.add_argument(
        "--port",
        type=int,
        default=_DEFAULT_PORT,
        help=f"BeamNGpy port (default: {_DEFAULT_PORT})",
    )
    p.add_argument(
        "--vehicle-id",
        default=_DEFAULT_VEHICLE_ID,
        help=f"BeamNG vehicle name to attach to (default: {_DEFAULT_VEHICLE_ID})",
    )
    p.add_argument(
        "--frame-rate",
        type=int,
        default=_DEFAULT_FRAME_RATE,
        help=f"polls per second (default: {_DEFAULT_FRAME_RATE})",
    )
    p.add_argument(
        "--duration",
        type=int,
        default=_DEFAULT_DURATION_SEC,
        dest="duration_sec",
        help=f"capture duration in seconds (default: {_DEFAULT_DURATION_SEC})",
    )
    p.add_argument(
        "--metric-scale",
        type=float,
        default=1.0,
        help="metric_scale field to emit per frame (default: 1.0; metres)",
    )
    p.add_argument(
        "--screenshots-dir",
        type=Path,
        default=None,
        help="optional directory for per-frame PNG screenshots",
    )
    p.add_argument(
        "--cam-resolution",
        type=_parse_resolution,
        default=None,
        help="camera resolution WxH (e.g. 1920x1080); enables Camera sensor",
    )
    p.add_argument(
        "--cam-fov",
        type=float,
        default=_DEFAULT_CAM_FOV_DEG,
        help=f"camera horizontal FOV degrees (default: {_DEFAULT_CAM_FOV_DEG})",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    """Script entry point. Returns the exit code (0 on success)."""
    args = build_parser().parse_args(argv)
    cam_res: tuple[int, int] | None = args.cam_resolution
    if cam_res is None and args.screenshots_dir is not None:
        cam_res = _DEFAULT_CAM_RES
    out = capture_telemetry(
        output_path=args.output,
        host=args.host,
        port=args.port,
        vehicle_id=args.vehicle_id,
        frame_rate=args.frame_rate,
        duration_sec=args.duration_sec,
        metric_scale=args.metric_scale,
        screenshots_dir=args.screenshots_dir,
        cam_resolution=cam_res,
        cam_fov_deg=args.cam_fov,
    )
    print(f"wrote {out} ({args.frame_rate * args.duration_sec} frames)")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI passthrough
    sys.exit(main())
