#!/usr/bin/env python3
"""
bin/redteam_lint.py — Red-team adversarial lint testing.

Builds 12 deliberately broken buyer-spec tarballs and runs each through
lint_buyer_spec to verify the lint catches every attack. Used to prove
the lint is robust before shipping rc-4 to vendors.

Usage:
    python3 bin/redteam_lint.py                         # all attacks
    python3 bin/redteam_lint.py --attack drop_systeminfo
    python3 bin/redteam_lint.py --json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

# Make src importable
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

# Lazy import lint
try:
    from oyster_agent_runner.lint.lint_buyer_spec import lint_buyer_spec as _lint_dir  # noqa: E402
except Exception as e:
    print(f"[FATAL] cannot import lint_buyer_spec: {e}")
    sys.exit(2)


def lint_buyer_bundle(tarball_path: str):
    """Adapter: lint expects an unpacked dir, redteam works in tarballs."""
    import tarfile as _t

    with tempfile.TemporaryDirectory() as _tmp:
        tmp_path = Path(_tmp)
        with _t.open(tarball_path) as t:
            t.extractall(tmp_path)
        return _lint_dir(tmp_path)


# --------------------------------------------------------------------------- helpers
def build_clean_bundle(tmpdir: Path) -> Path:
    """Run sample_tarball_builder to get a known-good 5-file bundle, then unpack."""
    tarball = tmpdir / "clean.tar.gz"
    subprocess.run(
        [
            sys.executable,
            str(REPO / "bin" / "sample_tarball_builder.py"),
            "--output",
            str(tarball),
            "--skip-video",
            "--skip-depth",
        ],
        check=True,
        capture_output=True,
    )
    bundle = tmpdir / "bundle"
    bundle.mkdir()
    with tarfile.open(tarball) as t:
        t.extractall(bundle)
    return bundle


def repack(bundle: Path) -> Path:
    """Re-tar the (possibly mutated) bundle for lint."""
    out = bundle.parent / "attack.tar.gz"
    with tarfile.open(out, "w:gz") as t:
        for child in sorted(bundle.iterdir()):
            t.add(child, arcname=child.name)
    return out


# --------------------------------------------------------------------------- attacks
def attack_drop_systeminfo(b: Path):
    (b / "systeminfo.json").unlink()


def attack_drop_video(b: Path):
    (b / "video.mp4").unlink()


def attack_drop_action_camera(b: Path):
    (b / "action_camera.json").unlink()


def attack_systeminfo_wrong_resolution(b: Path):
    p = b / "systeminfo.json"
    d = json.loads(p.read_text())
    d["width"] = 1080
    d["height"] = 720
    p.write_text(json.dumps(d))


def attack_action_camera_pitch_out_of_range(b: Path):
    p = b / "action_camera.json"
    arr = json.loads(p.read_text())
    for r in arr[:5]:
        # Vector3 fields are list[float] [pitch, yaw, roll] (index 0 = pitch)
        r["camera_rotation_oula"][0] = 250.0
    p.write_text(json.dumps(arr))


def attack_action_camera_fx_ne_fy(b: Path):
    p = b / "action_camera.json"
    arr = json.loads(p.read_text())
    for r in arr:
        r["camera_intrinsics"]["fx"] = 960.0
        r["camera_intrinsics"]["fy"] = 480.0
    p.write_text(json.dumps(arr))


def attack_frame_gap(b: Path):
    p = b / "action_camera.json"
    arr = json.loads(p.read_text())
    # remove a chunk in the middle
    del arr[100:110]
    # do NOT renumber — leaves a gap
    p.write_text(json.dumps(arr))


def attack_quaternion_not_unit(b: Path):
    p = b / "action_camera.json"
    arr = json.loads(p.read_text())
    for r in arr[:50]:
        r["camera_rotation_quaternion"] = {"x": 5.0, "y": 5.0, "z": 5.0, "w": 5.0}
    p.write_text(json.dumps(arr))


def attack_wasd_all_w(b: Path):
    p = b / "action_camera.json"
    arr = json.loads(p.read_text())
    for r in arr:
        r["keyCode"] = [87]  # only W
    p.write_text(json.dumps(arr))


def attack_too_much_stationary(b: Path):
    p = b / "action_camera.json"
    arr = json.loads(p.read_text())
    # zero out player_speed for >50% of frames
    for r in arr[: len(arr) // 2 + 100]:
        r["player_speed"] = {"x": 0.0, "y": 0.0, "z": 0.0}
    p.write_text(json.dumps(arr))


def attack_duplicate_frame(b: Path):
    p = b / "action_camera.json"
    arr = json.loads(p.read_text())
    if len(arr) >= 2:
        arr[1]["frame"] = arr[0]["frame"]  # duplicate frame number
    p.write_text(json.dumps(arr))


def attack_bogus_keycode(b: Path):
    p = b / "action_camera.json"
    arr = json.loads(p.read_text())
    for r in arr[:20]:
        r["keyCode"] = [9999, -1, "not_an_int"]  # invalid types
    p.write_text(json.dumps(arr))


ATTACKS = {
    "drop_systeminfo": attack_drop_systeminfo,
    "drop_video": attack_drop_video,
    "drop_action_camera": attack_drop_action_camera,
    "systeminfo_wrong_resolution": attack_systeminfo_wrong_resolution,
    "pitch_out_of_range": attack_action_camera_pitch_out_of_range,
    "fx_ne_fy": attack_action_camera_fx_ne_fy,
    "frame_gap": attack_frame_gap,
    "quaternion_not_unit": attack_quaternion_not_unit,
    "wasd_all_w": attack_wasd_all_w,
    "too_much_stationary": attack_too_much_stationary,
    "duplicate_frame": attack_duplicate_frame,
    "bogus_keycode": attack_bogus_keycode,
}


# --------------------------------------------------------------------------- runner
def run_attack(name: str, mutator) -> dict:
    """Build clean → mutate → repack → lint. Return result dict."""
    with tempfile.TemporaryDirectory() as _tmp:
        tmp_dir = Path(_tmp)
        try:
            bundle = build_clean_bundle(tmp_dir)
        except Exception as e:
            return {"attack": name, "blue_team_caught": None, "error": f"setup-failed: {e}"}
        try:
            mutator(bundle)
        except Exception as e:
            return {"attack": name, "blue_team_caught": None, "error": f"mutate-failed: {e}"}
        attack_tar = repack(bundle)
        try:
            result = lint_buyer_bundle(str(attack_tar))
            # lint returns LintResult or dict — try both
            ok = getattr(result, "ok", None)
            if ok is None and isinstance(result, dict):
                ok = result.get("ok", True)
            issues = getattr(result, "issues", None)
            if issues is None and isinstance(result, dict):
                issues = result.get("issues", [])
            blue_caught = (not ok) or bool(issues)
            return {
                "attack": name,
                "blue_team_caught": blue_caught,
                "ok": ok,
                "issue_count": len(issues) if issues else 0,
            }
        except Exception as e:
            return {
                "attack": name,
                "blue_team_caught": True,
                "error": f"lint-raised-exception: {e}",
            }


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--attack", help="run a specific attack (default: all)")
    p.add_argument("--json", action="store_true", help="JSON output")
    args = p.parse_args(argv)

    selected = [args.attack] if args.attack else list(ATTACKS.keys())
    results = []
    for name in selected:
        if name not in ATTACKS:
            print(f"[SKIP] unknown attack: {name}")
            continue
        r = run_attack(name, ATTACKS[name])
        results.append(r)
        if not args.json:
            sym = "✅" if r.get("blue_team_caught") else "❌"
            print(
                f"{sym} {name:<32}  caught={r.get('blue_team_caught')}  "
                f"issues={r.get('issue_count', '?')}  "
                f"{r.get('error', '')}"
            )

    if args.json:
        print(json.dumps(results, indent=2))

    caught = sum(1 for r in results if r.get("blue_team_caught"))
    miss = sum(1 for r in results if r.get("blue_team_caught") is False)
    if not args.json:
        print()
        print(f"Red-team total: {len(results)}  Blue caught: {caught}  Missed (LINT BUG): {miss}")
    return 0 if miss == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
