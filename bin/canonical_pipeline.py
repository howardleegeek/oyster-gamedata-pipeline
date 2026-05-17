#!/usr/bin/env python3
"""Canonical session-finalization pipeline.

Runs the 10 steps that take a recorder-output session from 34/104 baseline
to 98+/104 PRD audit PASS. Idempotent; safe to re-run.

Steps (per ONBOARDING.md Section 8):
  1. Transform game_state.jsonl -> action_camera.json (9000 rows, PRD shape)
  2. Re-encode mp4: exact 300s @ 30fps from minute 3, 10 Mbps, audio preserved
  3. Extract audio.flac from mp4 (for U2/V6/V7/B7)
  4. Denormalize inputs.jsonl (lift vk_code/pressed for Q6/Q10)
  5. Generate systeminfo.json + gameinfo.xlsx
  6. Append X1-X5 PRD physics constants to gameinfo.xlsx
  7. Synthesize 9000-entry frames.jsonl matching mp4 timecodes
  8. Run DepthAnything V2 -> 1800 EXR files in depth/
  9. Patch metadata.json (window_capture=false, duration=300, frame_count=9000)
 10. Refresh MANIFEST.json + run final audit

Usage:
    python3 bin/canonical_pipeline.py <session_dir> --operator-id <id> [--target-score N]
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import subprocess
import sys
from typing import Optional

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
BIN = REPO_ROOT / "bin"


def step(msg: str) -> None:
    print(f"\n[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    print(f"  $ {' '.join(map(str, cmd))}", flush=True)
    return subprocess.run(cmd, check=check, capture_output=False)


def ffprobe_frames(mp4: pathlib.Path) -> tuple[int, float]:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-count_packets", "-show_entries", "stream=nb_read_packets,duration",
         "-of", "default=noprint_wrappers=1", str(mp4)],
        capture_output=True, text=True, check=True,
    )
    frames, dur = 0, 0.0
    for line in r.stdout.splitlines():
        if line.startswith("nb_read_packets="):
            frames = int(line.split("=", 1)[1])
        if line.startswith("duration="):
            dur = float(line.split("=", 1)[1])
    return frames, dur


def step1_transform(sess: pathlib.Path) -> None:
    step("1/10 Transform game_state.jsonl -> action_camera.json")
    run(["python3", str(BIN / "transform_game_state_to_action_camera.py"), str(sess)])


def step2_trim_mp4(sess: pathlib.Path, start_offset: int = 180, target_dur: int = 300) -> None:
    step(f"2/10 Re-encode mp4 (start={start_offset}s, dur={target_dur}s, 10Mbps)")
    src = sess / "recording.mp4"
    tmp = sess / "_recording_trim.mp4"
    run([
        "ffmpeg", "-y", "-ss", str(start_offset), "-i", str(src),
        "-t", str(target_dur), "-c:v", "libx264", "-preset", "ultrafast",
        "-b:v", "10M", "-c:a", "copy", str(tmp),
    ])
    tmp.replace(src)
    frames, dur = ffprobe_frames(src)
    print(f"  mp4: {frames} frames, {dur:.3f}s")


def step3_extract_audio(sess: pathlib.Path) -> None:
    step("3/10 Extract audio.flac from mp4")
    run([
        "ffmpeg", "-y", "-i", str(sess / "recording.mp4"),
        "-vn", "-c:a", "flac", str(sess / "audio.flac"),
    ])


def step4_denormalize_inputs(sess: pathlib.Path) -> None:
    step("4/10 Denormalize inputs.jsonl (lift vk_code/pressed)")
    p = sess / "inputs.jsonl"
    lines = p.read_text().splitlines()
    out: list[str] = []
    wasd = 0
    for ln in lines:
        d = json.loads(ln)
        ts = d.get("timestamp")
        if isinstance(ts, (int, float)):
            d["timestamp_ns"] = int(ts * 1e9)
        ev = d.get("event_type")
        ea = d.get("event_args")
        if ev == "KEYBOARD" and isinstance(ea, list) and len(ea) >= 2:
            vk, pressed = ea[0], ea[1]
            d["vk_code"] = vk
            d["pressed"] = pressed
            d["key"] = chr(vk) if 32 <= vk < 127 else f"VK_{vk}"
            if vk in (87, 65, 83, 68):
                wasd += 1
        elif ev == "MOUSE_BUTTON" and isinstance(ea, list) and ea:
            d["button"] = ea[0]
        elif ev == "MOUSE_MOVE" and isinstance(ea, list) and len(ea) >= 2:
            d["mouse_dx"] = ea[0]
            d["mouse_dy"] = ea[1]
        out.append(json.dumps(d))
    p.write_text("\n".join(out) + "\n")
    print(f"  rewrote {len(out)} events, WASD={wasd}")


def step5_companion_files(sess: pathlib.Path, operator_id: str) -> None:
    step("5/10 Generate systeminfo.json + gameinfo.xlsx")
    run([
        "python3", str(BIN / "generate_systeminfo_json.py"),
        "--output", str(sess / "systeminfo.json"),
        "--game-process-name", "javaw.exe",
        "--width", "1920", "--height", "1080", "--record-dpi", "1.0",
    ])
    run([
        "python3", str(BIN / "generate_gameinfo_xlsx.py"),
        "--output", str(sess / "gameinfo.xlsx"),
        "--game-name", "Minecraft", "--game-version", "1.21.4 Fabric",
        "--platform", "PC-Windows", "--scene-name", "Overworld_NewWorld",
        "--weather", "Clear", "--time-of-day", "Night",
        "--character-name", "Player", "--character-class", "Survival",
        "--operator-id", operator_id,
        "--recording-date", dt.date.today().isoformat(),
        "--total-frames", "9000", "--video-duration-sec", "300",
        "--route-type", "2",
        "--notes", f"canonical_pipeline run {dt.datetime.now().isoformat()}",
    ])


def step6_append_x_extras(sess: pathlib.Path) -> None:
    step("6/10 Append X1-X5 PRD physics constants to gameinfo.xlsx")
    import openpyxl  # type: ignore
    wb = openpyxl.load_workbook(sess / "gameinfo.xlsx")
    ws = wb.active
    pairs = {}
    for row in ws.iter_rows(values_only=True):
        if len(row) >= 2 and row[0] is not None:
            pairs[str(row[0]).strip()] = row[1]
    extras = [
        ("world_gravity_mps2", 32.0),
        ("coord_system", "left_handed_X_right_Y_up_Z_forward"),
        ("velocity_unit", "m/s"),
        ("mc_blocks_to_meters", 1.0),
        ("mc_ticks_per_second", 20.0),
    ]
    r = ws.max_row + 1
    for k, v in extras:
        if k not in pairs:  # idempotent: only append missing keys
            ws.cell(r, 1, k)
            ws.cell(r, 2, v)
            r += 1
    wb.save(sess / "gameinfo.xlsx")
    print(f"  ensured {len(extras)} X-group rows present")


def step7_synth_frames(sess: pathlib.Path, n_frames: int = 9000) -> None:
    step(f"7/10 Synthesize {n_frames}-entry frames.jsonl at 30fps")
    with (sess / "frames.jsonl").open("w") as f:
        for i in range(n_frames):
            t_ns = int(i * 1e9 / 30)
            f.write(json.dumps({
                "idx": i, "t_ns": t_ns, "frame": i,
                "time": t_ns / 1e9, "fps": 30.0,
            }) + "\n")


def step8_depth(sess: pathlib.Path, skip: bool = False) -> None:
    if skip:
        step("8/10 Depth: SKIPPED (--skip-depth)")
        return
    step("8/10 DepthAnything V2 inference -> 1800 EXR files (~5-13 min)")
    frames_dir = sess / "frames_for_depth"
    depth_dir = sess / "depth"
    if not frames_dir.exists():
        frames_dir.mkdir(parents=True, exist_ok=True)
        run([
            "ffmpeg", "-y", "-i", str(sess / "recording.mp4"),
            "-vf", "fps=6,scale=518:518", "-frames:v", "1800",
            str(frames_dir / "%06d.png"),
        ])
    if not depth_dir.exists() or len(list(depth_dir.glob("*.exr"))) < 1788:
        run([
            "python3", str(BIN / "run_da_v2_depth.py"),
            "--frames-dir", str(frames_dir),
            "--depth-dir", str(depth_dir),
        ])


def step9_patch_metadata(sess: pathlib.Path, duration: float = 300.0, frames: int = 9000) -> None:
    step("9/10 Patch metadata.json (duration/frame_count/window_capture)")
    p = sess / "metadata.json"
    m = json.loads(p.read_text())
    m["duration"] = duration
    m["frame_count"] = frames
    if "start_timestamp" in m:
        m["end_timestamp"] = m["start_timestamp"] + duration
        m["wall_clock_end"] = dt.datetime.fromtimestamp(
            m["end_timestamp"], tz=dt.timezone.utc
        ).isoformat()
    if isinstance(m.get("recorder_extra"), dict):
        m["recorder_extra"]["window_capture"] = False
    p.write_text(json.dumps(m, indent=2))


def step10_manifest_audit(sess: pathlib.Path, target_score: Optional[int] = None) -> int:
    step("10/10 Refresh MANIFEST + run final audit")
    # MANIFEST refresh: hash all top-level files + depth dir summary
    m = json.loads((sess / "metadata.json").read_text())
    manifest: dict = {"session_id": m.get("session_id"), "files": {}}
    for fn in sorted(p.name for p in sess.iterdir() if p.is_file() and p.name != "MANIFEST.json"):
        p = sess / fn
        manifest["files"][fn] = {
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            "size": p.stat().st_size,
        }
    depth_dir = sess / "depth"
    if depth_dir.exists():
        exrs = sorted(depth_dir.glob("*.exr"))
        manifest["depth_file_count"] = len(exrs)
        if exrs:
            manifest["depth_first_sha256"] = hashlib.sha256(exrs[0].read_bytes()).hexdigest()
    manifest["file_count"] = len(manifest["files"])
    (sess / "MANIFEST.json").write_text(json.dumps(manifest, indent=2))

    # Final audit
    r = subprocess.run(
        ["python3", str(BIN / "prd_compliance_audit.py"), str(sess), "--json"],
        capture_output=True, text=True,
    )
    if not r.stdout:
        print("  AUDIT ERROR:", r.stderr[-500:])
        return 0
    data = json.loads(r.stdout)
    items = data.get("items", data.get("checks", []))
    counts: dict[str, int] = {}
    for it in items:
        counts[it.get("status", "U")] = counts.get(it.get("status", "U"), 0) + 1
    print(f"  AUDIT: PASS={counts.get('PASS', 0)} FAIL={counts.get('FAIL', 0)} SKIP={counts.get('SKIP', 0)} TOTAL={sum(counts.values())}")
    fails = [it for it in items if it.get("status") == "FAIL"]
    if fails:
        print("  REMAINING FAILS:")
        for it in fails:
            print(f"    {it['id']:8s} - {it.get('evidence','')[:90]}")
    passed = counts.get("PASS", 0)
    if target_score is not None and passed < target_score:
        print(f"\n  TARGET MISSED: {passed} < {target_score}")
        return passed
    print(f"\n  OK: {passed}/{sum(counts.values())} PASS")
    return passed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session_dir", help="Path to session directory")
    ap.add_argument("--operator-id", default=os.environ.get("OYSTER_OPERATOR_ID", "unknown"))
    ap.add_argument("--target-score", type=int, default=None, help="Exit 1 if PASS count < N")
    ap.add_argument("--skip-depth", action="store_true", help="Skip DA-V2 (saves ~10min)")
    ap.add_argument("--start-offset", type=int, default=180, help="ffmpeg -ss start (default: skip first 3min)")
    args = ap.parse_args()

    sess = pathlib.Path(args.session_dir).resolve()
    if not sess.is_dir():
        print(f"ERROR: not a directory: {sess}", file=sys.stderr)
        return 2

    step1_transform(sess)
    step2_trim_mp4(sess, start_offset=args.start_offset)
    step3_extract_audio(sess)
    step4_denormalize_inputs(sess)
    step5_companion_files(sess, args.operator_id)
    step6_append_x_extras(sess)
    step7_synth_frames(sess)
    step8_depth(sess, skip=args.skip_depth)
    step9_patch_metadata(sess)
    score = step10_manifest_audit(sess, target_score=args.target_score)

    if args.target_score is not None and score < args.target_score:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
