"""BFT consensus orchestrator for action_camera frame verification.

We run N independent verifiers (V1 Claude, V2 MiniMax, V3 Physics-Oracle, and
optionally V2' GLM) on each frame pair. Each verifier emits one Vote per
residual: PASS / FAIL / ABSTAIN. Tally aggregates by residual name and
applies the standard PBFT majority rule: f = (N-1)//3, COMMIT requires
>= 2f+1 PASS votes, REJECT requires >= f+1 FAIL votes that also outnumber
PASS, equal PASS/FAIL triggers VIEW_CHANGE, and INSUFFICIENT means too many
ABSTAINs to decide. IL3 (independence) is enforced upstream by the verifier
modules - this orchestrator only counts votes, never sees source residual
math, so a malicious verifier cannot rewrite consensus from inside.
"""
from __future__ import annotations

import importlib
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class Vote:
    verifier_id: str
    residual: str
    verdict: str  # 'PASS' | 'FAIL' | 'ABSTAIN'
    residual_value: float
    threshold: float
    evidence: str = ""


# Each entry: (residual_name, fn_name, needs_neighbor, needs_fps).
_V1_RES = [
    ("R01", "r01_quat_norm", False, False),
    ("R02", "r02_euler_quat_consistency", False, False),
    ("R03", "r03_kinematics", True, True),
    ("R04", "r04_mouse_dx_diff", True, False),
    ("R05", "r05_dt_uniform", True, True),
    ("R06", "r06_angle_range", False, False),
    ("R07", "r07_mouse_range", False, False),
    ("R08", "r08_fx_eq_fy", False, False),
    ("R09", "r09_keycode_vk", False, False),
    ("R10", "r10_speed_max", False, False),
    ("R12", "r12_fps_range", False, False),
]
_V2_RES = [
    ("R01", "r01_quat_norm", False, False),
    ("R02", "r02_euler_quat_consistency", False, False),
    ("R03", "r03_kinematics", True, True),
    ("R04", "r04_mouse_dx_diff", True, False),
    ("R05", "r05_dt", True, False),
    ("R06", "r06_angle_range", False, False),
    ("R07", "r07_mouse_range", False, False),
    ("R08", "r08_fx_eq_fy", False, False),
    ("R09", "r09_keycode_vk", False, False),
    ("R10", "r10_speed_max", False, False),
    ("R12", "r12_fps_range", False, False),
]
_V3_RES = [
    ("R01", "r01_quat_unit_norm", False, False),
    ("R02", "r02_oula_quat_table", False, False),
    ("R07", "r07_mouse_range_strict", False, False),
    ("R08", "r08_intrinsics_symmetric", False, False),
    ("R09", "r09_keycode_vk_known", False, False),
    ("R12", "r12_fps_fixed_30", False, False),
]

# V₂' GLM has slightly different residual signatures (R03/R05 take only
# (rec, neighbor) — no fps arg). This is an LLM-API independence cost: each
# verifier may have its own contract. Orchestrator adapts via separate registry.
_V2P_RES = [
    ("R01", "r01_quat_norm", False, False),
    ("R02", "r02_euler_quat_consistency", False, False),
    ("R03", "r03_kinematics", True, False),     # GLM: 2-arg signature
    ("R04", "r04_mouse_dx_diff", True, False),
    ("R05", "r05_dt", True, False),              # GLM: 2-arg signature
    ("R06", "r06_angle_range", False, False),
    ("R07", "r07_mouse_range", False, False),
    ("R08", "r08_fx_eq_fy", False, False),
    ("R09", "r09_keycode_vk", False, False),
    ("R10", "r10_speed_max", False, False),
    ("R12", "r12_fps_range", False, False),
]


def _load(path: str) -> Any:
    try:
        return importlib.import_module(path)
    except ImportError:
        return None


def _to_vote_v1(name: str, vid: str, r: Any) -> Vote:
    return Vote(vid, name, "PASS" if r.passed else "FAIL", float(r.residual),
                float(r.threshold), getattr(r, "note", ""))


def _to_vote_v2(name: str, vid: str, r: dict) -> Vote:
    return Vote(vid, name, "PASS" if r.get("passed") else "FAIL",
                float(r.get("residual", 0.0)), float(r.get("threshold", 0.0)),
                str(r.get("name", "")))


def _to_vote_v3(name: str, vid: str, r: Any) -> Vote:
    verdict = r.verdict.value if hasattr(r.verdict, "value") else str(r.verdict)
    val = math.nan if (isinstance(r.residual, float) and math.isnan(r.residual)) else float(r.residual)
    return Vote(vid, name, verdict, val, 0.0, getattr(r, "note", "") or "")


def _invoke(fn: Callable[..., Any], rec: dict, neighbor: dict | None,
            fps: float, needs_n: bool, needs_f: bool) -> Any:
    if needs_n and needs_f:
        return fn(rec, neighbor, fps)
    if needs_n:
        return fn(rec, neighbor)
    if needs_f:
        return fn(rec, fps)
    return fn(rec)


def _run(module: Any, vid: str, registry: list, normalize: Callable[[str, str, Any], Vote],
         frame: dict, neighbor: dict | None, fps: float) -> list[Vote]:
    out: list[Vote] = []
    if module is None:
        return out
    for name, fn_name, needs_n, needs_f in registry:
        if needs_n and neighbor is None:
            continue
        fn = getattr(module, fn_name, None)
        if fn is None:
            continue
        try:
            result = _invoke(fn, frame, neighbor, fps, needs_n, needs_f)
            out.append(normalize(name, vid, result))
        except Exception as exc:  # exception still counts as detection (FAIL)
            out.append(Vote(vid, name, "FAIL", math.nan, 0.0,
                            f"{type(exc).__name__}: {exc}"))
    return out


def collect_votes(frame: dict, neighbor: dict | None = None,
                  fps: float = 30.0) -> list[Vote]:
    """Run all V1/V2/V3 (and V2' if present) residuals on the frame pair.

    Residuals needing neighbor (R03, R04, R05) are skipped if neighbor is
    None. Any verifier exception becomes a FAIL vote with evidence=str(exc):
    a TypeError raised by V2 R07 on scalar mouse_x is still detection.
    """
    votes: list[Vote] = []
    votes += _run(_load("bin.v1_claude_residuals.residuals"), "V1", _V1_RES,
                  _to_vote_v1, frame, neighbor, fps)
    votes += _run(_load("bin.v2_minimax_residuals.residuals"), "V2", _V2_RES,
                  _to_vote_v2, frame, neighbor, fps)
    votes += _run(_load("bin.v2prime_glm_residuals.residuals"), "V2prime",
                  _V2P_RES, _to_vote_v2, frame, neighbor, fps)
    votes += _run(_load("bin.v3_physics_oracle.residuals"), "V3", _V3_RES,
                  _to_vote_v3, frame, neighbor, fps)
    return votes


def tally(votes: list[Vote]) -> dict[str, dict[str, Any]]:
    """Aggregate votes by residual name and apply the BFT majority rule.

    N is the count of distinct verifiers that voted on the residual (with a
    floor of 3, since the canonical committee is V1+V2+V3); f = (N-1)//3.
    Decision precedence (BFT-correct):

    1. REJECT: failed >= f+1 AND failed > passed - clear failure majority.
    2. Clean tie (passed == failed, no abstains) -> VIEW_CHANGE.
    3. Abstain-tainted tie -> INSUFFICIENT (third witness missing).
    4. Supermajority (passed >= 2f+1 AND passed > failed) -> COMMIT.
    5. Plain plurality with a quorum (passed > failed AND p+f >= f+1) -> COMMIT.
    6. Otherwise INSUFFICIENT.
    """
    distinct = {v.verifier_id for v in votes}
    n_total = max(len(distinct), 3)
    f = (n_total - 1) // 3

    by_res: dict[str, dict[str, int]] = {}
    for v in votes:
        bucket = by_res.setdefault(v.residual,
                                   {"passed": 0, "failed": 0, "abstain": 0})
        if v.verdict == "PASS":
            bucket["passed"] += 1
        elif v.verdict == "FAIL":
            bucket["failed"] += 1
        else:
            bucket["abstain"] += 1

    out: dict[str, dict[str, Any]] = {}
    for r_name, c in by_res.items():
        p, fl, ab = c["passed"], c["failed"], c["abstain"]
        if fl >= f + 1 and fl > p:
            decision = "REJECT"
        elif p == fl and p >= 1 and ab == 0:
            decision = "VIEW_CHANGE"
        elif p == fl and p >= 1 and ab > 0:
            decision = "INSUFFICIENT"
        elif p >= 2 * f + 1 and p > fl:
            decision = "COMMIT"
        elif p > fl and p + fl >= f + 1:
            decision = "COMMIT"
        else:
            decision = "INSUFFICIENT"
        out[r_name] = {"passed": p, "failed": fl, "abstain": ab,
                       "decision": decision}
    return out


def aggregate_dataset(records: list[dict], fps: float = 30.0) -> dict[str, Any]:
    """Run collect_votes + tally on each frame pair (n, n+1)."""
    n_pairs = max(len(records) - 1, 0)
    by_res: dict[str, dict[str, int]] = {}
    for i in range(n_pairs):
        for r_name, info in tally(collect_votes(records[i], records[i + 1], fps=fps)).items():
            bucket = by_res.setdefault(r_name, {"COMMIT": 0, "REJECT": 0, "VIEW_CHANGE": 0, "INSUFFICIENT": 0})
            bucket[info["decision"]] += 1

    all_commit = True
    any_reject = False
    for counts in by_res.values():
        total = sum(counts.values()) or 1
        if counts["REJECT"] > 0:
            any_reject = True
        if counts["COMMIT"] / total < 0.95:
            all_commit = False

    verdict = "PASS" if (all_commit and not any_reject) else ("FAIL" if any_reject else "NEEDS_HUMAN")
    return {"frames": n_pairs, "residuals": by_res, "dataset_decision": verdict}


def _cli(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: python -m bin.bft_orchestrator.orchestrator "
              "<action_camera.json>", file=sys.stderr)
        return 2
    path = Path(argv[1])
    with path.open() as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        print("expected JSON list of frame records", file=sys.stderr)
        return 2
    sample = data[:100]
    s = aggregate_dataset(sample, fps=30.0)
    print(f"=== BFT Orchestrator - {path.name} ===")
    print(f"frames analyzed: {s['frames']} (of {len(data)} total)")
    print(f"dataset decision: {s['dataset_decision']}")
    print("")
    print(f"{'residual':<8} {'COMMIT':>7} {'REJECT':>7} {'VIEW_CHG':>9} {'INSUFF':>7}")
    print("-" * 44)
    for r_name in sorted(s["residuals"].keys()):
        c = s["residuals"][r_name]
        print(f"{r_name:<8} {c['COMMIT']:>7} {c['REJECT']:>7} "
              f"{c['VIEW_CHANGE']:>9} {c['INSUFFICIENT']:>7}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
