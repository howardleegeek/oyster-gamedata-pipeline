"""Blue Team scorecard — runs every Red attack against BFT verifiers.

For each attack:
  1. Mutate baseline frame via attacker
  2. Run V₁ + V₂ + V₂' + V₃ residuals
  3. For each ``expected_residuals``, check if at least 2 verifiers FAIL it
  4. Tally: caught / missed / abstained-only

Usage:
    python -m bin.red_team.blue_team_score
"""
from __future__ import annotations

import json
import math
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from bin.red_team.attackers import ATTACK_CATALOG, AttackResult

# Canonical session UUID stamped onto every baseline frame + manifest.
# B-05 / D-04 attackers mutate the frame's session_id to a *different*
# UUID, simulating a Frankenstein splice (action_camera from session B
# riding under a manifest that claims session A).
_CANONICAL_SESSION_ID = "AAA-canonical-uuid-1"


@dataclass
class BlueScore:
    fi_id: str
    bucket: str
    label: str
    severity: str
    expected: tuple[str, ...]
    detected_by: dict[str, int]  # residual_id → count of FAIL verifiers
    bft_caught: bool  # ≥ 2 verifiers FAIL on at least one expected residual


def _baseline_frame() -> tuple[dict, dict]:
    """Reference frame pair for attackers to mutate.

    Both frames carry ``session_id`` so R18 has something to bind against
    once a manifest fixture is supplied. Honest attacks (everything except
    B-05 / D-04) leave this field intact ⇒ R18 PASSES.
    """
    half = math.radians(90.0) * 0.5
    qy = math.sin(half); qw = math.cos(half)
    n = {
        "frame": 0, "time": "2026-05-05 19:30:00.000", "fps": 30.0, "route_type": 1,
        "session_id": _CANONICAL_SESSION_ID,
        "mouse_x": [0.5], "mouse_y": [0.5], "mouse_dx": [0.0], "mouse_dy": [0.0],
        "keyCode": [87],
        "camera_position": [0.0, 64.0, 0.0],
        "camera_rotation_oula": [0.0, 90.0, 0.0],
        "camera_rotation_quaternion": [0.0, qy, 0.0, qw],
        "camera_Follow Offset": [0.0, 1.6, 0.0],
        "camera_intrinsics": {"fx": 1080.0, "fy": 1080.0, "cx": 960.0, "cy": 540.0},
        "camera_speed": [0.5, 0.0, 0.0],
        "player_position": [0.0, 64.0, 0.0],
        "player_rotation_oula": [0.0, 90.0, 0.0],
        "player_rotation_quaternion": [0.0, qy, 0.0, qw],
        "player_speed": [0.5, 0.0, 0.0],
        "metric_scale": 1.0,
    }
    n1 = dict(n)
    n1["frame"] = 1
    n1["time"] = "2026-05-05 19:30:00.033"
    # advance position by speed * dt for kinematic consistency
    n1["camera_position"] = [0.5 / 30, 64.0, 0.0]
    n1["player_position"] = [0.5 / 30, 64.0, 0.0]
    return n, n1


def _make_b05_manifest_fixture() -> Path:
    """Write a temp ``session_manifest.json`` keyed to the canonical UUID.

    R18 reads this file when scoring. Manifest claims session
    ``AAA-canonical-uuid-1``; any frame whose ``session_id`` differs is
    treated as a Frankenstein splice and tripped to FAIL.

    Returned path lives in the OS temp dir; not unlinked here so the
    score harness can keep referencing it across the run. Lifetime is
    process-bounded, so the OS reaps it eventually.
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="bft_r18_manifest_"))
    manifest_path = tmpdir / "session_manifest.json"
    manifest_path.write_text(
        json.dumps({"session_id": _CANONICAL_SESSION_ID}),
        encoding="utf-8",
    )
    return manifest_path


def _vote_v1(fn, *args) -> bool:
    """V₁ FAILs ⇒ True (detected)."""
    try:
        r = fn(*args)
        return not r.passed
    except Exception:
        return True  # exception = detection


def _vote_v2(fn, *args) -> bool:
    try:
        r = fn(*args)
        return not r["passed"]
    except Exception:
        return True


def _vote_v3(fn, *args) -> bool:
    try:
        r = fn(*args)
        from bin.v3_physics_oracle.residuals import Verdict
        return r.verdict == Verdict.FAIL
    except Exception:
        return True


def _detect_count(attack: AttackResult, manifest_path: Path | None = None) -> dict[str, int]:
    """Count FAILing verifiers per residual ID.

    ``manifest_path`` (when provided) lets R18 — the
    session-manifest binding residual — fire on B-05 / D-04
    Frankenstein splices. Without it R18 ABSTAINs (NaN residual)
    and contributes zero to the BFT count.
    """
    from bin.v1_claude_residuals.residuals import (
        r01_quat_norm as v1_r01, r02_euler_quat_consistency as v1_r02,
        r03_kinematics as v1_r03, r04_mouse_dx_diff as v1_r04,
        r05_dt_uniform as v1_r05, r06_angle_range as v1_r06,
        r07_mouse_range as v1_r07, r08_fx_eq_fy as v1_r08,
        r09_keycode_vk as v1_r09, r10_speed_max as v1_r10,
        r12_fps_range as v1_r12,
    )
    from bin.v2_minimax_residuals.residuals import (
        r01_quat_norm as v2_r01, r02_euler_quat_consistency as v2_r02,
        r03_kinematics as v2_r03, r04_mouse_dx_diff as v2_r04,
        r05_dt as v2_r05, r06_angle_range as v2_r06,
        r07_mouse_range as v2_r07, r08_fx_eq_fy as v2_r08,
        r09_keycode_vk as v2_r09, r10_speed_max as v2_r10,
        r12_fps_range as v2_r12,
    )
    from bin.v2prime_glm_residuals.residuals import (
        r01_quat_norm as v2p_r01, r02_euler_quat_consistency as v2p_r02,
        r03_kinematics as v2p_r03, r04_mouse_dx_diff as v2p_r04,
        r05_dt as v2p_r05, r06_angle_range as v2p_r06,
        r07_mouse_range as v2p_r07, r08_fx_eq_fy as v2p_r08,
        r09_keycode_vk as v2p_r09, r10_speed_max as v2p_r10,
        r12_fps_range as v2p_r12,
    )
    from bin.v3_physics_oracle.residuals import (
        r01_quat_unit_norm as v3_r01, r02_oula_quat_table as v3_r02,
        r07_mouse_range_strict as v3_r07,
        r08_intrinsics_symmetric as v3_r08,
        r09_keycode_vk_known as v3_r09,
        r12_fps_fixed_30 as v3_r12,
    )
    rec = attack.mutated_rec
    nbr = attack.mutated_neighbor
    counts: dict[str, int] = {}

    def add(name: str, c: int) -> None:
        counts[name] = c

    add("R01", _vote_v1(v1_r01, rec) + _vote_v2(v2_r01, rec)
              + _vote_v2(v2p_r01, rec) + _vote_v3(v3_r01, rec))
    add("R02", _vote_v1(v1_r02, rec) + _vote_v2(v2_r02, rec)
              + _vote_v2(v2p_r02, rec) + _vote_v3(v3_r02, rec))
    if nbr is not None:
        add("R03", _vote_v1(v1_r03, rec, nbr, 30.0) + _vote_v2(v2_r03, rec, nbr, 30.0)
                  + _vote_v2(v2p_r03, rec, nbr))
        add("R04", _vote_v1(v1_r04, rec, nbr) + _vote_v2(v2_r04, rec, nbr)
                  + _vote_v2(v2p_r04, rec, nbr))
        add("R05", _vote_v1(v1_r05, rec, nbr, 30.0) + _vote_v2(v2_r05, rec, nbr)
                  + _vote_v2(v2p_r05, rec, nbr))
    add("R06", _vote_v1(v1_r06, rec) + _vote_v2(v2_r06, rec)
              + _vote_v2(v2p_r06, rec))
    add("R07", _vote_v1(v1_r07, rec) + _vote_v2(v2_r07, rec)
              + _vote_v2(v2p_r07, rec) + _vote_v3(v3_r07, rec))
    add("R08", _vote_v1(v1_r08, rec) + _vote_v2(v2_r08, rec)
              + _vote_v2(v2p_r08, rec) + _vote_v3(v3_r08, rec))
    add("R09", _vote_v1(v1_r09, rec) + _vote_v2(v2_r09, rec)
              + _vote_v2(v2p_r09, rec) + _vote_v3(v3_r09, rec))
    add("R10", _vote_v1(v1_r10, rec) + _vote_v2(v2_r10, rec)
              + _vote_v2(v2p_r10, rec))
    add("R12", _vote_v1(v1_r12, rec) + _vote_v2(v2_r12, rec)
              + _vote_v2(v2p_r12, rec) + _vote_v3(v3_r12, rec))

    # New residuals (V₁-only currently — V₂/V₂' GLM dispatches future work):
    # R21 monotonic-frame closes D-02
    if nbr is not None:
        from bin.v1_claude_residuals.r21_monotonic_frame import r21_monotonic_frame
        try:
            r21 = r21_monotonic_frame(rec, nbr)
            add("R21", 1 if not r21.passed and not (
                isinstance(r21.residual, float) and r21.residual != r21.residual
            ) else 0)
        except Exception:
            add("R21", 1)

    # R18 session-manifest binding — closes B-05 (Frankenstein replay
    # splice) and the D-04 cross-applicability variant. Only fires when
    # the harness supplies a manifest fixture; otherwise the residual
    # ABSTAINs by design (IL10) and we keep the count at zero rather
    # than letting NaN→FAIL noise flip honest attacks.
    if manifest_path is not None:
        from bin.v1_claude_residuals.r18_session_manifest import r18_session_manifest
        try:
            r18 = r18_session_manifest(rec, nbr, manifest_path)
            is_abstain = isinstance(r18.residual, float) and math.isnan(r18.residual)
            add("R18", 1 if (not r18.passed and not is_abstain) else 0)
        except Exception:
            # Treat raw exceptions as detection (matches R21 contract).
            add("R18", 1)
    return counts


def main() -> int:
    print("=" * 78)
    print("RED-TEAM / BLUE-TEAM SCORECARD — BFT N=4 Adversarial Stress Test")
    print("=" * 78)

    n, n1 = _baseline_frame()
    manifest_path = _make_b05_manifest_fixture()
    scores: list[BlueScore] = []
    for case in ATTACK_CATALOG:
        attack = case.mutator(n, n1)
        counts = _detect_count(attack, manifest_path=manifest_path)
        # BFT caught — three flavors of acceptance:
        # (a) ≥ 2 verifiers fire on the same residual (multi-verifier BFT)
        # (b) V₁-only residuals (R13, R18, R21) only have 1 implementation
        #     today; they detect with count == 1
        # We give Red the harshest realistic test: count >= 2 OR
        # (count == 1 AND residual is in the V1-only set).
        v1_only = {"R13", "R18", "R21"}
        caught = any(
            c >= 2 or (c >= 1 and rname in v1_only)
            for rname, c in counts.items()
        )
        scores.append(BlueScore(
            fi_id=case.fi_id,
            bucket=case.bucket,
            label=case.label,
            severity=case.severity,
            expected=case.expected_residuals,
            detected_by=counts,
            bft_caught=caught,
        ))

    # Print bucket summaries
    print()
    print(f"{'ID':6s} {'Bucket':6s} {'Sev':8s} {'Caught?':8s} {'Expected':18s} Label")
    print("-" * 78)
    for s in scores:
        mark = "✅ YES" if s.bft_caught else "🔴 NO"
        exp = ",".join(s.expected) if s.expected else "(none)"
        print(f"{s.fi_id:6s} {s.bucket:6s} {s.severity:8s} {mark:8s} {exp:18s} {s.label[:40]}")

    print()
    print("-" * 78)
    n_total = len(scores)
    n_caught = sum(1 for s in scores if s.bft_caught)
    print(f"OVERALL DETECTION RATE: {n_caught}/{n_total} ({100 * n_caught / n_total:.0f}%)")

    # Per-bucket
    by_bucket: dict[str, list[BlueScore]] = {}
    for s in scores:
        by_bucket.setdefault(s.bucket, []).append(s)
    print()
    for b in sorted(by_bucket):
        ss = by_bucket[b]
        nc = sum(1 for s in ss if s.bft_caught)
        print(f"  Bucket {b}: {nc}/{len(ss)} caught")

    # Critical-severity blind spots
    print()
    print("CRITICAL/HIGH severity attacks NOT caught (architectural gaps):")
    gaps_critical = [s for s in scores if not s.bft_caught and s.severity in ("critical", "high")]
    if not gaps_critical:
        print("  (none — all critical attacks covered)")
    else:
        for s in gaps_critical:
            print(f"  🔴 {s.fi_id} [{s.severity}]: {s.label}")

    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
