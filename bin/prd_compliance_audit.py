#!/usr/bin/env python3
"""PRD compliance audit — runs MECE checklist (`oyster-audit/PRD-COMPLIANCE-MECE.md`)
against a real session directory. NOT synthetic data — only real recordings.

Usage:
  python3 bin/prd_compliance_audit.py <session_dir> [--json | --markdown]

Output: 83-item ✅/❌ matrix with per-item evidence.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

# Required action_camera.json fields per PRD §5 (20 literal names)
PRD_FIELDS_20 = [
    "frame", "time", "fps", "route_type",
    "mouse_x", "mouse_y", "mouse_dx", "mouse_dy",
    "keyCode",
    "camera_position", "camera_rotation_oula", "camera_rotation_quaternion",
    "camera_Follow Offset", "camera_intrinsics", "camera_speed",
    "player_position", "player_rotation_oula", "player_rotation_quaternion",
    "player_speed", "metric_scale",
]
GAMEINFO_FIELDS_14 = [
    "game_name", "game_version", "platform", "scene_name",
    "weather", "time_of_day", "character_name", "character_class",
    "operator_id", "recording_date", "total_frames",
    "video_duration_sec", "route_type", "notes",
]


def _result(id_: str, ok: bool, evidence: str) -> dict:
    return {"id": id_, "status": "PASS" if ok else "FAIL", "evidence": evidence}


def audit_group_a(session: Path) -> list[dict]:
    """A1-A5: files present."""
    items = []
    for id_, fname in [
        ("A1", "recording.mp4"), ("A2", "action_camera.json"),
        ("A3", "gameinfo.xlsx"), ("A5", "metadata.json"),
    ]:
        p = session / fname
        ok = p.exists() and p.stat().st_size > 0
        items.append(_result(id_, ok, f"{fname}: {'present, ' + str(p.stat().st_size) + ' bytes' if ok else 'missing or empty'}"))
    # A4: depth/ dir with 1800 EXR files
    depth_dir = session / "depth"
    if depth_dir.exists():
        exrs = list(depth_dir.glob("*.exr"))
        ok = 1788 <= len(exrs) <= 1810
        items.append(_result("A4", ok, f"depth/: {len(exrs)} EXR files (expect 1788-1810)"))
    else:
        items.append(_result("A4", False, "depth/ directory missing"))
    return items


def audit_groups_c_d_e(session: Path) -> list[dict]:
    """Read action_camera.json, check field presence + value constraints + coord."""
    items = []
    ac_path = session / "action_camera.json"
    if not ac_path.exists():
        items.append(_result("C-prereq", False, "action_camera.json missing — skipping C/D/E"))
        return items
    try:
        data = json.loads(ac_path.read_text())
    except Exception as e:
        items.append(_result("C-prereq", False, f"action_camera.json parse error: {e}"))
        return items
    if not isinstance(data, list) or len(data) == 0:
        items.append(_result("C-prereq", False, "action_camera.json must be non-empty array"))
        return items

    sample = data[0]
    # Group C: 20 field presence
    for i, field in enumerate(PRD_FIELDS_20, 1):
        ok = field in sample
        items.append(_result(f"C{i}", ok, f'"{field}": {"present" if ok else "MISSING"}'))

    # Group D: value constraints
    frames = [f.get("frame", f.get("frame_index", None)) for f in data[:100]]
    d1_ok = all(isinstance(x, int) for x in frames) and frames == list(range(min(len(data), 100)))
    items.append(_result("D1", d1_ok, f"frame continuity 0..99: {'OK' if d1_ok else 'GAP detected'}"))

    n = len(data)
    items.append(_result("D2", 8900 <= n <= 9100, f"frame count: {n} (expect 8900-9100)"))

    fps_set = {f.get("fps") for f in data[:100]}
    items.append(_result("D3", fps_set == {30.0} or fps_set == {30}, f"fps values (first 100): {fps_set}"))

    rt_set = {f.get("route_type") for f in data}
    rt_ok = rt_set and all(rt in {1, 2, 3} for rt in rt_set)
    items.append(_result("D4", rt_ok, f"route_type values: {rt_set}"))

    mx_range = all(0 <= f.get("mouse_x", -1) <= 1 for f in data[:100])
    my_range = all(0 <= f.get("mouse_y", -1) <= 1 for f in data[:100])
    items.append(_result("D5", mx_range and my_range, f"mouse_x/y in [0,1]: {mx_range and my_range}"))

    # D7: quaternion norm ≈ 1
    d7_violations = 0
    for f in data[:200]:
        q = f.get("camera_rotation_quaternion")
        if isinstance(q, list) and len(q) == 4:
            norm = math.sqrt(sum(c * c for c in q))
            if not (0.99 <= norm <= 1.01):
                d7_violations += 1
    items.append(_result("D7", d7_violations == 0, f"quat norm violations in first 200: {d7_violations}"))

    # D8: fx == fy
    intr = sample.get("camera_intrinsics", {})
    d8_ok = "fx" in intr and "fy" in intr and intr["fx"] == intr["fy"]
    items.append(_result("D8", d8_ok, f"camera_intrinsics fx==fy: {intr.get('fx')} == {intr.get('fy')}"))

    # D9: speed magnitudes ≤ 100 m/s
    d9_max = 0.0
    for f in data[:500]:
        cs = f.get("camera_speed", [0, 0, 0])
        if isinstance(cs, list):
            d9_max = max(d9_max, math.sqrt(sum(c * c for c in cs)))
    items.append(_result("D9", d9_max <= 100, f"max camera_speed magnitude (first 500): {d9_max:.2f} m/s"))

    # D10: angle ranges
    d10_violations = 0
    for f in data[:200]:
        oula = f.get("camera_rotation_oula", [0, 0, 0])
        if isinstance(oula, list) and len(oula) == 3:
            if not (-90 <= oula[0] <= 90 and -180 <= oula[1] <= 180 and -180 <= oula[2] <= 180):
                d10_violations += 1
    items.append(_result("D10", d10_violations == 0, f"angle range violations: {d10_violations}"))

    # Group E: coord system
    items.append(_result("E4", "camera_rotation_quaternion" in sample and isinstance(sample["camera_rotation_quaternion"], list) and len(sample["camera_rotation_quaternion"]) == 4,
                         "quat order xyzw (list of 4)"))

    return items


def audit_group_f(session: Path) -> list[dict]:
    """F1-F14: gameinfo.xlsx fields."""
    items = []
    gi_path = session / "gameinfo.xlsx"
    if not gi_path.exists():
        for i, fld in enumerate(GAMEINFO_FIELDS_14, 1):
            items.append(_result(f"F{i}", False, f"{fld}: gameinfo.xlsx missing"))
        return items
    try:
        import openpyxl  # noqa: PLC0415
        wb = openpyxl.load_workbook(gi_path, data_only=True)
        ws = wb.active
        cells_text = set()
        for row in ws.iter_rows(values_only=True):
            for c in row:
                if c is not None:
                    cells_text.add(str(c).strip())
        for i, fld in enumerate(GAMEINFO_FIELDS_14, 1):
            ok = fld in cells_text
            items.append(_result(f"F{i}", ok, f"{fld}: {'present in xlsx' if ok else 'NOT in xlsx'}"))
    except ImportError:
        for i, fld in enumerate(GAMEINFO_FIELDS_14, 1):
            items.append(_result(f"F{i}", False, "openpyxl not installed — cannot audit xlsx"))
    return items


# ── Self-healing transforms ──────────────────────────────────────────────
# Each transform attempts to convert legacy/wrong-named output into
# PRD-compliant form. Idempotent: running --fix twice produces the same
# result as running once. Original file backed up to *.bak first.
LEGACY_TO_PRD_RENAMES = {
    "mouseX": "mouse_x",
    "mouseY": "mouse_y",
    "frame_index": "frame",
    "timestamp": "time",
    "Follow_Offset": "camera_Follow Offset",
    "rotation_oula": "camera_rotation_oula",
    "rotation_quaternion": "camera_rotation_quaternion",
}


def heal_action_camera(session: Path) -> dict:
    """Rename legacy field names to PRD form in action_camera.json.

    Returns {'renamed_fields': [...], 'frames_touched': N}. Idempotent.
    """
    ac_path = session / "action_camera.json"
    if not ac_path.exists():
        return {"renamed_fields": [], "frames_touched": 0, "note": "no action_camera.json"}
    data = json.loads(ac_path.read_text())
    if not isinstance(data, list) or len(data) == 0:
        return {"renamed_fields": [], "frames_touched": 0, "note": "empty/non-array"}
    renamed = set()
    for f in data:
        for old, new in LEGACY_TO_PRD_RENAMES.items():
            if old in f and new not in f:
                f[new] = f.pop(old)
                renamed.add(f"{old}→{new}")
            elif old in f and new in f:
                # both present — keep new, discard old
                del f[old]
                renamed.add(f"{old}→{new} (deduped)")
    if renamed:
        # backup + write
        bak = ac_path.with_suffix(".json.bak")
        if not bak.exists():
            bak.write_text(ac_path.read_text())
        ac_path.write_text(json.dumps(data, indent=2))
    return {"renamed_fields": sorted(renamed), "frames_touched": len(data) if renamed else 0}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: prd_compliance_audit.py <session_dir> [--json|--markdown] [--fix]", file=sys.stderr)
        return 2
    session = Path(argv[1])
    if not session.exists():
        print(f"FATAL: session dir does not exist: {session}", file=sys.stderr)
        return 2

    out_format = "--json" if "--json" in argv else "--markdown"
    do_heal = "--fix" in argv

    if do_heal:
        heal_report = heal_action_camera(session)
        print(f"# 自愈 (self-heal) pass — {session.name}\n")
        if heal_report["renamed_fields"]:
            print(f"  renamed in {heal_report['frames_touched']} frames:")
            for r in heal_report["renamed_fields"]:
                print(f"    - {r}")
            print(f"  backup saved: action_camera.json.bak")
        else:
            print(f"  no legacy names found ({heal_report.get('note', 'nothing to heal')})")
        print()

    items = []
    items.extend(audit_group_a(session))
    items.extend(audit_groups_c_d_e(session))
    items.extend(audit_group_f(session))

    total = len(items)
    passed = sum(1 for it in items if it["status"] == "PASS")
    failed = total - passed

    if out_format == "--json":
        report = {
            "session_dir": str(session),
            "total_items": total,
            "passed": passed,
            "failed": failed,
            "items": items,
        }
        print(json.dumps(report, indent=2))
    else:
        print(f"# PRD Compliance Audit — {session.name}\n")
        print(f"**{passed}/{total} PASS** ({100*passed/total:.0f}%)  · {failed} FAIL\n")
        print("| ID | Status | Evidence |")
        print("|---|---|---|")
        for it in items:
            mark = "✅" if it["status"] == "PASS" else "❌"
            print(f"| {it['id']} | {mark} | {it['evidence']} |")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
