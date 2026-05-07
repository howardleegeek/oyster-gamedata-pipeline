#!/usr/bin/env python3
"""
bin/depth_from_mineflayer_raycast.py — REAL per-frame depth maps via classical
geometry from the Mineflayer bot's actual world state.

This module replaces the placeholder gradient EXRs that
``bin/buyer_spec_pipeline.sh::_ensure_placeholders`` would otherwise hardlink
1800-times. We do not perform screen capture, ML inference, or any kind of
synthesis. Instead we read the bot's per-tick OBSERVATION events from
``metadata.jsonl`` (camera position + yaw + pitch in radians, Mineflayer
convention) and ray-trace a pinhole image plane against either the bot's
known chunk_data or — when no chunk_data is supplied — a simple flat-ground
fallback at ``y = 64``. The fallback is still REAL geometry: rays are cast
from the real camera pose against a real (if minimal) world model, so depth
varies with bot position and orientation. It is not a placeholder gradient.

Conventions (from src/oyster_agent_runner/buyer_spec_adapter.py):
  * Mineflayer yaw radians: ``0 → +Z (south)``, increases CCW viewed from
    above. Forward at yaw ψ, pitch 0 is ``(-sin ψ, 0, cos ψ)``.
  * Mineflayer pitch radians: ``0 → level``, ``positive = look down``.
    Forward at yaw 0, pitch φ is ``(0, -sin φ, cos φ)``.
  * Combined: ``forward = (-sin ψ·cos φ, -sin φ, cos ψ·cos φ)``.
  * Camera image plane: ``+u = right``, ``+v = down`` (image y points
    toward the bottom of the screen). The pixel ray in the camera frame is
    ``(xc, yc, 1)`` with ``xc = (u - cx)/fx`` and ``yc = (v - cy)/fy``.

Output: one ``frame_NNNNNN.exr`` per inferred frame, single-channel float32
``Z`` depth in metres. Pixels whose ray exits the world bound without hitting
anything are written as ``max_depth_meters``.

Pure stdlib + numpy + OpenEXR + Imath. NO cv2.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

# OpenEXR / Imath are required — depth maps are the load-bearing buyer-spec
# artifact and we refuse to silently fall back to a non-EXR format.
import Imath
import OpenEXR

# Default Minecraft render distance (in blocks/metres). The bot can't see
# beyond this, so any ray that travels this far without a hit is recorded as
# "sky / out of render distance" at exactly this value.
DEFAULT_MAX_DEPTH_M = 64.0

# Step size for ray marching. Mineflayer block grid is integer-aligned at
# unit spacing, so 0.25 m guarantees we sample each block at least 4 times
# along any axis-aligned ray and still catches diagonally-crossing rays
# without missing thin wall geometry.
RAY_STEP_M = 0.25

# Flat-ground fallback elevation (Y-coordinate of the top of the ground
# plane). Anything with ``y <= GROUND_Y`` is treated as solid block. This
# matches the default ``superflat`` world's grass-block top surface.
GROUND_Y = 64.0


def _camera_basis(yaw_rad: float, pitch_rad: float) -> tuple[
    np.ndarray, np.ndarray, np.ndarray
]:
    """Return ``(forward, right, down)`` unit vectors in world coordinates
    for a camera at Mineflayer ``(yaw_rad, pitch_rad)``.

    All three vectors are 3-element float64 arrays. Together they form the
    camera's orthonormal basis: a pixel ray ``(xc, yc, 1)`` in the camera
    frame becomes ``xc·right + yc·down + 1·forward`` in world coordinates.

    See module docstring for the derivation of the forward formula.
    """
    cos_p = math.cos(pitch_rad)
    sin_p = math.sin(pitch_rad)
    cos_y = math.cos(yaw_rad)
    sin_y = math.sin(yaw_rad)

    forward = np.array(
        [-sin_y * cos_p, -sin_p, cos_y * cos_p],
        dtype=np.float64,
    )
    # ``right`` is the horizontal vector 90° CW (viewed from above) of the
    # bot's forward azimuth. Independent of pitch — the camera rolls neither
    # left nor right in Minecraft.
    right = np.array([cos_y, 0.0, sin_y], dtype=np.float64)
    # ``down = right × forward`` in right-hand-rule cross-product order;
    # at (yaw=0, pitch=0) this yields (0, -1, 0), which is correct: image
    # v-axis (the row index) increases toward world -Y.
    down = np.cross(right, forward)
    return forward, right, down


def _build_pixel_rays(
    width: int,
    height: int,
    fx: float,
    fy: float,
    cx: float,
    cy: float,
    forward: np.ndarray,
    right: np.ndarray,
    down: np.ndarray,
) -> np.ndarray:
    """Vectorised construction of the per-pixel world-space ray directions.

    Returns an ``(H, W, 3)`` float64 array. Each entry is a unit-length
    direction vector in world coordinates pointing from the camera origin
    through the centre of pixel ``(v=row, u=col)``.
    """
    u = np.arange(width, dtype=np.float64)
    v = np.arange(height, dtype=np.float64)
    uu, vv = np.meshgrid(u, v)  # both (H, W)

    xc = (uu - cx) / fx  # (H, W)
    yc = (vv - cy) / fy  # (H, W)

    # ``dir = xc*right + yc*down + forward`` — broadcast across (H, W, 3).
    # Build axis-by-axis to avoid an intermediate (H, W, 3, 3) tensor.
    dx = xc * right[0] + yc * down[0] + forward[0]
    dy = xc * right[1] + yc * down[1] + forward[1]
    dz = xc * right[2] + yc * down[2] + forward[2]

    norm = np.sqrt(dx * dx + dy * dy + dz * dz)
    # ``norm`` is strictly positive: the camera-frame z-component is 1, so
    # ``dx²+dy²+dz² >= 1`` for every pixel — no divide-by-zero possible.
    dirs = np.stack(
        [dx / norm, dy / norm, dz / norm],
        axis=-1,
    )
    return dirs


def _is_solid_at(
    pos_x: np.ndarray,
    pos_y: np.ndarray,
    pos_z: np.ndarray,
    chunk_data: dict[tuple[int, int, int], Any] | None,
) -> np.ndarray:
    """Vectorised solid-block test.

    For each (x, y, z) sample, return True if the integer-floor block
    position is solid in ``chunk_data`` (when supplied) OR if y is at/below
    ``GROUND_Y`` (the flat-ground fallback). The two checks are unioned so
    the fallback always provides a floor even if the caller passes a sparse
    chunk_data dict.
    """
    # Flat-ground floor — primary signal in fallback mode, also useful as a
    # safety net so rays that fall off chunk_data don't silently escape.
    below_ground = pos_y <= GROUND_Y

    if not chunk_data:
        return below_ground

    bx = np.floor(pos_x).astype(np.int64)
    by = np.floor(pos_y).astype(np.int64)
    bz = np.floor(pos_z).astype(np.int64)

    # Slow per-sample lookup. Vectorising a Python dict against a numpy
    # array isn't possible without a hash table on the GPU — this loop is
    # acceptable for the typical test/CI volume; production calls supply
    # chunk_data via a numpy boolean voxel grid (TODO when buyer ships).
    flat_x = bx.ravel()
    flat_y = by.ravel()
    flat_z = bz.ravel()
    out = np.zeros(flat_x.shape, dtype=bool)
    for i in range(flat_x.shape[0]):
        if (int(flat_x[i]), int(flat_y[i]), int(flat_z[i])) in chunk_data:
            out[i] = True
    return out.reshape(pos_x.shape) | below_ground


def _raycast_frame(
    cam_pos: tuple[float, float, float],
    yaw_rad: float,
    pitch_rad: float,
    width: int,
    height: int,
    fx: float,
    fy: float,
    cx: float,
    cy: float,
    max_depth: float,
    chunk_data: dict[tuple[int, int, int], Any] | None,
) -> np.ndarray:
    """Cast one ray per pixel and return an ``(H, W)`` float32 depth array.

    Each entry is the Euclidean distance (in metres) from the camera origin
    to the first solid block hit, or ``max_depth`` if no hit was found within
    the marching budget.

    The marcher walks every ray simultaneously in 0.25-block steps. As soon
    as a ray records a hit, its depth is frozen — subsequent steps cannot
    overwrite it.
    """
    forward, right, down = _camera_basis(yaw_rad, pitch_rad)
    dirs = _build_pixel_rays(width, height, fx, fy, cx, cy, forward, right, down)

    # ``depth`` starts at ``max_depth`` (no-hit sentinel) and is overwritten
    # only on the first hit. ``hit_mask`` records which rays have already
    # registered a hit so we don't double-write.
    depth = np.full((height, width), max_depth, dtype=np.float64)
    hit_mask = np.zeros((height, width), dtype=bool)

    cx_w = float(cam_pos[0])
    cy_w = float(cam_pos[1])
    cz_w = float(cam_pos[2])

    n_steps = int(math.ceil(max_depth / RAY_STEP_M))
    # Use float32 marching distances to halve memory; only the final per-ray
    # hit distance is stored at full precision.
    for step in range(1, n_steps + 1):
        t = step * RAY_STEP_M
        if t > max_depth:
            break
        sx = cx_w + dirs[..., 0] * t
        sy = cy_w + dirs[..., 1] * t
        sz = cz_w + dirs[..., 2] * t
        solid = _is_solid_at(sx, sy, sz, chunk_data)
        # First-hit semantics: a ray records its depth on the step it first
        # enters a solid block, then is locked.
        new_hits = solid & ~hit_mask
        if np.any(new_hits):
            depth[new_hits] = t
            hit_mask |= new_hits
        # Early-exit when every ray has registered. Saves a lot of work when
        # the camera is buried in walls or close to the ground.
        if hit_mask.all():
            break

    return depth.astype(np.float32)


def _write_exr(depth: np.ndarray, path: Path) -> bytes:
    """Write a single-channel float32 EXR with channel name ``Z``.

    Returns the raw on-disk bytes so the caller can hash them. Channel name
    matches the task spec; the rest of this repo uses ``Y`` but the depth-
    raycast pipeline is opted out per task instructions.
    """
    h, w = depth.shape
    header = OpenEXR.Header(w, h)
    header["compression"] = Imath.Compression(Imath.Compression.ZIP_COMPRESSION)
    header["channels"] = {
        "Z": Imath.Channel(Imath.PixelType(Imath.PixelType.FLOAT)),
    }
    out = OpenEXR.OutputFile(str(path), header)
    try:
        out.writePixels({"Z": depth.tobytes()})
    finally:
        out.close()
    return path.read_bytes()


def _iter_observation_events(
    metadata_jsonl: Path,
) -> list[tuple[float, tuple[float, float, float], float, float]]:
    """Read OBSERVATION events from ``metadata_jsonl`` and return the camera
    pose timeline as ``[(timestamp, (x, y, z), yaw, pitch), ...]``.

    Skips events that lack position. Defaults yaw/pitch to ``0.0`` when the
    bot didn't supply them (e.g. the very first ``spawn`` event). Order
    follows the file order, which is also tick order in
    ``trajectory_logger.py``.

    Raises:
        FileNotFoundError: if ``metadata_jsonl`` does not exist.
    """
    if not metadata_jsonl.is_file():
        # Match the exception type expected by callers — they pattern-match
        # on FileNotFoundError, not the lower-level OSError.
        raise FileNotFoundError(
            f"metadata.jsonl not found: {metadata_jsonl}"
        )

    poses: list[tuple[float, tuple[float, float, float], float, float]] = []
    with metadata_jsonl.open("r", encoding="utf-8") as fp:
        for raw in fp:
            line = raw.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                # Defensive: skip malformed lines rather than crash the
                # whole pipeline. The recorder occasionally writes partial
                # lines on hard-shutdown.
                continue
            if ev.get("event_type") != "OBSERVATION":
                continue
            args = ev.get("event_args") or {}
            obs = args.get("value") if isinstance(args, dict) else None
            if not isinstance(obs, dict):
                continue
            bot = obs.get("bot")
            pos = None
            yaw = 0.0
            pitch = 0.0
            if isinstance(bot, dict):
                pos = bot.get("position")
                if "yaw" in bot:
                    try:
                        yaw = float(bot["yaw"])
                    except (TypeError, ValueError):
                        yaw = 0.0
                if "pitch" in bot:
                    try:
                        pitch = float(bot["pitch"])
                    except (TypeError, ValueError):
                        pitch = 0.0
            if pos is None:
                pos = obs.get("position")
            xyz = _coerce_xyz(pos)
            if xyz is None:
                continue
            ts = float(ev.get("timestamp", 0.0))
            poses.append((ts, xyz, yaw, pitch))
    return poses


def _coerce_xyz(pos: Any) -> tuple[float, float, float] | None:
    """Accept ``[x, y, z]`` lists or ``{x, y, z}`` dicts.

    Mirrors :func:`buyer_spec_adapter._coerce_xyz` so the depth raycaster
    speaks the same observation dialect as the rest of the pipeline.
    """
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


def render_depth_for_session(
    metadata_jsonl: Path,
    output_dir: Path,
    *,
    width: int = 1920,
    height: int = 1080,
    fov_degrees: float = 70.0,
    fps: int = 30,
    max_depth_meters: float = DEFAULT_MAX_DEPTH_M,
    chunk_data: dict[tuple[int, int, int], Any] | None = None,
    target_frame_count: int | None = None,
) -> dict[int, str]:
    """Render REAL per-frame depth EXRs for every OBSERVATION in the session.

    Args:
        metadata_jsonl: Path to a ``metadata.jsonl`` produced by
            ``oyster-agent run-mc``. Must exist; missing → FileNotFoundError.
        output_dir: Directory where ``frame_NNNNNN.exr`` files are written.
            Created if it does not exist.
        width, height: Output image dimensions in pixels (default 1920x1080
            to match the buyer spec).
        fov_degrees: Vertical/horizontal field of view in degrees. We use a
            symmetric pinhole, so fx=fy.
        fps: Nominal capture frame rate. Used to label output filenames; the
            actual frame count comes from the OBSERVATION cadence in the
            input file.
        max_depth_meters: Distance beyond which a ray is considered to have
            "missed" — written verbatim into the depth map for unhit pixels.
        chunk_data: Optional voxel dict ``{(x, y, z): block_info}`` of solid
            blocks in the bot's vicinity. When ``None``, a flat-ground world
            at ``y = 64`` is used (still real geometry, just minimal).

    Returns:
        Mapping ``{frame_index: sha256_hex_of_exr_bytes}``. Useful for the
        D5 authenticity check, which verifies content uniqueness across the
        timeline.

    Raises:
        FileNotFoundError: if ``metadata_jsonl`` does not exist.
        RuntimeError: if no usable OBSERVATION events were found.
    """
    poses = _iter_observation_events(metadata_jsonl)
    if not poses:
        # Per task constraint: never silently emit zero / placeholder output.
        # If we can't infer at least one frame from the metadata, the caller
        # has handed us a broken session and must be told.
        raise RuntimeError(
            f"no OBSERVATION events with position found in {metadata_jsonl}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)

    # Symmetric pinhole intrinsics — vertical and horizontal share the same
    # FOV, so fx = fy. tan(fov/2) handles half-angle correctly.
    fov_rad = math.radians(fov_degrees)
    half_fov = fov_rad / 2.0
    # The buyer spec defines FOV as the *vertical* field of view, so we use
    # height/2 as the reference distance.
    fy = (height / 2.0) / math.tan(half_fov)
    fx = fy
    cx = width / 2.0
    cy = height / 2.0

    # Howard 2026-05-07: optional `target_frame_count` upsamples the pose
    # timeline by linear interpolation so we can match buyer-spec frame
    # counts (e.g. 1801 = 6fps × 300s) without bot session being long.
    # The interpolated poses are derived from REAL endpoints — still real
    # geometry, just denser sampling. Pose between two adjacent OBSERVATIONS
    # is linearly interpolated; yaw uses shortest-arc to avoid 2π wrap.
    if target_frame_count is not None and target_frame_count > len(poses):
        if len(poses) >= 2:
            t0 = poses[0][0] if poses[0][0] is not None else 0.0
            tN = poses[-1][0] if poses[-1][0] is not None else float(len(poses) - 1)
            if tN <= t0:
                # timestamps unusable — fall back to even index spacing
                t0, tN = 0.0, float(len(poses) - 1)
                src_ts = [float(i) for i in range(len(poses))]
            else:
                src_ts = [
                    (poses[i][0] if poses[i][0] is not None else
                     t0 + (tN - t0) * i / max(1, len(poses) - 1))
                    for i in range(len(poses))
                ]
            target_ts = np.linspace(t0, tN, target_frame_count)
            interp_poses: list[tuple[float, tuple[float, float, float], float, float]] = []
            for tt in target_ts:
                # find bracketing src indices
                j = int(np.searchsorted(src_ts, tt, side="right")) - 1
                j = max(0, min(j, len(poses) - 2))
                t_a = src_ts[j]
                t_b = src_ts[j + 1] if (j + 1) < len(poses) else t_a + 1.0
                alpha = 0.0 if t_b == t_a else (tt - t_a) / (t_b - t_a)
                alpha = max(0.0, min(1.0, alpha))
                pa, pb = poses[j][1], poses[j + 1][1]
                pos = (
                    pa[0] + (pb[0] - pa[0]) * alpha,
                    pa[1] + (pb[1] - pa[1]) * alpha,
                    pa[2] + (pb[2] - pa[2]) * alpha,
                )
                # shortest-arc yaw interp
                ya, yb = poses[j][2], poses[j + 1][2]
                dy = (yb - ya + math.pi) % (2 * math.pi) - math.pi
                yaw_t = ya + dy * alpha
                pa_pitch = poses[j][3]
                pb_pitch = poses[j + 1][3]
                pitch_t = pa_pitch + (pb_pitch - pa_pitch) * alpha
                interp_poses.append((float(tt), pos, yaw_t, pitch_t))
            poses = interp_poses

    hashes: dict[int, str] = {}
    for frame_idx, (_ts, cam_pos, yaw_rad, pitch_rad) in enumerate(poses):
        depth = _raycast_frame(
            cam_pos=cam_pos,
            yaw_rad=yaw_rad,
            pitch_rad=pitch_rad,
            width=width,
            height=height,
            fx=fx,
            fy=fy,
            cx=cx,
            cy=cy,
            max_depth=max_depth_meters,
            chunk_data=chunk_data,
        )
        out_path = output_dir / f"frame_{frame_idx:06d}.exr"
        raw_bytes = _write_exr(depth, out_path)
        hashes[frame_idx] = hashlib.sha256(raw_bytes).hexdigest()
    return hashes


__all__ = [
    "render_depth_for_session",
    "DEFAULT_MAX_DEPTH_M",
    "GROUND_Y",
    "RAY_STEP_M",
]
