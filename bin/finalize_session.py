#!/usr/bin/env python3
"""
finalize_session.py — Post-record PRD §3 data-completeness finalizer.

What it does
============
The recorder writes `action_camera.json` during recording BEFORE
`game_state.jsonl` is available in the session dir. (mc-mod writes
game_state.jsonl to `~/Documents/OysterClips/active_session/` — a
DIFFERENT path from the recorder's session dir.) Result: action_camera
quaternion / position / Follow_Offset fields stay NULL, and PRD lint
criteria #13 (Quaternion xyzw Order) and #14 (Quaternion Normalization)
fail with "No quaternion data found in any frame."

This finalizer closes that gap post-record without touching the Rust
recorder:

  1. **Sync game_state.jsonl** from `~/Documents/OysterClips/active_session/`
     into `<session_dir>/game_state.jsonl` (if not already present).

  2. **Backfill action_camera.json** by matching each frame to its
     nearest game_state tick via timestamp, then computing quaternion
     from (yaw_deg, pitch_deg) Euler angles and copying position +
     Follow_Offset.

  3. **Generate gameinfo.xlsx** by invoking the existing
     `vendor/recorder/scripts/generate_gameinfo.py` (PRD §3.3, 14
     fields).

  4. **Generate depth/*.exr (PRD §3.4)** by invoking the new
     `bin/depth_exr_writer.py` — 6 fps sampling of recording.mp4 through
     DepthAnything V2 Small ONNX (via onnxruntime-directml). Produces
     1800 single-channel float32 EXR files named `000000.exr` ..
     `001799.exr` in `session_dir/depth/`. Opt-out: pass `--no-depth`
     to finalize_session OR set `OYSTER_SKIP_DEPTH=1` env var to skip
     (e.g. for fast finalize during development). Skipping is flagged
     with WARN because lint criteria #15, #16, and #24 depend on this
     output existing.

  5. **Generate audio_check.json** by invoking the existing
     `bin/audio_continuity_check.py` (PRD §6 lint #38 dependency).

  6. **Generate input_latency.json** by post-hoc estimation from
     inputs.jsonl and frames.jsonl (PRD §3.1 lint #39). This computes
     the median latency between keyboard input events and subsequent
     video frames. Opt-out: set `OYSTER_SKIP_LATENCY_MEASUREMENT=1` env.

After finalize_session.py runs, a recording session has every PRD §3
deliverable.

Lint score impact on a fresh recording: ≥8 criteria unblocked
(#13, #14, #15, #16, #24-partial, #38, #39, plus #24 fully when depth is
generated). The remaining #27/#31 input-pipeline failures are
independent bugs deferred to rc19.x.

Why post-record (not in-recorder)
---------------------------------
Patching the Rust recorder to look for game_state.jsonl at the mc-mod
fallback path would unblock all this AT RECORDING TIME — but that's a
submodule change with cargo-check risk. The post-record approach is
pure Python, runs against existing recordings idempotently, and ships
without touching the proven-good submodule SHA (7bd4d8c).

PRD §3 alignment: this is the "honest" finalize step the customer
expects between recording and delivery.

Usage
-----
  python bin/finalize_session.py <session_dir>
  python bin/finalize_session.py <session_dir> --verbose
  OYSTER_SKIP_LATENCY_MEASUREMENT=1 python bin/finalize_session.py <session_dir>  # skip latency
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# mc-mod writes game_state.jsonl here during active recording
MC_MOD_FALLBACK = Path.home() / "Documents" / "OysterClips" / "active_session" / "game_state.jsonl"

# PRD §3.1: max allowed input-to-frame latency in ms
PRD_LATENCY_LIMIT_MS = 20.0

# Suspicious latency threshold — warn if median exceeds this
SUSPICIOUS_LATENCY_MS = 50.0

# rc19.0.3 coord-system constants — L1 oracle:
# vendor/enrichment/docs/COORDINATE_SYSTEMS_GUIDE.md:55
# Minecraft runs the world simulation at 20 ticks/sec and 1 block = 1 m,
# so game_state velocity in blocks/tick × 20 = m/s (PRD §3.2 unit).
MC_TICKS_PER_SECOND = 20.0
# Minecraft vanilla entity gravity ≈ 32 m/s² (NOT Earth's 9.8) — emitted
# into gameinfo.xlsx so the buyer's physics layer uses the right constant.
MC_WORLD_GRAVITY_MPS2 = 32.0


# ---------------------------------------------------------------------------
# Quaternion helpers (from yaw/pitch Euler angles)
# ---------------------------------------------------------------------------

def _axis_quat(axis: str, angle_rad: float) -> Tuple[float, float, float, float]:
    """Unit quaternion (x, y, z, w) for a rotation about a principal axis."""
    h = angle_rad / 2.0
    s, c = math.sin(h), math.cos(h)
    if axis == "x":
        return (s, 0.0, 0.0, c)
    if axis == "y":
        return (0.0, s, 0.0, c)
    return (0.0, 0.0, s, c)  # "z"


def _quat_mul(a: Tuple[float, float, float, float],
              b: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
    """Hamilton product of two XYZW quaternions: a ⊗ b."""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def euler_to_quat_xyzw(yaw_deg: float, pitch_deg: float, roll_deg: float = 0.0) -> List[float]:
    """Convert Euler angles (degrees) to an XYZW quaternion — BUYER-SPEC convention.

    rc19.0.3: rewritten to match the buyer-pipeline oracle exactly.
    Source of truth: vendor/enrichment/src/oyster_enrichment/quaternion_utils.py
    (the buyer-accepted module). Per its docstring, buyer_spec is:
      - pitch = rotation about +X
      - yaw   = rotation about +Y   (Y-up frame — the OLD local code wrongly
                                     rotated yaw about +Z; that produced
                                     wrong-convention quaternions)
      - roll  = rotation about +Z
      - application order Y-X-Z extrinsic → q = q_roll ⊗ q_pitch ⊗ q_yaw

    A self-contained reimplementation (cannot import vendor/enrichment — it
    is a submodule, not bundled into the recorder installer). Verified
    against the oracle by tests/bin/test_finalize_coord_physics.py.
    """
    qy = _axis_quat("y", math.radians(yaw_deg))
    qp = _axis_quat("x", math.radians(pitch_deg))
    qr = _axis_quat("z", math.radians(roll_deg))
    # Y-X-Z extrinsic: q = q_roll ⊗ q_pitch ⊗ q_yaw
    qx, qyy, qz, qw = _quat_mul(qr, _quat_mul(qp, qy))

    n = math.sqrt(qx * qx + qyy * qyy + qz * qz + qw * qw)
    if n > 0:
        qx /= n; qyy /= n; qz /= n; qw /= n

    return [qx, qyy, qz, qw]


# ---------------------------------------------------------------------------
# Step 1 — sync game_state.jsonl
# ---------------------------------------------------------------------------

def sync_game_state(session_dir: Path, verbose: bool = False) -> bool:
    """Copy mc-mod's game_state.jsonl into session_dir if not already there."""
    dst = session_dir / "game_state.jsonl"
    if dst.exists() and dst.stat().st_size > 0:
        if verbose:
            print(f"  game_state.jsonl already in session_dir ({dst.stat().st_size} bytes) — skip sync")
        return True

    if not MC_MOD_FALLBACK.exists():
        if verbose:
            print(f"  no game_state.jsonl at mc-mod fallback {MC_MOD_FALLBACK} — skipping (mc-mod may not have run)")
        return False

    shutil.copy2(MC_MOD_FALLBACK, dst)
    if verbose:
        print(f"  synced game_state.jsonl: {MC_MOD_FALLBACK} → {dst} ({dst.stat().st_size} bytes)")
    return True


# ---------------------------------------------------------------------------
# Step 2 — backfill action_camera.json with quaternion + position
# ---------------------------------------------------------------------------

def _get_frame_t_ns(frame: dict) -> Optional[int]:
    """Return the frame's recording-relative timestamp in nanoseconds.

    rc19 recorder writes `timestamp_ns` (preferred) and `timestamp` (seconds,
    float). Older recordings used `t_ns`. We accept all three so this function
    works against rc17.x, rc18.x, and rc19+ session dirs without coupling to
    the recorder build that emitted them.
    """
    t_ns = frame.get("timestamp_ns")
    if t_ns is not None:
        return int(t_ns)
    t_ns = frame.get("t_ns")
    if t_ns is not None:
        return int(t_ns)
    ts = frame.get("timestamp")
    if ts is not None:
        try:
            return int(float(ts) * 1_000_000_000)
        except (TypeError, ValueError):
            return None
    return None


def _get_frame_index(frame: dict) -> Optional[int]:
    """Return the frame's integer index.

    rc19 recorder writes `frame_index` (preferred) and `frame_number`. Older
    recordings used `idx`. Accept all three.
    """
    for key in ("frame_index", "idx", "frame_number"):
        v = frame.get(key)
        if v is not None:
            try:
                return int(v)
            except (TypeError, ValueError):
                continue
    return None


def backfill_action_camera(session_dir: Path, verbose: bool = False) -> int:
    """Patch action_camera.json frames with quaternion + position from game_state.

    Returns count of frames patched.

    Timestamp alignment
    -------------------
    The recorder writes action_camera frames with `timestamp_ns` (nanoseconds
    since recording start, monotonic). mc-mod writes game_state ticks with
    `timestamp_ms` (Unix-epoch wall clock). We anchor frame[0].timestamp_ns
    to game_state[0].timestamp_ms and convert every other frame to an
    absolute ms value relative to that anchor, then binary-search the
    game_state tick array for nearest neighbor.

    Naming compatibility
    --------------------
    Frame key names changed between recorder builds:
      rc17.x / rc18.x : `idx`,         `t_ns`
      rc19+           : `frame_index`, `timestamp_ns` (also `timestamp` seconds)
    `_get_frame_t_ns` and `_get_frame_index` accept both so finalize works
    against any session dir on disk.
    """
    ac_path = session_dir / "action_camera.json"
    gs_path = session_dir / "game_state.jsonl"

    if not ac_path.exists():
        if verbose:
            print("  action_camera.json missing — skip backfill")
        return 0
    if not gs_path.exists():
        if verbose:
            print("  game_state.jsonl missing — skip backfill")
        return 0

    ac = json.loads(ac_path.read_text())
    if isinstance(ac, dict) and "frames" in ac:
        frames = ac["frames"]
    elif isinstance(ac, list):
        frames = ac
    else:
        if verbose:
            print("  action_camera.json unexpected structure — skip backfill")
        return 0

    if not frames:
        if verbose:
            print("  action_camera.json contains no frames — skip backfill")
        return 0

    # Load game_state.jsonl into memory (it's ~4 MB, fine).
    gs_lines: List[dict] = []
    gs_ts: List[int] = []
    with open(gs_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                # Skip a corrupt mid-stream line rather than aborting backfill
                # — mc-mod can leave a half-written final line if Minecraft
                # was killed mid-flush.
                continue
            ts = obj.get("timestamp_ms")
            if ts is None:
                continue
            gs_lines.append(obj)
            gs_ts.append(int(ts))

    if not gs_lines:
        if verbose:
            print("  game_state.jsonl empty — skip backfill")
        return 0

    # game_state ticks are written in order, but make absolutely sure we're
    # sorted by timestamp_ms so binary search is correct.
    if any(gs_ts[i] > gs_ts[i + 1] for i in range(len(gs_ts) - 1)):
        order = sorted(range(len(gs_ts)), key=lambda i: gs_ts[i])
        gs_ts = [gs_ts[i] for i in order]
        gs_lines = [gs_lines[i] for i in order]

    # Anchor: action_camera frame[0].timestamp_ns ~= game_state[0].timestamp_ms
    # (both stamped at recording start). We use frame[0]'s timestamp_ns as the
    # zero point; absolute_ms(frame) = gs_ts[0] + (frame.timestamp_ns - first_t_ns) / 1e6
    first_t_ns: Optional[int] = None
    for fr in frames:
        t = _get_frame_t_ns(fr)
        if t is not None:
            first_t_ns = t
            break

    if first_t_ns is None:
        if verbose:
            print("  no timestamp_ns/t_ns/timestamp in frames — skip backfill")
        return 0

    anchor_ms = gs_ts[0]

    def nearest_gs(abs_t_ms: float) -> dict:
        """Binary-search nearest game_state tick to abs_t_ms (wall-clock ms)."""
        # bisect returns the insertion point; nearest neighbor is one of
        # (pos-1, pos). O(log N) per frame vs the previous O(N).
        pos = bisect.bisect_left(gs_ts, abs_t_ms)
        if pos == 0:
            return gs_lines[0]
        if pos >= len(gs_ts):
            return gs_lines[-1]
        before = gs_ts[pos - 1]
        after = gs_ts[pos]
        return gs_lines[pos - 1] if (abs_t_ms - before) <= (after - abs_t_ms) else gs_lines[pos]

    patched = 0
    for frame in frames:
        t_ns = _get_frame_t_ns(frame)
        if t_ns is None:
            continue
        abs_t_ms = anchor_ms + (t_ns - first_t_ns) / 1_000_000.0

        gs = nearest_gs(abs_t_ms)

        # Quaternion from yaw/pitch.
        # rc19.0.3 coord-system fix — L1 oracle:
        #   vendor/enrichment/docs/COORDINATE_SYSTEMS_GUIDE.md:55
        #   "Minecraft (Mineflayer): right-handed, Y-up ... Negate yaw to
        #    swap CW/CCW [to reach the buyer's left-handed frame]."
        # MC is right-handed; buyer spec is left-handed (X=right, Y=up,
        # Z=front). The handedness flip is carried by negating yaw — the
        # position axes need no negation because MC's +Z (south) already
        # maps to the buyer's +Z (front).
        yaw = gs.get("yaw_deg", 0.0)
        pitch = gs.get("pitch_deg", 0.0)
        quat = euler_to_quat_xyzw(-yaw, pitch)

        # Position — MC (x, y, z) maps directly to buyer (x, y, z); 1
        # block = 1 m so metric_scale = 1.0, no axis negation (per oracle).
        pos = [gs.get("x", 0.0), gs.get("y", 0.0), gs.get("z", 0.0)]

        # rc19.0.3 velocity unit fix — game_state.jsonl carries MC's raw
        # velocity in blocks/tick. PRD §3.2 wants m/s. MC runs at 20
        # ticks/sec and 1 block = 1 m, so blocks/tick × 20 = m/s. The
        # earlier "rc17.4 multiplied by 20" comment was aspirational —
        # the code never actually did it (confirmed: walking-speed
        # physics test measured ~0.2 m/s before this fix).
        vel = [
            gs.get("velocity_x", 0.0) * MC_TICKS_PER_SECOND,
            gs.get("velocity_y", 0.0) * MC_TICKS_PER_SECOND,
            gs.get("velocity_z", 0.0) * MC_TICKS_PER_SECOND,
        ]

        # Patch frame — these were NULL when recorder wrote the file
        frame["camera_rotation_quaternion"] = quat
        frame["player_rotation_quaternion"] = quat
        frame["rotation_quaternion"] = quat
        frame["camera_position"] = pos
        frame["player_position"] = pos
        frame["rotation_oula"] = [0.0, pitch, -yaw]  # [roll, pitch, yaw], yaw negated for buyer LH frame (rc19.0.3)
        frame["Follow_Offset"] = [0.0, 0.0, 0.0]  # first-person → camera at player
        # rc19: velocity fields per PRD §3.2 (camera_speed + player_speed both
        # in m/s; for first-person MC they're identical since camera is at
        # player position with no offset).
        frame["camera_speed"] = vel
        frame["player_speed"] = vel
        # scalar |v| in m/s for fields that historically held a magnitude
        speed_mag = (vel[0] ** 2 + vel[1] ** 2 + vel[2] ** 2) ** 0.5
        # Don't overwrite if the recorder already set a non-zero scalar speed;
        # else fill it from |velocity|. Keeps backwards compat with rc17.x.
        if frame.get("speed", 0.0) == 0.0:
            frame["speed"] = speed_mag

        # rc19.0.2.1: scene_name from game_state.dimension (PRD §3.2 wants
        # the scene tag per frame for cross-scene routing analysis). Empty
        # dimension → leave existing scene_name if any.
        dimension = gs.get("dimension")
        if dimension:
            frame["scene_name"] = dimension

        # rc19-c: PRD §3.2 requires `time` as ISO ms format and `fps` per
        # frame. The recorder writes `timestamp` (relative seconds) and
        # `timestamp_ns`; convert to absolute ISO using game_state's
        # timestamp_ms which gives us wall-clock anchor.
        if "time" not in frame or not isinstance(frame.get("time"), str):
            abs_dt = datetime.fromtimestamp(
                gs.get("timestamp_ms", 0) / 1000.0, tz=timezone.utc
            )
            frame["time"] = abs_dt.strftime("%Y-%m-%d %H:%M:%S.") + \
                            f"{abs_dt.microsecond // 1000:03d}"
        # rc19-c: per-frame fps field — PRD §3.2 wants 30.0 explicit, even
        # though recorder writes session-average fps in metadata.
        if frame.get("fps") is None or frame.get("fps") == 0:
            frame["fps"] = 30.0

        patched += 1

    # Write back
    ac_path.write_text(json.dumps(ac, indent=2))
    if verbose:
        print(f"  backfilled {patched} frames with quaternion + position + velocity")
    return patched


def compute_mouse_look_vector(session_dir: Path, verbose: bool = False) -> int:
    """MECE A24 / RBGA-A6 — replace screen-cursor mouse_x/y with cumulative
    look-vector normalized to [0,1].

    In Minecraft the cursor is locked to screen center, so raw mouse_x/y
    coordinates are useless for ML (all ~0.5). What matters is camera
    direction. We accumulate mouse_dx/dy deltas with Minecraft default
    sensitivity (0.15° per count), wrap yaw to [0,360), clamp pitch to
    [-90, 90], then normalize:

        mouse_x = (yaw_deg mod 360) / 360        # [0,1] wraps cleanly
        mouse_y = (pitch_deg + 90) / 180         # 0 = looking straight down, 1 = up

    Idempotent: looks for sentinel ``look_vector_applied: true`` at root of
    action_camera.json (or first frame's marker). Re-running is a no-op.

    Returns count of frames rewritten. Backs original up to .bak first.
    """
    ac_path = session_dir / "action_camera.json"
    if not ac_path.exists():
        if verbose:
            print("  action_camera.json missing — skip look-vector")
        return 0

    try:
        data = json.loads(ac_path.read_text())
    except json.JSONDecodeError as e:
        if verbose:
            print(f"  action_camera.json parse error: {e}")
        return 0

    if isinstance(data, dict) and "frames" in data:
        frames = data["frames"]
        is_wrapped = True
        if data.get("look_vector_applied"):
            if verbose:
                print("  look-vector already applied — skip (idempotent)")
            return 0
    elif isinstance(data, list):
        frames = data
        is_wrapped = False
        if frames and isinstance(frames[0], dict) and frames[0].get("_look_vector_applied"):
            if verbose:
                print("  look-vector already applied — skip (idempotent)")
            return 0
    else:
        if verbose:
            print("  action_camera.json unexpected structure — skip look-vector")
        return 0

    if not frames:
        return 0

    # Backup before rewriting (matches heal-tool convention).
    bak = ac_path.with_suffix(".json.bak")
    if not bak.exists():
        bak.write_text(ac_path.read_text())

    # Minecraft default mouse sensitivity in degrees per raw mouse count.
    # 0.15 is the empirical mid-slider value; sensitivity setting scales
    # this 1×–4×. Without the operator's exact slider we use the default.
    MC_DEG_PER_COUNT = 0.15

    yaw_deg = 0.0
    pitch_deg = 0.0
    rewritten = 0
    for f in frames:
        if not isinstance(f, dict):
            continue
        dx = f.get("mouse_dx")
        dy = f.get("mouse_dy")
        if isinstance(dx, (int, float)):
            yaw_deg += float(dx) * MC_DEG_PER_COUNT
        if isinstance(dy, (int, float)):
            # MC convention: positive dy means looking down (mouse moved
            # forward physically = camera tilts up in some games but DOWN
            # in MC's stock camera). Sign matches the PRD coord doc.
            pitch_deg += float(dy) * MC_DEG_PER_COUNT
            if pitch_deg > 90.0:
                pitch_deg = 90.0
            elif pitch_deg < -90.0:
                pitch_deg = -90.0
        yaw_wrapped = yaw_deg % 360.0
        if yaw_wrapped < 0:
            yaw_wrapped += 360.0
        f["mouse_x"] = yaw_wrapped / 360.0
        f["mouse_y"] = (pitch_deg + 90.0) / 180.0
        rewritten += 1

    # Idempotency sentinel: drop on list-form first frame OR dict-form root.
    if is_wrapped:
        data["look_vector_applied"] = True
    elif frames:
        frames[0]["_look_vector_applied"] = True

    ac_path.write_text(json.dumps(data, indent=2))
    if verbose:
        print(f"  look-vector applied to {rewritten} frames (final yaw={yaw_deg:.1f}° "
              f"pitch={pitch_deg:.1f}°), backup at {bak.name}")
    return rewritten


# ---------------------------------------------------------------------------
# Step 3 — generate gameinfo.xlsx
# ---------------------------------------------------------------------------

def generate_gameinfo(session_dir: Path, repo_root: Path, verbose: bool = False) -> bool:
    """Invoke vendor/recorder/scripts/generate_gameinfo.py."""
    out = session_dir / "gameinfo.xlsx"
    if out.exists() and out.stat().st_size > 0:
        if verbose:
            print(f"  gameinfo.xlsx already present ({out.stat().st_size} bytes) — skip generate")
        # rc19.0.3: still run the coord/gravity augmentation — it's
        # idempotent, and a pre-existing xlsx from an older finalize run
        # won't have the rc19.0.3 fields.
        _augment_gameinfo_coords(out, verbose=verbose)
        return True

    script = repo_root / "vendor" / "recorder" / "scripts" / "generate_gameinfo.py"
    if not script.exists():
        if verbose:
            print(f"  generate_gameinfo.py not found at {script} — skip")
        return False

    try:
        r = subprocess.run(
            [sys.executable, str(script), str(session_dir)],
            capture_output=True, text=True, timeout=60, cwd=session_dir
        )
        ok = r.returncode == 0 and out.exists()
        if verbose:
            print(f"  generate_gameinfo.py: exit={r.returncode}  xlsx_exists={out.exists()}")
            if not ok and r.stderr.strip():
                print(f"    stderr: {r.stderr.strip()[:200]}")
        if ok:
            # rc19.0.3: append the coord-system / units / gravity fields the
            # vendor script doesn't know about. Done here (not in the
            # submodule script) to keep the submodule untouched.
            _augment_gameinfo_coords(out, verbose=verbose)
        return ok
    except Exception as e:
        if verbose:
            print(f"  generate_gameinfo.py exception: {e}")
        return False


def _augment_gameinfo_coords(xlsx_path: Path, verbose: bool = False) -> None:
    """Append rc19.0.3 coord-system / units / gravity rows to gameinfo.xlsx.

    The vendor generate_gameinfo.py emits the PRD §3.3 14 fields but not
    the physics/coordinate metadata the buyer's training pipeline needs.
    We append key-value rows (idempotent — skipped if already present).

    Constants come from the L1 oracle
    (vendor/enrichment/docs/COORDINATE_SYSTEMS_GUIDE.md:55).
    """
    try:
        import openpyxl  # noqa: PLC0415
    except ImportError:
        if verbose:
            print("  _augment_gameinfo_coords: openpyxl missing — skip augmentation")
        return

    extra_rows = [
        ("world_gravity_mps2", MC_WORLD_GRAVITY_MPS2),
        ("coord_system", "left_handed_X_right_Y_up_Z_forward"),
        ("velocity_unit", "m/s"),
        ("mc_blocks_to_meters", 1.0),
        ("mc_ticks_per_second", MC_TICKS_PER_SECOND),
    ]
    try:
        wb = openpyxl.load_workbook(xlsx_path)
        ws = wb.active
        existing = set()
        for row in ws.iter_rows(values_only=True):
            for cell in row:
                if cell is not None:
                    existing.add(str(cell).strip().lower())
        appended = 0
        for key, val in extra_rows:
            if key.lower() in existing:
                continue
            ws.append([key, val])
            appended += 1
        if appended:
            wb.save(xlsx_path)
        wb.close()
        if verbose:
            print(f"  _augment_gameinfo_coords: appended {appended} coord/units/gravity rows")
    except Exception as e:
        if verbose:
            print(f"  _augment_gameinfo_coords exception: {e}")


# ---------------------------------------------------------------------------
# Step 4 — generate depth/*.exr
# ---------------------------------------------------------------------------

def generate_depth_exr(session_dir: Path, verbose: bool = False) -> bool:
    """Invoke bin/depth_exr_writer.py against the session.

    The tool's CLI signature is: `depth_exr_writer.py <session_dir>` —
    it finds recording.mp4 inside session_dir and writes depth/*.exr
    next to it. Exit 0 = full success; 1 = partial (some frame errors);
    2 = missing dep / model / mp4. Both 0 and 1 produce usable output.
    """
    depth_dir = session_dir / "depth"
    out = session_dir / "depth" / "000000.exr"
    if out.exists() and out.stat().st_size > 0:
        if verbose:
            print(f"  depth/*.exr already present — skip generate")
        return True

    script = Path(__file__).parent / "depth_exr_writer.py"
    if not script.exists():
        if verbose:
            print(f"  depth_exr_writer.py not found — skip")
        return False

    try:
        # Depth inference is the slowest finalize step. 30-min timeout covers
        # CPU-only fallback (~60 min worst case; we hard-stop at 30 to keep
        # finalize from blocking shipment forever — partial output is
        # preferable).
        cmd = [sys.executable, str(script), str(session_dir)]
        if verbose:
            cmd.append("--verbose")
        r = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=1800,
        )
        # Exit 0 = full success; 1 = partial (some frame errors but kept).
        # Both produce usable output. 2 = missing dep / model / mp4.
        ok = r.returncode in (0, 1) and depth_dir.exists() and any(depth_dir.glob("*.exr"))
        if verbose:
            print(f"  depth_exr_writer.py: exit={r.returncode}  ok={ok}")
            if r.stderr.strip():
                # Surface the writer's own log lines (it uses logging which
                # routes to stderr by default).
                tail = "\n    ".join(r.stderr.strip().splitlines()[-10:])
                print(f"    stderr tail:\n    {tail}")
        return ok
    except subprocess.TimeoutExpired:
        if verbose:
            print("  depth_exr_writer.py timed out after 30 min — partial output may exist")
        # Check if we got partial output despite the timeout
        return depth_dir.exists() and any(depth_dir.glob("*.exr"))
    except Exception as e:
        if verbose:
            print(f"  depth_exr_writer.py exception: {e}")
        return False


# ---------------------------------------------------------------------------
# Step 5 — generate audio_check.json
# ---------------------------------------------------------------------------

def generate_audio_check(session_dir: Path, verbose: bool = False) -> bool:
    """Invoke bin/audio_continuity_check.py against the session.

    The tool's CLI signature is: `audio_continuity_check.py <session_dir>` —
    it finds recording.mp4 inside session_dir and writes audio_check.json
    next to it. Exit code 0 = pass, 1 = silence-gap-fail, 2 = error (missing
    ffprobe, no audio stream). All three produce audio_check.json (lint #38
    only requires the file to exist + be readable JSON).
    """
    out = session_dir / "audio_check.json"
    if out.exists() and out.stat().st_size > 0:
        if verbose:
            print("  audio_check.json already present — skip generate")
        return True

    script = Path(__file__).parent / "audio_continuity_check.py"
    if not script.exists():
        if verbose:
            print("  audio_continuity_check.py not found — skip")
        return False

    try:
        r = subprocess.run(
            [sys.executable, str(script), str(session_dir)],
            capture_output=True, text=True, timeout=180, cwd=session_dir
        )
        # exit 0 = pass, 1 = silence-gap-fail (still wrote JSON), 2 = error.
        # For lint #38 we just need the file to exist. Treat 0 + 1 as OK.
        ok = r.returncode in (0, 1) and out.exists()
        if verbose:
            print(f"  audio_continuity_check.py: exit={r.returncode}  json_exists={out.exists()}")
            if not ok and r.stderr.strip():
                print(f"    stderr: {r.stderr.strip()[:200]}")
        return ok
    except Exception as e:
        if verbose:
            print(f"  audio_continuity_check.py exception: {e}")
        return False


def extract_audio_flac(session_dir: Path, verbose: bool = False) -> bool:
    """MECE U2 — extract recording.mp4 audio track to a separate audio.flac.

    PRD §3 / RBGA-B2 wants audio available as an independent file (not just
    embedded in mp4) so buyer-side QC + model training can ingest audio
    without re-muxing the entire video.

    Idempotent: skip if audio.flac already exists and is non-empty. No-ops
    if recording.mp4 is missing. Returns True on success / skip; False on
    real failure. Bug-fix vs. prior absence: this step was 0% implemented
    until 2026-05-15; closes MECE U2 / RBGA-B2.
    """
    out = session_dir / "audio.flac"
    if out.exists() and out.stat().st_size > 0:
        if verbose:
            print(f"  audio.flac already present ({out.stat().st_size} bytes) — skip")
        return True

    mp4 = session_dir / "recording.mp4"
    if not mp4.exists():
        if verbose:
            print("  recording.mp4 missing — skip audio.flac extract")
        return False

    # -vn: no video.  -c:a flac: re-encode to lossless flac.
    # -compression_level 5 is ffmpeg default; keeps extract under ~30 sec
    # on a 5-min recording. We do NOT use -c:a copy because mp4 carries
    # AAC and the wire format we want IS flac (lossless).
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(mp4),
             "-vn", "-c:a", "flac", "-compression_level", "5", str(out)],
            capture_output=True, text=True, timeout=120
        )
        ok = r.returncode == 0 and out.exists() and out.stat().st_size > 0
        if verbose:
            size_mb = out.stat().st_size / 1e6 if out.exists() else 0
            print(f"  audio.flac: exit={r.returncode}  size={size_mb:.1f}MB")
            if not ok and r.stderr.strip():
                print(f"    stderr: {r.stderr.strip()[:200]}")
        return ok
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        if verbose:
            print(f"  ffmpeg unavailable or timed out: {e}")
        return False
    except Exception as e:
        if verbose:
            print(f"  extract_audio_flac exception: {e}")
        return False


# ---------------------------------------------------------------------------
# Step 6 — generate input_latency.json (post-hoc estimation)
# ---------------------------------------------------------------------------

def _load_frames_for_latency(session_dir: Path) -> Tuple[List[Tuple[int, float]], Optional[int]]:
    """Load frames.jsonl and return list of (frame_idx, timestamp_ms) and first_t_ns.

    timestamp_ms is computed from t_ns relative to first frame.
    """
    frames_path = session_dir / "frames.jsonl"
    if not frames_path.exists():
        return [], None

    frames = []
    first_t_ns = None

    with open(frames_path) as f:
        for line in f:
            if not line.strip():
                continue
            obj = json.loads(line)
            idx = obj.get("idx")
            t_ns = obj.get("t_ns")
            if idx is None or t_ns is None:
                continue
            if first_t_ns is None:
                first_t_ns = t_ns
            ms_offset = (t_ns - first_t_ns) / 1_000_000.0
            frames.append((idx, ms_offset))

    frames.sort(key=lambda x: x[1])  # sort by timestamp
    return frames, first_t_ns


def _load_inputs_for_latency(session_dir: Path) -> List[Tuple[float, str, any]]:
    """Load inputs.jsonl and return list of (timestamp_s, event_type, event_args).

    Only returns KEYBOARD events (not mouse moves or other types).
    """
    inputs_path = session_dir / "inputs.jsonl"
    if not inputs_path.exists():
        return []

    events = []
    with open(inputs_path) as f:
        for line in f:
            if not line.strip():
                continue
            obj = json.loads(line)
            ts = obj.get("timestamp")
            evt_type = obj.get("event_type")
            evt_args = obj.get("event_args", [])
            if ts is None or evt_type is None:
                continue
            # Only care about KEYBOARD events (key press, not release)
            # event_args format: [key_code, is_pressed] where is_pressed=true means press
            if evt_type == "KEYBOARD" and len(evt_args) >= 2 and evt_args[1] is True:
                events.append((ts, evt_type, evt_args))

    events.sort(key=lambda x: x[0])  # sort by timestamp
    return events


def generate_input_latency(session_dir: Path, verbose: bool = False) -> bool:
    """Compute input-to-frame latency via post-hoc estimation.

    For each KEYBOARD event in inputs.jsonl, find the first frame that
    appears after it and compute the delta. Return median of all deltas.

    Output format (lint #39 compatible):
    {
        "trials": <count>,
        "median_ms": <value>,
        "p50_ms": <value>,
        "p95_ms": <value>,
        "p99_ms": <value>,
        "mean_ms": <value>,
        "std_ms": <value>,
        "method": "posthoc_inputs_vs_frames",
        "measured_at": "<ISO timestamp>"
    }
    """
    out = session_dir / "input_latency.json"
    if out.exists() and out.stat().st_size > 0:
        if verbose:
            print("  input_latency.json already present — skip generate")
        return True

    # Load frames
    frames, first_t_ns = _load_frames_for_latency(session_dir)
    if not frames or first_t_ns is None:
        if verbose:
            print("  frames.jsonl missing or empty — cannot compute latency")
        return False

    # Load inputs
    inputs = _load_inputs_for_latency(session_dir)
    if not inputs:
        if verbose:
            print("  No KEYBOARD events in inputs.jsonl — cannot compute latency")
        return False

    # For each keyboard event, find the first frame after it
    # frames is list of (frame_idx, ms_offset_from_first_frame)
    # inputs is list of (timestamp_s, event_type, event_args)
    # We need to align timestamps: inputs are absolute, frames are relative to first frame

    # Get absolute timestamp of first frame from game_state or metadata
    # Actually, frames.jsonl t_ns is relative to recording start, not absolute.
    # We need to find the absolute start time from metadata or game_state.

    # Try to get absolute start time from metadata.json
    metadata_path = session_dir / "metadata.json"
    abs_start_ms = None
    if metadata_path.exists():
        try:
            meta = json.loads(metadata_path.read_text())
            # metadata may have 'timestamp' or 'start_time'
            abs_start_ms = meta.get("timestamp") * 1000 if meta.get("timestamp") else None
        except Exception:
            pass

    # Fallback: try game_state.jsonl
    if abs_start_ms is None:
        gs_path = session_dir / "game_state.jsonl"
        if gs_path.exists():
            try:
                with open(gs_path) as f:
                    first_line = f.readline()
                    if first_line:
                        gs = json.loads(first_line)
                        abs_start_ms = gs.get("timestamp_ms")
            except Exception:
                pass

    if abs_start_ms is None:
        if verbose:
            print("  Cannot determine absolute start time — skipping latency measurement")
        return False

    # Convert frame ms_offsets to absolute timestamps
    frame_abs_ms = [(idx, abs_start_ms + ms_offset) for idx, ms_offset in frames]

    # Compute latencies
    latencies = []
    for inp_ts_s, _, _ in inputs:
        inp_ts_ms = inp_ts_s * 1000.0  # convert to ms

        # Find first frame after input
        for frame_idx, frame_ts_ms in frame_abs_ms:
            if frame_ts_ms > inp_ts_ms:
                latency_ms = frame_ts_ms - inp_ts_ms
                # Sanity check: latency should be positive and reasonable (< 500ms)
                if 0 < latency_ms < 500:
                    latencies.append(latency_ms)
                break

    if not latencies:
        if verbose:
            print("  No valid latency measurements found")
        return False

    # Compute statistics
    latencies_sorted = sorted(latencies)
    n = len(latencies_sorted)

    median_ms = statistics.median(latencies)
    mean_ms = statistics.mean(latencies)
    std_ms = statistics.stdev(latencies) if n > 1 else 0.0

    # Percentiles
    p50_idx = int(n * 0.50)
    p95_idx = int(n * 0.95)
    p99_idx = int(n * 0.99)

    p50_ms = latencies_sorted[p50_idx] if p50_idx < n else latencies_sorted[-1]
    p95_ms = latencies_sorted[p95_idx] if p95_idx < n else latencies_sorted[-1]
    p99_ms = latencies_sorted[p99_idx] if p99_idx < n else latencies_sorted[-1]

    result = {
        "trials": n,
        "median_ms": round(median_ms, 1),
        "p50_ms": round(p50_ms, 1),
        "p95_ms": round(p95_ms, 1),
        "p99_ms": round(p99_ms, 1),
        "mean_ms": round(mean_ms, 1),
        "std_ms": round(std_ms, 1),
        "method": "posthoc_inputs_vs_frames",
        "measured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    # Write output
    out.write_text(json.dumps(result, indent=2))

    if verbose:
        print(f"  Computed latency: median={median_ms:.1f}ms, mean={mean_ms:.1f}ms, n={n}")
        if median_ms > SUSPICIOUS_LATENCY_MS:
            print(f"  WARN: median latency {median_ms:.1f}ms exceeds {SUSPICIOUS_LATENCY_MS}ms — suspicious!")

    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Finalize a recording session for PRD §3 compliance")
    parser.add_argument("session_dir", type=Path, help="Path to recording session directory")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Repo root (so we can find vendor/recorder/scripts/)",
    )
    parser.add_argument(
        "--no-depth",
        action="store_true",
        help="Skip depth EXR generation (PRD §3.4) — fast finalize for "
             "development/testing only. Lint #15/#16/#24 will fail without it.",
    )
    args = parser.parse_args(argv)

    sd = args.session_dir
    if not sd.exists() or not sd.is_dir():
        print(f"Error: session_dir not found / not a dir: {sd}", file=sys.stderr)
        return 1

    print(f"Finalizing session: {sd}")
    t0 = time.time()

    print("\n[1/6] Syncing game_state.jsonl from mc-mod fallback path...")
    sync_ok = sync_game_state(sd, verbose=args.verbose)

    print("\n[2/6] Backfilling action_camera.json (quaternion + position)...")
    backfilled = backfill_action_camera(sd, verbose=args.verbose)
    # MECE A24 / RBGA-A6 — replace screen-cursor mouse_x/y with cumulative
    # look-vector accumulated from mouse_dx/dy. Idempotent.
    look_rewritten = compute_mouse_look_vector(sd, verbose=args.verbose)

    print("\n[3/6] Generating gameinfo.xlsx...")
    gi_ok = generate_gameinfo(sd, args.repo_root, verbose=args.verbose)

    # Step 4: depth EXR (heavy — 4-5 min on GPU, ~60 min on CPU). Allow
    # skip via --no-depth flag OR OYSTER_SKIP_DEPTH=1 env var so testers
    # iterating finalize don't pay the inference cost on every cycle.
    skip_depth_env = os.environ.get("OYSTER_SKIP_DEPTH") == "1"
    if args.no_depth or skip_depth_env:
        reason = "--no-depth flag" if args.no_depth else "OYSTER_SKIP_DEPTH=1 env"
        print(f"\n[4/6] Generating depth/*.exr ... SKIPPED ({reason})")
        print("  WARN lint criteria #15, #16, #24 will FAIL without depth EXR output")
        depth_ok = False
    else:
        print("\n[4/6] Generating depth/*.exr (PRD §3.4, 6 fps × 1800 frames — heavy)...")
        depth_ok = generate_depth_exr(sd, verbose=args.verbose)
        if not depth_ok:
            print("  WARN depth EXR generation failed — lint #15, #16, #24 will FAIL")

    print("\n[5/6] Generating audio_check.json + audio.flac (MECE U2)...")
    ac_ok = generate_audio_check(sd, verbose=args.verbose)
    # MECE U2 / RBGA-B2 — extract audio track as standalone flac.
    flac_ok = extract_audio_flac(sd, verbose=args.verbose)
    if not flac_ok:
        print("  WARN audio.flac extract failed — MECE U2 will FAIL")

    # Step 6: input latency measurement (post-hoc from inputs.jsonl + frames.jsonl)
    skip_latency_env = os.environ.get("OYSTER_SKIP_LATENCY_MEASUREMENT") == "1"
    if skip_latency_env:
        print("\n[6/6] Generating input_latency.json ... SKIPPED (OYSTER_SKIP_LATENCY_MEASUREMENT=1)")
        lat_ok = False
    else:
        print("\n[6/6] Generating input_latency.json (post-hoc estimation)...")
        lat_ok = generate_input_latency(sd, verbose=args.verbose)
        if not lat_ok:
            print("  WARN input_latency.json generation failed — lint #39 will FAIL")

    dt = time.time() - t0
    print(
        f"\nDone in {dt:.1f}s — "
        f"sync={'✓' if sync_ok else '✗'}  "
        f"backfill={backfilled}f  "
        f"gameinfo={'✓' if gi_ok else '✗'}  "
        f"depth={'✓' if depth_ok else ('SKIP' if (args.no_depth or skip_depth_env) else '✗')}  "
        f"audio={'✓' if ac_ok else '✗'}  "
        f"latency={'✓' if lat_ok else ('SKIP' if skip_latency_env else '✗')}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
