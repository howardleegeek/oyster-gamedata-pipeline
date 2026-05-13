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

After finalize_session.py runs, a recording session has every PRD §3
deliverable.

Lint score impact on a fresh recording: ≥7 criteria unblocked
(#13, #14, #15, #16, #24-partial, #38, plus #24 fully when depth is
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
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Constants — mc-mod fallback path per
# mc-mod/src/main/java/world/oyster/recorder/SessionDir.java:67
# ---------------------------------------------------------------------------

MC_MOD_FALLBACK = Path.home() / "Documents" / "OysterClips" / "active_session" / "game_state.jsonl"


# ---------------------------------------------------------------------------
# Euler → quaternion (xyzw normalized)
# ---------------------------------------------------------------------------

def euler_to_quat_xyzw(yaw_deg: float, pitch_deg: float, roll_deg: float = 0.0) -> List[float]:
    """Convert Euler (yaw, pitch, roll) degrees → quaternion [x, y, z, w].

    Uses ZYX intrinsic order (yaw around Y, pitch around X, roll around Z)
    consistent with Minecraft's camera convention. Result is normalized
    (|q| = 1).
    """
    half_yaw = math.radians(yaw_deg) / 2.0
    half_pitch = math.radians(pitch_deg) / 2.0
    half_roll = math.radians(roll_deg) / 2.0

    cy = math.cos(half_yaw); sy = math.sin(half_yaw)
    cp = math.cos(half_pitch); sp = math.sin(half_pitch)
    cr = math.cos(half_roll); sr = math.sin(half_roll)

    qw = cr * cp * cy + sr * sp * sy
    qx = sr * cp * cy - cr * sp * sy
    qy = cr * sp * cy + sr * cp * sy
    qz = cr * cp * sy - sr * sp * cy

    # Normalize defensively against floating-point drift
    n = math.sqrt(qx * qx + qy * qy + qz * qz + qw * qw)
    if n > 0:
        qx /= n; qy /= n; qz /= n; qw /= n

    return [qx, qy, qz, qw]


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

def backfill_action_camera(session_dir: Path, verbose: bool = False) -> int:
    """Patch action_camera.json frames with quaternion + position from game_state.

    Returns count of frames patched.
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
        wrapped = True
    elif isinstance(ac, list):
        frames = ac
        wrapped = False
    else:
        if verbose:
            print("  action_camera.json unexpected format — skip")
        return 0

    gs_lines = [json.loads(line) for line in gs_path.read_text().splitlines() if line.strip()]
    if not gs_lines:
        if verbose:
            print("  game_state.jsonl empty — skip")
        return 0

    # Sort game_state by timestamp_ms (defensive — should already be sorted)
    gs_lines.sort(key=lambda s: s.get("timestamp_ms", 0))
    gs_start_ms = gs_lines[0]["timestamp_ms"]

    # Build sorted-ts index for binary search
    gs_ts = [s["timestamp_ms"] for s in gs_lines]

    import bisect
    patched = 0
    for frame in frames:
        rel_t_sec = frame.get("timestamp", 0)
        abs_t_ms = gs_start_ms + int(rel_t_sec * 1000)

        # Binary search closest game_state sample
        idx = bisect.bisect_left(gs_ts, abs_t_ms)
        # Pick whichever neighbour is closer
        candidates = []
        if idx > 0:
            candidates.append(idx - 1)
        if idx < len(gs_ts):
            candidates.append(idx)
        if not candidates:
            continue
        best_idx = min(candidates, key=lambda i: abs(gs_ts[i] - abs_t_ms))
        gs = gs_lines[best_idx]

        # Quaternion from yaw/pitch
        yaw = gs.get("yaw_deg", 0.0)
        pitch = gs.get("pitch_deg", 0.0)
        quat = euler_to_quat_xyzw(yaw, pitch)

        # Position
        pos = [gs.get("x", 0.0), gs.get("y", 0.0), gs.get("z", 0.0)]

        # rc19: velocity from game_state (m/s per rc17.4-velocity commit that
        # multiplied MC's blocks/tick by 20). PRD §3.2 requires camera_speed +
        # player_speed; without this they stay 0-stuck and lint §3.2 14/20
        # passes are capped at what schema-only achieves.
        vel = [
            gs.get("velocity_x", 0.0),
            gs.get("velocity_y", 0.0),
            gs.get("velocity_z", 0.0),
        ]

        # Patch frame — these were NULL when recorder wrote the file
        frame["camera_rotation_quaternion"] = quat
        frame["player_rotation_quaternion"] = quat
        frame["rotation_quaternion"] = quat
        frame["camera_position"] = pos
        frame["player_position"] = pos
        frame["rotation_oula"] = [0.0, pitch, yaw]  # [roll, pitch, yaw] in MC convention
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
        patched += 1

    # Write back
    payload = {"frames": frames} if wrapped else frames
    ac_path.write_text(json.dumps(payload, indent=2))

    # rc19: declare quaternion_order in metadata.json so lint #13 can do a
    # contract check instead of relying on the unreliable rest-state
    # heuristic (which mis-classifies large-rotation game data — see
    # lint_v3_prd_grounded.py _check_quaternion docstring for analysis).
    # Also backfill scene_name from game_state dimension field (which is
    # always present per mc-mod's per-tick emission).
    metadata_path = session_dir / "metadata.json"
    if metadata_path.exists() and patched > 0:
        try:
            with open(metadata_path) as f:
                meta = json.load(f)
            changed = False
            if meta.get("quaternion_order") != "xyzw":
                meta["quaternion_order"] = "xyzw"
                changed = True
                if verbose:
                    print("  declared quaternion_order=xyzw in metadata.json")
            # Derive scene_name from majority game_state dimension
            dims: Dict[str, int] = {}
            for s in gs_lines:
                dim = s.get("dimension")
                if isinstance(dim, str) and dim:
                    # "minecraft:overworld" → "overworld"
                    short = dim.split(":", 1)[-1] if ":" in dim else dim
                    dims[short] = dims.get(short, 0) + 1
            if dims:
                top_scene = max(dims.items(), key=lambda kv: kv[1])[0]
                if meta.get("scene_name") != top_scene:
                    meta["scene_name"] = top_scene
                    changed = True
                    if verbose:
                        print(f"  backfilled scene_name={top_scene} (from game_state dimension; {len(dims)} unique)")
            if changed:
                with open(metadata_path, "w") as f:
                    json.dump(meta, f, indent=2)
        except (json.JSONDecodeError, OSError) as e:
            if verbose:
                print(f"  WARN could not update metadata.json: {e}")

    if verbose:
        print(f"  backfilled {patched} action_camera frames with quaternion + position from {len(gs_lines)} game_state samples")
    return patched


# ---------------------------------------------------------------------------
# Step 3 — generate gameinfo.xlsx via the recorder's script
# ---------------------------------------------------------------------------

def generate_gameinfo(session_dir: Path, repo_root: Path, verbose: bool = False) -> bool:
    """Invoke vendor/recorder/scripts/generate_gameinfo.py."""
    target = session_dir / "gameinfo.xlsx"
    if target.exists() and target.stat().st_size > 100:
        if verbose:
            print(f"  gameinfo.xlsx already present ({target.stat().st_size} bytes) — skip generate")
        return True

    script = repo_root / "vendor" / "recorder" / "scripts" / "generate_gameinfo.py"
    if not script.exists():
        # Fallback: look for it in the same dir as this script (shipped alongside in installer)
        script = Path(__file__).parent / "generate_gameinfo.py"
        if not script.exists():
            if verbose:
                print("  generate_gameinfo.py not found — skip")
            return False

    try:
        r = subprocess.run(
            [sys.executable, str(script), str(session_dir)],
            capture_output=True, text=True, timeout=60
        )
        ok = r.returncode == 0 and target.exists()
        if verbose:
            print(f"  generate_gameinfo.py: {'OK' if ok else 'FAIL'} (exit {r.returncode})")
            if r.stderr.strip():
                print(f"    stderr: {r.stderr.strip()[:200]}")
        return ok
    except Exception as e:
        if verbose:
            print(f"  generate_gameinfo.py exception: {e}")
        return False


# ---------------------------------------------------------------------------
# Step 4 — generate depth/*.exr (PRD §3.4)
# ---------------------------------------------------------------------------


def generate_depth_exr(session_dir: Path, verbose: bool = False) -> bool:
    """Invoke bin/depth_exr_writer.py against the session.

    rc19: 6 fps sampling of recording.mp4 through DepthAnything V2 Small ONNX,
    writing 1800 single-channel float32 EXR files per PRD §3.4. Heavy step —
    typical runtime 4-5 min on AMD 780M iGPU, ~60 min on CPU fallback.

    Returns True if the writer reports success (≥1 EXR file written) OR if
    the depth dir already has ≥1800 files (idempotent re-run). False on
    decode/init failure — caller logs a WARN since lint #15/#16/#24 depend
    on this output.
    """
    depth_dir = session_dir / "depth"
    # Idempotent: 1800 .exr present → already done, no need to re-run.
    if depth_dir.exists() and sum(1 for _ in depth_dir.glob("*.exr")) >= 1800:
        if verbose:
            print("  depth/ already has ≥1800 .exr files — skip generate")
        return True

    script = Path(__file__).parent / "depth_exr_writer.py"
    if not script.exists():
        if verbose:
            print("  depth_exr_writer.py not found — skip")
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

    print("\n[1/5] Syncing game_state.jsonl from mc-mod fallback path...")
    sync_ok = sync_game_state(sd, verbose=args.verbose)

    print("\n[2/5] Backfilling action_camera.json (quaternion + position)...")
    backfilled = backfill_action_camera(sd, verbose=args.verbose)

    print("\n[3/5] Generating gameinfo.xlsx...")
    gi_ok = generate_gameinfo(sd, args.repo_root, verbose=args.verbose)

    # Step 4: depth EXR (heavy — 4-5 min on GPU, ~60 min on CPU). Allow
    # skip via --no-depth flag OR OYSTER_SKIP_DEPTH=1 env var so testers
    # iterating finalize don't pay the inference cost on every cycle.
    skip_depth_env = os.environ.get("OYSTER_SKIP_DEPTH") == "1"
    if args.no_depth or skip_depth_env:
        reason = "--no-depth flag" if args.no_depth else "OYSTER_SKIP_DEPTH=1 env"
        print(f"\n[4/5] Generating depth/*.exr ... SKIPPED ({reason})")
        print("  WARN lint criteria #15, #16, #24 will FAIL without depth EXR output")
        depth_ok = False
    else:
        print("\n[4/5] Generating depth/*.exr (PRD §3.4, 6 fps × 1800 frames — heavy)...")
        depth_ok = generate_depth_exr(sd, verbose=args.verbose)
        if not depth_ok:
            print("  WARN depth EXR generation failed — lint #15, #16, #24 will FAIL")

    print("\n[5/5] Generating audio_check.json...")
    ac_ok = generate_audio_check(sd, verbose=args.verbose)

    dt = time.time() - t0
    print(
        f"\nDone in {dt:.1f}s — "
        f"sync={'✓' if sync_ok else '✗'}  "
        f"backfill={backfilled}f  "
        f"gameinfo={'✓' if gi_ok else '✗'}  "
        f"depth={'✓' if depth_ok else ('SKIP' if (args.no_depth or skip_depth_env) else '✗')}  "
        f"audio={'✓' if ac_ok else '✗'}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
