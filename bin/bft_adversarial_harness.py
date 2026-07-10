"""BFT Adversarial Fault-Injection Harness.

Five FI cases, each mutates ONE field of an otherwise PRD-compliant frame
and feeds the corrupted frame to V1/V2/V3 verifiers. BFT consensus needs
>=2/3 verifiers to catch each fault on the relevant residual.
Run: ``python3 -m bin.bft_adversarial_harness``. No JSON, no producer
imports, no validator modifications. V2's R02 sign-bug stays buggy.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable

from bin.v1_claude_residuals.residuals import (
    r02_euler_quat_consistency as v1_r02,
)
from bin.v1_claude_residuals.residuals import (
    r07_mouse_range as v1_r07,
)
from bin.v1_claude_residuals.residuals import (
    r08_fx_eq_fy as v1_r08,
)
from bin.v1_claude_residuals.residuals import (
    r09_keycode_vk as v1_r09,
)
from bin.v1_claude_residuals.residuals import (
    r12_fps_range as v1_r12,
)
from bin.v2_minimax_residuals.residuals import (
    r02_euler_quat_consistency as v2_r02,
)
from bin.v2_minimax_residuals.residuals import (
    r07_mouse_range as v2_r07,
)
from bin.v2_minimax_residuals.residuals import (
    r08_fx_eq_fy as v2_r08,
)
from bin.v2_minimax_residuals.residuals import (
    r09_keycode_vk as v2_r09,
)
from bin.v2_minimax_residuals.residuals import (
    r12_fps_range as v2_r12,
)
from bin.v3_physics_oracle.residuals import (
    Verdict,
)
from bin.v3_physics_oracle.residuals import (
    r02_oula_quat_table as v3_r02,
)
from bin.v3_physics_oracle.residuals import (
    r07_mouse_range_strict as v3_r07,
)
from bin.v3_physics_oracle.residuals import (
    r08_intrinsics_symmetric as v3_r08,
)
from bin.v3_physics_oracle.residuals import (
    r09_keycode_vk_known as v3_r09,
)
from bin.v3_physics_oracle.residuals import (
    r12_fps_fixed_30 as v3_r12,
)


@dataclass(frozen=True)
class VerifierVote:
    verifier: str  # "V1" | "V2" | "V3"
    residual: str
    detected: bool  # True iff verifier flagged the FI
    detail: str


def _baseline_frame() -> dict[str, Any]:
    """Pre-mutation frame passes V1/V2/V3 on the targeted residuals.
    oula=(0,0,0)+quat=(0,0,0,1) is identity rotation, in V3's table[0]."""
    return {
        "time": "2026-05-05 12:00:00.000",
        "fps": 30.0,
        "camera_rotation_oula": [0.0, 0.0, 0.0],
        "camera_rotation_quaternion": [0.0, 0.0, 0.0, 1.0],
        "player_rotation_oula": [0.0, 0.0, 0.0],
        "player_rotation_quaternion": [0.0, 0.0, 0.0, 1.0],
        "camera_position": [0.0, 0.0, 0.0],
        "player_position": [0.0, 0.0, 0.0],
        "camera_speed": [0.0, 0.0, 0.0],
        "player_speed": [0.0, 0.0, 0.0],
        "mouse_x": [0.5],
        "mouse_y": [0.5],
        "mouse_dx": [0.0],
        "mouse_dy": [0.0],
        "keyCode": [87],  # 'W'
        "camera_intrinsics": {"fx": 1080.0, "fy": 1080.0, "cx": 960.0, "cy": 540.0},
    }


def _safe_call(fn: Callable[..., Any], rec: dict) -> tuple[bool, str]:
    """Run residual; return (detected_failure, detail). Exceptions count
    as detection. V3 ABSTAIN does NOT count toward consensus."""
    try:
        out = fn(rec)
    except Exception as e:  # exception path is part of the test contract
        return True, f"raised {type(e).__name__}: {e}"
    if hasattr(out, "verdict"):  # V3 OracleResult
        if out.verdict == Verdict.FAIL:
            return True, f"FAIL residual={out.residual:.4g} note={out.note!r}"
        if out.verdict == Verdict.ABSTAIN:
            return False, f"ABSTAIN note={out.note!r}"
        return False, f"PASS residual={out.residual:.4g}"
    if hasattr(out, "passed"):  # V1 ResidualResult dataclass
        passed = bool(out.passed)
        note = getattr(out, "note", "")
        return (
            (not passed),
            f"{'FAIL' if not passed else 'PASS'} residual={out.residual:.4g} note={note!r}",
        )
    if isinstance(out, dict) and "passed" in out:  # V2 dict result
        return (
            not bool(out["passed"])
        ), f"{'FAIL' if not out['passed'] else 'PASS'} residual={out.get('residual'):.4g}"
    return False, f"unknown result type: {type(out).__name__}"


# Fault-injection mutators -----------------------------------------------------


def _fi01_swap_quat_order(rec: dict) -> dict:
    """[x,y,z,w] -> [w,x,y,z]. Identity [0,0,0,1] becomes [1,0,0,0] = 180 pitch."""
    rec = copy.deepcopy(rec)
    qx, qy, qz, qw = rec["camera_rotation_quaternion"]
    rec["camera_rotation_quaternion"] = [qw, qx, qy, qz]
    return rec


def _fi02_keycode_W_to_88(rec: dict) -> dict:
    """87 -> 88. NOTE 88 is in VK table (it is X), so R09 cannot detect.
    The 0/3 detection rate is itself the diagnostic — R09 is not semantic."""
    rec = copy.deepcopy(rec)
    rec["keyCode"] = [88]
    return rec


def _fi03_fps_30_to_25(rec: dict) -> dict:
    rec = copy.deepcopy(rec)
    rec["fps"] = 25.0
    return rec


def _fi04_mouse_x_scalar(rec: dict) -> dict:
    """list[0.5] -> scalar 0.5. V2 raises TypeError on [0] index — counted."""
    rec = copy.deepcopy(rec)
    rec["mouse_x"] = 0.5
    return rec


def _fi05_fx_1080_to_900(rec: dict) -> dict:
    rec = copy.deepcopy(rec)
    rec["camera_intrinsics"] = dict(rec["camera_intrinsics"])
    rec["camera_intrinsics"]["fx"] = 900.0
    return rec


_TRIPLES: dict[str, tuple] = {
    "R02": (v1_r02, v2_r02, v3_r02),
    "R09": (v1_r09, v2_r09, v3_r09),
    "R12": (v1_r12, v2_r12, v3_r12),
    "R07": (v1_r07, v2_r07, v3_r07),
    "R08": (v1_r08, v2_r08, v3_r08),
}


def _votes_for(rec: dict, residual_id: str) -> list[VerifierVote]:
    fns = _TRIPLES[residual_id]
    labels = ("V1", "V2", "V3")
    out = []
    for label, fn in zip(labels, fns):
        detected, detail = _safe_call(fn, rec)
        out.append(VerifierVote(label, residual_id, detected, detail))
    return out


FI_CASES: tuple[tuple[str, str, Callable[[dict], dict], str], ...] = (
    ("FI-01", "quaternion order [x,y,z,w] -> [w,x,y,z]", _fi01_swap_quat_order, "R02"),
    ("FI-02", "keyCode W (87) -> 88", _fi02_keycode_W_to_88, "R09"),
    ("FI-03", "fps 30.0 -> 25.0", _fi03_fps_30_to_25, "R12"),
    ("FI-04", "mouse_x list[0.5] -> scalar 0.5", _fi04_mouse_x_scalar, "R07"),
    ("FI-05", "fx 1080 -> 900 (fy still 1080)", _fi05_fx_1080_to_900, "R08"),
)


def main() -> int:
    print("=" * 72)
    print("BFT ADVERSARIAL FAULT-INJECTION HARNESS")
    print("Verifiers: V1 Claude, V2 MiniMax, V3 Physics-Oracle (N=3, consensus>=2)")
    print("=" * 72)

    baseline = _baseline_frame()
    fi_pass_count = 0

    for fi_id, label, mutator, residual_id in FI_CASES:
        mutated = mutator(baseline)
        votes = _votes_for(mutated, residual_id)
        print()
        print(f"[{fi_id}] {label}")
        print(f"  expected failing residual: {residual_id}")
        for v in votes:
            mark = "DETECT" if v.detected else "missed"
            print(f"  {v.verifier} {v.residual} {mark}: {v.detail}")
        n_detected = sum(1 for v in votes if v.detected)
        bft_pass = n_detected >= 2
        verdict = "PASS" if bft_pass else "FAIL"
        if bft_pass:
            fi_pass_count += 1
        print(f"[{fi_id}] {verdict}: detected by {n_detected}/3 verifiers")

    print()
    print("=" * 72)
    print(f"DETECTION RATE = {fi_pass_count}/{len(FI_CASES)}")
    print("=" * 72)
    return 0 if fi_pass_count == len(FI_CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
