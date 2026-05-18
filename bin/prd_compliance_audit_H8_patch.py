"""H8 compliance audit patch for engine_zbuffer depth source.

This module provides a single function evaluate_h8() that can be imported
by the main prd_compliance_audit.py to handle the engine_zbuffer kind
in depth/.source, upgrading it from SKIP_honest → PASS (or PASS_DEGRADED).
"""

import json
import pathlib


def evaluate_h8(session_dir: pathlib.Path) -> dict:
    """Return {'id':'H8', 'status':PASS|FAIL|SKIP_honest|PASS_DEGRADED, 'evidence':str}"""
    source_path = session_dir / "depth" / ".source"
    if not source_path.exists():
        return {"id": "H8", "status": "FAIL", "evidence": "depth/.source missing"}
    src = json.loads(source_path.read_text())
    kind = src.get("kind")
    frame_count = src.get("frame_count", 0)
    if kind == "monocular_da_v2":
        return {
            "id": "H8",
            "status": "SKIP_honest",
            "evidence": f"monocular DA-V2 fallback, {frame_count} frames",
        }
    if kind == "engine_zbuffer":
        if frame_count == 0:
            return {
                "id": "H8",
                "status": "FAIL",
                "evidence": "engine_zbuffer marker but frame_count==0",
            }
        # Verify at least one EXR exists and is readable
        exrs = list((session_dir / "depth").glob("frame_*.exr"))
        if not exrs:
            return {
                "id": "H8",
                "status": "FAIL",
                "evidence": "engine_zbuffer marker but no EXR files",
            }
        try:
            import OpenEXR

            f = OpenEXR.InputFile(str(exrs[0]))
            f.close()
        except Exception as e:
            return {
                "id": "H8",
                "status": "FAIL",
                "evidence": f"engine_zbuffer EXR unreadable: {e}",
            }
        # Check gap miss ratio
        gap_str = src.get("gap_miss_ratio", "0/0")
        try:
            miss, total = (int(x) for x in gap_str.split("/"))
            ratio = miss / total if total else 0
        except (ValueError, ZeroDivisionError):
            ratio = 0
        if ratio > 0.1:
            return {
                "id": "H8",
                "status": "PASS_DEGRADED",
                "evidence": f"engine ground truth with {ratio:.1%} gap misses",
            }
        return {
            "id": "H8",
            "status": "PASS",
            "evidence": f"engine Z-buffer ground truth, {frame_count} frames, EXR readable",
        }
    return {
        "id": "H8",
        "status": "FAIL",
        "evidence": f"unknown depth source kind: {kind}",
    }
