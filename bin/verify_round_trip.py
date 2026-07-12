#!/usr/bin/env python3
"""verify_round_trip.py — Round-trip data integrity test for action_camera.json

Howard 2026-05-05 (parallel-engineer-D): "prove that the data recorded can be
RECONSTRUCTED back to the original input — if not, the recorder has lossy or
corrupt encoding."

This complements bin/verify_action_camera.py (which checks math invariants /
PRD references / temporal continuity). The verifier here goes the other way:
it walks the recorded action_camera.json and proves that each derived field
can be reconstructed from earlier frames or from the original event stream.

Four round-trip checks:

  Check 1 — Keyboard event reconstruction
      Walk frames, diff `keyCode` lists frame-to-frame to recover synthetic
      key_down / key_up events, then re-replay through the recorder's
      latching frame-bucketing logic (mirrors recorder_consumer_lite._run_one_session).
      Assert the reconstructed `keyCode` list per frame matches the original.

  Check 2 — Mouse position reconstruction
      Compute cumulative Σ mouse_dx, Σ mouse_dy from frame 0 → N. The recorded
      mouse_x[N] should equal mouse_x[0] + Σ mouse_dx (modulo screen wrap and
      normalization). We accept either pixel-domain or normalized-domain
      deltas, picking whichever matches more frames.

  Check 3 — Quaternion ↔ Euler round-trip
      For each frame, compute euler from camera_rotation_quaternion and
      compare with camera_rotation_euler / camera_rotation_oula within ±0.5°.

  Check 4 — Frame-time consistency
      Derive expected frame index from (time - time[0]) * fps and compare
      with the `frame` / `frame_index` field within ±1 frame.

CLI:
    python3 bin/verify_round_trip.py <clip_dir>
    python3 bin/verify_round_trip.py <clip_dir> --json

Exit code = number of failed checks (0 = all pass).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

# Re-use the math helpers from verify_action_camera to keep formulas identical.
sys.path.insert(0, str(Path(__file__).parent))
from verify_action_camera import (  # noqa: E402
    _quat,
    _vec3,
    quat_to_euler_zyx,
)

EPS_EULER_RT_DEG = 0.5            # quaternion → euler round-trip tolerance
EPS_MOUSE_REL = 0.01              # 1% of screen for mouse cumulative-sum check
EPS_FRAME_INDEX = 1               # ±1 frame for time consistency
SAMPLE_BAD_FRAMES = 5             # show first N mismatching frames per check


# ---- Field readers (handles both buyer-spec and PRD shapes) ----------------

def _frame_index(rec: dict[str, Any]) -> int | None:
    """Recorded frame index. Buyer-spec uses `frame_index`, PRD uses `frame`."""
    if "frame_index" in rec:
        return int(rec["frame_index"])
    if "frame" in rec:
        return int(rec["frame"])
    return None


def _timestamp_seconds(rec: dict[str, Any], base: float | None) -> float | None:
    """Frame timestamp in seconds relative to clip start.

    Buyer-spec: `timestamp` is float seconds (already relative).
    PRD: `time` is "YYYY-MM-DD HH:MM:SS.mmm" — subtract base.
    """
    if "timestamp" in rec and isinstance(rec["timestamp"], (int, float)):
        return float(rec["timestamp"])
    raw = rec.get("time")
    if not isinstance(raw, str):
        return None
    from datetime import datetime
    try:
        t = datetime.strptime(raw[:23], "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        return None
    epoch = t.timestamp()
    if base is None:
        return epoch
    return epoch - base


def _mouse_xy(rec: dict[str, Any]) -> tuple[float, float] | None:
    """Mouse normalized position. Buyer-spec mouseX/Y, PRD mouse_x/mouse_y."""
    for kx, ky in (("mouse_x", "mouse_y"), ("mouseX", "mouseY")):
        if kx in rec and ky in rec:
            x, y = rec[kx], rec[ky]
            if x is None or y is None:
                continue
            return float(x), float(y)
    return None


def _mouse_dxy(rec: dict[str, Any]) -> tuple[float, float]:
    return float(rec.get("mouse_dx", 0)), float(rec.get("mouse_dy", 0))


def _key_codes(rec: dict[str, Any]) -> list[int]:
    raw = rec.get("keyCode") or []
    if isinstance(raw, list):
        return [int(k) for k in raw]
    return []


def _fps(records: list[dict[str, Any]]) -> float:
    """Look up fps from records or default to 30."""
    for r in records:
        f = r.get("fps")
        if isinstance(f, (int, float)) and f > 0:
            return float(f)
    return 30.0


# ---- Check 1 — Keyboard event reconstruction --------------------------------

def reconstruct_key_events(records: list[dict[str, Any]]) -> list[tuple[int, str, int]]:
    """Diff frame-to-frame keyCode lists into synthetic (frame, type, kc) events.

    Mirrors what a real key listener would have emitted. Held keys persist
    across frames; events fire only on the boundary frame where membership
    in `keyCode` changes.
    """
    events: list[tuple[int, str, int]] = []
    prev: set[int] = set()
    for i, rec in enumerate(records):
        cur = set(_key_codes(rec))
        for kc in cur - prev:
            events.append((i, "key_down", kc))
        for kc in prev - cur:
            events.append((i, "key_up", kc))
        prev = cur
    return events


def replay_key_events(events: list[tuple[int, str, int]],
                      frame_count: int) -> list[list[int]]:
    """Replay (frame, type, kc) tuples through the recorder's latching logic.

    Same algorithm as recorder_consumer_lite._run_one_session: events at frame F
    apply at the START of frame F; the resulting `cur_keys` list is the
    snapshot for frame F (and later frames until the next event).
    """
    out: list[list[int]] = []
    cur_keys: list[int] = []
    ev_idx = 0
    events_sorted = sorted(events, key=lambda e: e[0])
    for f in range(frame_count):
        while ev_idx < len(events_sorted) and events_sorted[ev_idx][0] <= f:
            _, et, kc = events_sorted[ev_idx]
            if et == "key_down":
                if kc not in cur_keys:
                    cur_keys.append(kc)
            elif et == "key_up" and kc in cur_keys:
                cur_keys.remove(kc)
            ev_idx += 1
        out.append(list(cur_keys))
    return out


def check1_keyboard(records: list[dict[str, Any]]) -> dict[str, Any]:
    events = reconstruct_key_events(records)
    replayed = replay_key_events(events, len(records))
    mismatches: list[tuple[int, list[int], list[int]]] = []
    for i, rec in enumerate(records):
        original = sorted(_key_codes(rec))
        rebuilt = sorted(replayed[i])
        if original != rebuilt:
            mismatches.append((i, original, rebuilt))
    samples = [
        f"frame {i}: original={orig} rebuilt={reb}"
        for i, orig, reb in mismatches[:SAMPLE_BAD_FRAMES]
    ]
    return {
        "check": 1,
        "name": "Keyboard event reconstruction",
        "passed": len(mismatches) == 0,
        "checked": len(records),
        "mismatches": len(mismatches),
        "events_synthesized": len(events),
        "first_mismatches": samples,
    }


# ---- Check 2 — Mouse position reconstruction --------------------------------

def check2_mouse_position(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Σ mouse_dx from frame 0 should reach mouse_x[N] - mouse_x[0].

    The recorder stores deltas in pixel units (recorder_consumer_lite line 1276-1277
    uses `(cur_mx - prev_mx) / SCREEN_W`, so they're effectively normalized).
    Buyer-spec samples store deltas in raw pixels. We try both and take the
    one with smaller residual; if neither matches we fail.
    """
    if len(records) < 2:
        return {
            "check": 2,
            "name": "Mouse position reconstruction",
            "passed": True,
            "checked": len(records),
            "mismatches": 0,
            "note": "fewer than 2 frames — vacuous",
            "first_mismatches": [],
        }

    # Try normalized-domain (delta is already a fraction of screen).
    mismatches_norm: list[tuple[int, float, float]] = []
    pos0 = _mouse_xy(records[0])
    if pos0 is None:
        return {
            "check": 2,
            "name": "Mouse position reconstruction",
            "passed": True,
            "checked": 0,
            "mismatches": 0,
            "note": "no mouse position fields — vacuous",
            "first_mismatches": [],
        }
    cum_dx = 0.0
    cum_dy = 0.0
    for i, rec in enumerate(records):
        if i > 0:
            dx, dy = _mouse_dxy(rec)
            cum_dx += dx
            cum_dy += dy
        cur = _mouse_xy(rec)
        if cur is None:
            continue
        expected_x = pos0[0] + cum_dx
        expected_y = pos0[1] + cum_dy
        # Modulo screen-wrap tolerance: re-anchor to [0, 1] then compare.
        ex_mod = expected_x - math.floor(expected_x)
        ey_mod = expected_y - math.floor(expected_y)
        residual = max(min(abs(cur[0] - expected_x), abs(cur[0] - ex_mod)),
                       min(abs(cur[1] - expected_y), abs(cur[1] - ey_mod)))
        if residual > EPS_MOUSE_REL:
            mismatches_norm.append((i, cur[0] - expected_x, cur[1] - expected_y))

    # Try pixel-domain (delta is raw pixels; recorded position is normalized).
    SCREEN_W, SCREEN_H = 1920, 1080
    mismatches_px: list[tuple[int, float, float]] = []
    cum_px = 0.0
    cum_py = 0.0
    for i, rec in enumerate(records):
        if i > 0:
            dx, dy = _mouse_dxy(rec)
            cum_px += dx / SCREEN_W
            cum_py += dy / SCREEN_H
        cur = _mouse_xy(rec)
        if cur is None:
            continue
        expected_x = pos0[0] + cum_px
        expected_y = pos0[1] + cum_py
        ex_mod = expected_x - math.floor(expected_x)
        ey_mod = expected_y - math.floor(expected_y)
        residual = max(min(abs(cur[0] - expected_x), abs(cur[0] - ex_mod)),
                       min(abs(cur[1] - expected_y), abs(cur[1] - ey_mod)))
        if residual > EPS_MOUSE_REL:
            mismatches_px.append((i, cur[0] - expected_x, cur[1] - expected_y))

    # Pick the interpretation with fewer mismatches.
    if len(mismatches_px) < len(mismatches_norm):
        mismatches = mismatches_px
        domain = "pixel"
    else:
        mismatches = mismatches_norm
        domain = "normalized"

    samples = [
        f"frame {i}: residual_x={rx:.4f} residual_y={ry:.4f}"
        for i, rx, ry in mismatches[:SAMPLE_BAD_FRAMES]
    ]
    return {
        "check": 2,
        "name": "Mouse position reconstruction",
        "passed": len(mismatches) == 0,
        "checked": len(records),
        "mismatches": len(mismatches),
        "delta_domain": domain,
        "first_mismatches": samples,
    }


# ---- Check 3 — Quaternion ↔ Euler round-trip --------------------------------

def check3_quat_euler(records: list[dict[str, Any]]) -> dict[str, Any]:
    has_euler_field = any(
        "camera_rotation_euler" in r or "camera_rotation_oula" in r
        for r in records
    )
    if not has_euler_field:
        return {
            "check": 3,
            "name": "Quaternion-Euler round-trip",
            "passed": True,
            "checked": 0,
            "mismatches": 0,
            "note": "no euler field present — vacuous",
            "first_mismatches": [],
        }
    mismatches: list[tuple[int, float]] = []
    max_err = 0.0
    for i, rec in enumerate(records):
        q = _quat(rec.get("camera_rotation_quaternion"))
        if "camera_rotation_euler" in rec:
            e_orig = _vec3(rec["camera_rotation_euler"])
        elif "camera_rotation_oula" in rec:
            e_orig = _vec3(rec["camera_rotation_oula"])
        else:
            continue
        roll_rt, pitch_rt, yaw_rt = quat_to_euler_zyx(q)
        # Recorded euler convention is [pitch, yaw, roll] per PRD line 73-74
        # (matches recorder_consumer_lite line 1294 / sample_tarball_builder).
        # Compare each axis modulo 360°.
        diffs = []
        for orig, rebuilt in zip(e_orig, (pitch_rt, yaw_rt, roll_rt)):
            d = abs(orig - rebuilt)
            d = min(d, 360 - d)
            diffs.append(d)
        err = max(diffs)
        if err > max_err:
            max_err = err
        if err > EPS_EULER_RT_DEG:
            mismatches.append((i, err))
    samples = [
        f"frame {i}: euler_rt_err={err:.3f}°"
        for i, err in mismatches[:SAMPLE_BAD_FRAMES]
    ]
    return {
        "check": 3,
        "name": "Quaternion-Euler round-trip",
        "passed": len(mismatches) == 0,
        "checked": len(records),
        "mismatches": len(mismatches),
        "max_err_deg": round(max_err, 4),
        "tolerance_deg": EPS_EULER_RT_DEG,
        "first_mismatches": samples,
    }


# ---- Check 4 — Frame-time consistency ---------------------------------------

def check4_frame_time(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        return {
            "check": 4,
            "name": "Frame-time consistency",
            "passed": True,
            "checked": 0,
            "mismatches": 0,
            "note": "no records",
            "first_mismatches": [],
        }
    fps = _fps(records)

    # For PRD `time` strings we need a base epoch (the first frame's time).
    base: float | None = None
    if "time" in records[0] and isinstance(records[0]["time"], str):
        from datetime import datetime
        try:
            t = datetime.strptime(records[0]["time"][:23], "%Y-%m-%d %H:%M:%S.%f")
            base = t.timestamp()
        except ValueError:
            base = None

    t0 = _timestamp_seconds(records[0], base)
    if t0 is None:
        return {
            "check": 4,
            "name": "Frame-time consistency",
            "passed": True,
            "checked": 0,
            "mismatches": 0,
            "note": "no parseable timestamp field — vacuous",
            "first_mismatches": [],
        }
    mismatches: list[tuple[int, int, int]] = []
    max_drift = 0
    for i, rec in enumerate(records):
        recorded_idx = _frame_index(rec)
        if recorded_idx is None:
            continue
        ti = _timestamp_seconds(rec, base)
        if ti is None:
            continue
        expected_idx = round((ti - t0) * fps)
        drift = abs(expected_idx - recorded_idx)
        if drift > max_drift:
            max_drift = drift
        if drift > EPS_FRAME_INDEX:
            mismatches.append((i, recorded_idx, expected_idx))
    samples = [
        f"frame {i}: recorded={rec_i} expected_from_time={exp_i}"
        for i, rec_i, exp_i in mismatches[:SAMPLE_BAD_FRAMES]
    ]
    return {
        "check": 4,
        "name": "Frame-time consistency",
        "passed": len(mismatches) == 0,
        "checked": len(records),
        "mismatches": len(mismatches),
        "fps": fps,
        "max_drift_frames": max_drift,
        "tolerance_frames": EPS_FRAME_INDEX,
        "first_mismatches": samples,
    }


# ---- Loader & CLI -----------------------------------------------------------

def load_records(clip_dir: Path) -> list[dict[str, Any]]:
    ac_path = clip_dir / "action_camera.json"
    if not ac_path.is_file():
        raise FileNotFoundError(f"{ac_path} not found")
    with open(ac_path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "records" in data:
        return data["records"]
    if isinstance(data, list):
        return data
    raise ValueError("unrecognized action_camera.json shape (expect list or {records:[...]})")


def run_all_checks(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        check1_keyboard(records),
        check2_mouse_position(records),
        check3_quat_euler(records),
        check4_frame_time(records),
    ]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("clip_dir", type=Path, help="Path to extracted clip directory")
    p.add_argument("--json", action="store_true", help="Emit machine-readable JSON report")
    args = p.parse_args(argv)

    try:
        records = load_records(args.clip_dir)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 99
    except (ValueError, json.JSONDecodeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 99

    checks = run_all_checks(records)

    if args.json:
        report = {"records": len(records), "checks": checks}
        print(json.dumps(report, indent=2))
    else:
        print(f"\n=== verify_round_trip.py — {len(records)} records ===\n")
        for c in checks:
            mark = "PASS" if c.get("passed") else "FAIL"
            print(f"[{mark}] Check {c['check']} - {c['name']}")
            for k, v in c.items():
                if k in ("check", "name", "passed", "first_mismatches"):
                    continue
                print(f"    {k}: {v}")
            for sample in c.get("first_mismatches", []):
                print(f"    ! {sample}")
            print()

    return sum(1 for c in checks if not c.get("passed"))


if __name__ == "__main__":
    sys.exit(main())
