#!/usr/bin/env python3
"""
bin/recorder_replay_mod_postprocess.py — Replay Mod ``.mcpr`` post-processor (G274, W31).

PRD goal (A2/A3)
----------------
Buyer spec demands real camera 6DoF + quaternion timeseries in
``action_camera.json`` (cameraX/Y/Z + qW/qX/qY/qZ). The current recorder
emits placeholder zeros, which the validator flags. Replay Mod
(https://www.replaymod.com/) records full camera state into a ``.mcpr``
file (a zip archive) during play; this module reads that file and writes
the missing fields into the existing ``action_camera.json``.

Status — STUB WITH GRACEFUL DEGRADATION
---------------------------------------
Replay Mod's binary on-disk format is documented only by its source
(github.com/ReplayMod/ReplayMod). The interesting block is the
``recording.tmcpr`` packet stream — a length-prefixed sequence of
Minecraft network packets where the relevant ones are
``EntityTeleport`` / ``EntityLook`` / ``EntityHeadLook`` for the local
player entity. Decoding requires a full MC protocol VarInt parser and
matching to the recorder's start timestamp; doing that *correctly* is
~500 lines of protocol-version-aware code (the packet IDs change every
MC release).

Per the W31 brief, **we ship a stub today** that:

1. Recognises a ``.mcpr`` file (zip containing ``metaData.json`` and
   ``recording.tmcpr``) and reads the high-level metadata.
2. Returns a ``PostprocessResult`` with ``status='stub'`` so callers
   know real 6DoF data is *not* yet available.
3. Falls through with ``status='no_mcpr'`` when no Replay Mod file is
   present, so the recorder pipeline continues uninterrupted.
4. Writes a placeholder camera track (zeros) only on explicit request,
   so ``action_camera.json`` keeps its schema.

A TODO at the top of :func:`extract_camera_track` documents the binary
parser interface for the follow-up spec (G27x / W32+).

Standalone — stdlib only (``zipfile``, ``json``, ``struct``).
"""

from __future__ import annotations

import json
import struct
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_HZ = 30
META_DATA_NAME = "metaData.json"
RECORDING_NAME = "recording.tmcpr"

# ---------------------------------------------------------------------------
# TODO (W32+, G27x successor):
#
#   Replay Mod's recording.tmcpr is a sequence of:
#
#       [int32 BE timestamp_ms][int32 BE length][bytes packet]  *  N
#
#   Each ``packet`` is a Minecraft Java Edition clientbound network
#   packet whose first byte(s) are a VarInt packet ID. The packet IDs
#   that move the local player camera (entity ID == metaData.json's
#   ``selfId``) are version-dependent. For 1.20.x:
#
#       0x68  Player Position+Look (s2c)
#       0x66  Entity Teleport
#       0x35  Entity Look
#       0x36  Entity Head Look
#
#   The full parser must:
#     1. Read MC protocol version from metaData.json.
#     2. Map version → packet ID table (use mcdata or a vendored copy).
#     3. Decode VarInt packet length, VarInt packet ID, then the
#        position fields (3× double for X/Y/Z, 2× float for yaw/pitch
#        in degrees).
#     4. Convert yaw/pitch to a quaternion (Y-up world, Minecraft
#        convention: yaw rotates around Y, pitch around X).
#     5. Resample to ``DEFAULT_HZ`` (30) using the same algorithm as
#        :mod:`recorder_record_resampler`.
#
#   Until that parser exists, :func:`extract_camera_track` returns
#   status='stub' and emits a zero-filled track on request.
# ---------------------------------------------------------------------------


@dataclass
class CameraSample:
    """One frame of 6DoF camera state."""

    t_seconds: float
    pos_x: float = 0.0
    pos_y: float = 0.0
    pos_z: float = 0.0
    quat_w: float = 1.0
    quat_x: float = 0.0
    quat_y: float = 0.0
    quat_z: float = 0.0


@dataclass
class PostprocessResult:
    """Returned by :func:`run` to describe what (if anything) happened."""

    status: str  # 'ok' | 'stub' | 'no_mcpr' | 'error'
    samples: List[CameraSample] = field(default_factory=list)
    duration_seconds: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    notes: str = ""


def _quat_from_yaw_pitch(yaw_deg: float, pitch_deg: float) -> tuple:
    """Convert MC yaw/pitch (degrees) → unit quaternion (w,x,y,z)."""
    import math

    yaw = math.radians(yaw_deg)
    pitch = math.radians(pitch_deg)
    cy, sy = math.cos(yaw * 0.5), math.sin(yaw * 0.5)
    cp, sp = math.cos(pitch * 0.5), math.sin(pitch * 0.5)
    qw = cy * cp
    qx = cy * sp
    qy = sy * cp
    qz = -sy * sp
    return (qw, qx, qy, qz)


def _read_metadata(zf: zipfile.ZipFile) -> Dict[str, Any]:
    """Best-effort read of metaData.json from a ``.mcpr`` zip."""
    try:
        with zf.open(META_DATA_NAME) as fh:
            return json.loads(fh.read().decode("utf-8", errors="replace"))
    except (KeyError, json.JSONDecodeError, OSError):
        return {}


def _scan_recording(zf: zipfile.ZipFile) -> Dict[str, Any]:
    """Lightweight scan that just measures duration + packet count."""
    info = {"packet_count": 0, "duration_ms": 0, "scanned_bytes": 0}
    try:
        with zf.open(RECORDING_NAME) as fh:
            data = fh.read()
    except (KeyError, OSError):
        return info
    info["scanned_bytes"] = len(data)
    cursor = 0
    last_ts = 0
    count = 0
    while cursor + 8 <= len(data):
        try:
            ts, length = struct.unpack(">II", data[cursor : cursor + 8])
        except struct.error:
            break
        cursor += 8
        if length > len(data) - cursor:
            break
        cursor += length
        last_ts = ts
        count += 1
        if count > 1_000_000:
            break  # defensive cap — corrupted file
    info["packet_count"] = count
    info["duration_ms"] = last_ts
    return info


def extract_camera_track(
    mcpr_path: Path,
    hz: int = DEFAULT_HZ,
    emit_zero_track: bool = True,
) -> PostprocessResult:
    """Parse a ``.mcpr`` file. Currently STUB — see TODO above.

    Parameters
    ----------
    mcpr_path
        Absolute path to the ``.mcpr`` archive.
    hz
        Output rate. The placeholder zero track is sized to
        ``ceil(duration_ms/1000 * hz)``.
    emit_zero_track
        If True (default), populate ``samples`` with zero-pose frames so
        downstream JSON writers always see a valid track.
    """
    if not mcpr_path.is_file():
        return PostprocessResult(status="no_mcpr", notes=f"not a file: {mcpr_path}")
    if not zipfile.is_zipfile(mcpr_path):
        return PostprocessResult(status="error", notes="file is not a zip / not .mcpr")
    try:
        with zipfile.ZipFile(mcpr_path) as zf:
            meta = _read_metadata(zf)
            scan = _scan_recording(zf)
    except zipfile.BadZipFile as exc:
        return PostprocessResult(status="error", notes=f"bad zip: {exc}")
    duration_s = max(scan.get("duration_ms", 0) / 1000.0, 0.0)
    samples: List[CameraSample] = []
    if emit_zero_track and duration_s > 0:
        total_frames = int(round(duration_s * hz))
        for f in range(total_frames):
            samples.append(CameraSample(t_seconds=round(f / hz, 6)))
    return PostprocessResult(
        status="stub",
        samples=samples,
        duration_seconds=duration_s,
        metadata={
            "mc_version": meta.get("mcversion"),
            "server_name": meta.get("serverName"),
            "self_id": meta.get("selfId"),
            "single_player": meta.get("singleplayer"),
            "packet_count": scan.get("packet_count"),
            "scanned_bytes": scan.get("scanned_bytes"),
        },
        notes=(
            "Replay Mod binary parser not yet implemented — see TODO header. "
            "Zero-pose camera track emitted so action_camera.json schema is preserved."
        ),
    )


def find_latest_mcpr(replay_dir: Optional[Path] = None) -> Optional[Path]:
    """Return the most recent ``.mcpr`` file under the Replay Mod folder."""
    if replay_dir is None:
        candidates: List[Path] = []
        import os

        appdata = os.environ.get("APPDATA")
        if appdata:
            candidates.append(Path(appdata) / ".minecraft" / "replay_recordings")
        home = Path.home()
        candidates.extend(
            [
                home / ".minecraft" / "replay_recordings",
                home / "Library" / "Application Support" / "minecraft" / "replay_recordings",
            ]
        )
        for c in candidates:
            if c.is_dir():
                replay_dir = c
                break
    if replay_dir is None or not replay_dir.is_dir():
        return None
    files = sorted(replay_dir.glob("*.mcpr"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def merge_into_action_camera(
    action_camera_path: Path,
    result: PostprocessResult,
) -> bool:
    """Write camera fields of ``result`` into an existing action_camera.json."""
    if not action_camera_path.is_file():
        return False
    try:
        data = json.loads(action_camera_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    frames = data.get("frames")
    if not isinstance(frames, list):
        return False
    n = min(len(frames), len(result.samples))
    for i in range(n):
        s = result.samples[i]
        frames[i].update(
            {
                "cameraX": s.pos_x,
                "cameraY": s.pos_y,
                "cameraZ": s.pos_z,
                "qW": s.quat_w,
                "qX": s.quat_x,
                "qY": s.quat_y,
                "qZ": s.quat_z,
            }
        )
    data["camera_track_status"] = result.status
    data["camera_track_notes"] = result.notes
    action_camera_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return True


def run(
    mcpr_path: Optional[Path] = None,
    action_camera_path: Optional[Path] = None,
    hz: int = DEFAULT_HZ,
) -> PostprocessResult:
    """High-level entry: locate, extract, optionally merge."""
    target = mcpr_path or find_latest_mcpr()
    if target is None:
        return PostprocessResult(status="no_mcpr", notes="no .mcpr file located")
    result = extract_camera_track(target, hz=hz)
    if action_camera_path is not None and result.samples:
        merge_into_action_camera(action_camera_path, result)
    return result


def _main(argv: List[str]) -> int:
    """CLI: ``recorder_replay_mod_postprocess.py [path/to.mcpr] [action_camera.json]``"""
    mcpr = Path(argv[0]) if argv else None
    ac = Path(argv[1]) if len(argv) > 1 else None
    result = run(mcpr_path=mcpr, action_camera_path=ac)
    print(f"status            = {result.status}")
    print(f"duration_seconds  = {result.duration_seconds:.3f}")
    print(f"samples           = {len(result.samples)}")
    print(f"metadata          = {json.dumps(result.metadata, indent=2)}")
    if result.notes:
        print(f"notes             = {result.notes}")
    return 0 if result.status in ("ok", "stub", "no_mcpr") else 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
