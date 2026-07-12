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
    # R10: zero-LLM speed magnitude oracle (Aliyun→minipc dispatched 2026-05-06,
    # 10/10 pytest, lives in r10_speed_max.py, re-exported via residuals.py).
    ("R10", "r10_speed_max", False, False),
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
    is_nan = isinstance(r.residual, float) and math.isnan(r.residual)
    val = math.nan if is_nan else float(r.residual)
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


def _abstain_vote(verifier: str, residual_name: str, note: str) -> bool:
    """Return True if a residual result represents an ABSTAIN.

    Both V₁'s ResidualResult and V₂'/V₂prime's dict use a ``ABSTAIN:`` note
    prefix to encode "I don't know" per IL10. The orchestrator drops these
    so they don't poison the tally as FAIL votes.
    """
    return isinstance(note, str) and note.startswith("ABSTAIN:")


def _r13_vote_v1(frame: dict, neighbor: dict | None,
                 inputs_path: str | Path | None) -> Vote | None:
    """Invoke V₁ R13 keycode-replay if module is loadable. Returns None on
    ABSTAIN so we don't pollute the tally."""
    mod = _load("bin.v1_claude_residuals.r13_keycode_replay")
    if mod is None or inputs_path is None:
        return None
    fn = getattr(mod, "r13_keycode_replay", None)
    if fn is None:
        return None
    try:
        r = fn(frame, neighbor, inputs_path)
    except Exception as exc:
        return Vote("V1", "R13", "FAIL", math.nan, 0.0,
                    f"{type(exc).__name__}: {exc}")
    note = getattr(r, "note", "") or ""
    if _abstain_vote("V1", "R13", note):
        return None
    return _to_vote_v1("R13", "V1", r)


def _r13_vote_v2prime(frame: dict, neighbor: dict | None,
                      inputs_path: str | Path | None) -> Vote | None:
    """Invoke V₂' GLM R13 keycode-replay (dict return shape)."""
    mod = _load("bin.v2prime_glm_residuals.residuals")
    if mod is None or inputs_path is None:
        return None
    fn = getattr(mod, "r13_keycode_replay", None)
    if fn is None:
        return None
    try:
        r = fn(frame, neighbor, str(inputs_path))
    except Exception as exc:
        return Vote("V2prime", "R13", "FAIL", math.nan, 0.0,
                    f"{type(exc).__name__}: {exc}")
    note = str(r.get("note", "")) if isinstance(r, dict) else ""
    if _abstain_vote("V2prime", "R13", note):
        return None
    return _to_vote_v2("R13", "V2prime", r)


def collect_votes(frame: dict, neighbor: dict | None = None,
                  fps: float = 30.0,
                  inputs_path: str | Path | None = None) -> list[Vote]:
    """Run all V1/V2/V3 (and V2' if present) residuals on the frame pair.

    Residuals needing neighbor (R03, R04, R05) are skipped if neighbor is
    None. Any verifier exception becomes a FAIL vote with evidence=str(exc):
    a TypeError raised by V2 R07 on scalar mouse_x is still detection.

    Multimodal residual R13 is invoked per-frame when ``inputs_path`` is
    provided (V₁ + V₂' both implement R13 with matching contracts).
    R15/R16 are dataset-level and are invoked from ``aggregate_dataset``.
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

    # R13 is per-frame multimodal: needs inputs.jsonl, applied here.
    if inputs_path is not None:
        v1_r13 = _r13_vote_v1(frame, neighbor, inputs_path)
        if v1_r13 is not None:
            votes.append(v1_r13)
        v2p_r13 = _r13_vote_v2prime(frame, neighbor, inputs_path)
        if v2p_r13 is not None:
            votes.append(v2p_r13)
    return votes


def _r15_dataset_vote(video_path: str | Path | None,
                      sample_rec: dict) -> Vote | None:
    """Invoke V₁ R15 fps-consistency once per dataset. Returns None on
    ABSTAIN. The residual reads ``rec['fps']`` so we pass any record."""
    if video_path is None:
        return None
    mod = _load("bin.v1_claude_residuals.r15_fps_consistency")
    if mod is None:
        return None
    fn = getattr(mod, "r15_fps_consistency", None)
    if fn is None:
        return None
    try:
        r = fn(sample_rec, str(video_path))
    except Exception as exc:
        return Vote("V1", "R15", "FAIL", math.nan, 0.0,
                    f"{type(exc).__name__}: {exc}")
    note = getattr(r, "note", "") or ""
    if _abstain_vote("V1", "R15", note):
        return None
    return _to_vote_v1("R15", "V1", r)


def _r16_dataset_vote(depth_dir: str | Path | None,
                      video_duration_sec: float | None,
                      sample_rec: dict) -> Vote | None:
    """Invoke V₁ R16 depth-count once per dataset."""
    if depth_dir is None:
        return None
    mod = _load("bin.v1_claude_residuals.r16_depth_count")
    if mod is None:
        return None
    fn = getattr(mod, "r16_depth_count", None)
    if fn is None:
        return None
    try:
        r = fn(sample_rec, str(depth_dir), video_duration_sec)
    except Exception as exc:
        return Vote("V1", "R16", "FAIL", math.nan, 0.0,
                    f"{type(exc).__name__}: {exc}")
    note = getattr(r, "note", "") or ""
    if _abstain_vote("V1", "R16", note):
        return None
    return _to_vote_v1("R16", "V1", r)


# R20a..R20e are dataset-level drift residuals returning ``DriftResult``.
# Their ABSTAIN signal lives on ``.detail`` (not ``.note``) but uses the same
# ``ABSTAIN:`` prefix per IL11. We adapt to a Vote uniformly.
_R20_REGISTRY = (
    ("R20a", "r20a_quat_norm_distribution"),
    ("R20b", "r20b_mouse_dx_cumulative"),
    ("R20c", "r20c_fps_jitter"),
    ("R20d", "r20d_speed_profile"),
    ("R20e", "r20e_yaw_turn_rate"),
)


def _r20_dataset_votes(records: list[dict]) -> list[Vote]:
    """Invoke all five R20 sub-residuals once per dataset.

    Returns at most 5 votes — ABSTAINs (DriftResult.detail starts with
    ``ABSTAIN:``) are filtered so they don't poison the tally.
    """
    out: list[Vote] = []
    if not records:
        return out
    mod = _load("bin.v1_claude_residuals.r20_drift")
    if mod is None:
        return out
    for residual_name, fn_name in _R20_REGISTRY:
        fn = getattr(mod, fn_name, None)
        if fn is None:
            continue
        try:
            r = fn(records)
        except Exception as exc:
            out.append(Vote("V1", residual_name, "FAIL", math.nan, 0.0,
                            f"{type(exc).__name__}: {exc}"))
            continue
        detail = getattr(r, "detail", "") or ""
        if isinstance(detail, str) and detail.startswith("ABSTAIN:"):
            continue
        verdict = "PASS" if getattr(r, "passed", False) else "FAIL"
        sample_stat = getattr(r, "sample_stat", 0.0)
        threshold = getattr(r, "threshold", 0.0)
        try:
            sval = float(sample_stat)
        except (TypeError, ValueError):
            sval = math.nan
        out.append(Vote("V1", residual_name, verdict, sval,
                        float(threshold), str(detail)))
    return out


def _r22_dataset_vote(depth_dir: str | Path | None,
                      depth_manifest_path: str | Path | None,
                      sample_rec: dict) -> Vote | None:
    """Invoke V₁ R22 depth-content SHA-256 verifier once per dataset.

    R22 needs both ``depth_dir`` and a manifest path — either being None
    yields ABSTAIN inside the residual itself; we filter that out so a
    caller running without aux args sees no R22 entry at all.
    """
    if depth_dir is None or depth_manifest_path is None:
        return None
    mod = _load("bin.v1_claude_residuals.r22_depth_hash")
    if mod is None:
        return None
    fn = getattr(mod, "r22_depth_hash", None)
    if fn is None:
        return None
    try:
        r = fn(sample_rec, None, str(depth_dir), str(depth_manifest_path))
    except Exception as exc:
        return Vote("V1", "R22", "FAIL", math.nan, 0.0,
                    f"{type(exc).__name__}: {exc}")
    note = getattr(r, "note", "") or ""
    if _abstain_vote("V1", "R22", note):
        return None
    return _to_vote_v1("R22", "V1", r)


def _r23_dataset_vote(video_path: str | Path | None,
                      sample_rec: dict) -> Vote | None:
    """Invoke V₁ R23 video-codec verifier once per dataset."""
    if video_path is None:
        return None
    mod = _load("bin.v1_claude_residuals.r23_video_codec")
    if mod is None:
        return None
    fn = getattr(mod, "r23_video_codec", None)
    if fn is None:
        return None
    try:
        r = fn(sample_rec, None, str(video_path))
    except Exception as exc:
        return Vote("V1", "R23", "FAIL", math.nan, 0.0,
                    f"{type(exc).__name__}: {exc}")
    note = getattr(r, "note", "") or ""
    if _abstain_vote("V1", "R23", note):
        return None
    return _to_vote_v1("R23", "V1", r)


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
        elif p >= 2 * f + 1 and p > fl or p > fl and p + fl >= f + 1:
            decision = "COMMIT"
        else:
            decision = "INSUFFICIENT"
        out[r_name] = {"passed": p, "failed": fl, "abstain": ab,
                       "decision": decision}
    return out


def aggregate_dataset(records: list[dict], fps: float = 30.0,
                      inputs_path: str | Path | None = None,
                      video_path: str | Path | None = None,
                      depth_dir: str | Path | None = None,
                      video_duration_sec: float | None = None,
                      depth_manifest_path: str | Path | None = None) -> dict[str, Any]:
    """Run collect_votes + tally on each frame pair (n, n+1).

    Multimodal extras (all backward-compatible — None = unchanged behavior):

    * ``inputs_path``: when set, V₁ + V₂' R13 keycode-replay is invoked per
      frame and aggregated into the residuals dict.
    * ``video_path``: when set, V₁ R15 fps-consistency AND V₁ R23
      video-codec residuals are invoked ONCE for the whole dataset
      (ffprobe is dataset-level).
    * ``depth_dir`` + ``video_duration_sec``: when both set, V₁ R16
      depth-count is invoked ONCE for the whole dataset. If
      ``video_duration_sec`` is None, it is derived from frame count
      (``len(records) / fps``) so callers don't have to re-probe.
    * ``depth_manifest_path``: when set together with ``depth_dir``, V₁
      R22 SHA-256 manifest binding is invoked ONCE per dataset.
    * R20a..R20e (statistical drift) are always attempted on the full
      records list — they self-ABSTAIN when the sample is too small.
    """
    n_pairs = max(len(records) - 1, 0)
    by_res: dict[str, dict[str, int]] = {}
    for i in range(n_pairs):
        votes = collect_votes(records[i], records[i + 1], fps=fps,
                              inputs_path=inputs_path)
        for r_name, info in tally(votes).items():
            bucket = by_res.setdefault(r_name, {
                "COMMIT": 0, "REJECT": 0, "VIEW_CHANGE": 0, "INSUFFICIENT": 0
            })
            bucket[info["decision"]] += 1

    # Dataset-level residuals (R15, R16, R20a..e, R22, R23) — fire once each.
    sample_rec = records[0] if records else {}
    dataset_votes: list[Vote] = []
    if video_path is not None:
        v = _r15_dataset_vote(video_path, sample_rec)
        if v is not None:
            dataset_votes.append(v)
    if depth_dir is not None:
        duration = video_duration_sec
        if duration is None and records and fps > 0:
            duration = float(len(records)) / float(fps)
        v = _r16_dataset_vote(depth_dir, duration, sample_rec)
        if v is not None:
            dataset_votes.append(v)
    # R20a..R20e drift — always attempted; ABSTAIN-filtered inside helper.
    dataset_votes.extend(_r20_dataset_votes(records))
    # R22 manifest hash — needs both depth_dir and manifest path.
    v = _r22_dataset_vote(depth_dir, depth_manifest_path, sample_rec)
    if v is not None:
        dataset_votes.append(v)
    # R23 video codec — needs video_path.
    v = _r23_dataset_vote(video_path, sample_rec)
    if v is not None:
        dataset_votes.append(v)
    if dataset_votes:
        for r_name, info in tally(dataset_votes).items():
            bucket = by_res.setdefault(r_name, {
                "COMMIT": 0, "REJECT": 0, "VIEW_CHANGE": 0, "INSUFFICIENT": 0
            })
            bucket[info["decision"]] += 1

    all_commit = True
    any_reject = False
    for counts in by_res.values():
        total = sum(counts.values()) or 1
        if counts["REJECT"] > 0:
            any_reject = True
        if counts["COMMIT"] / total < 0.95:
            all_commit = False

    verdict = "PASS" if (all_commit and not any_reject) else (
        "FAIL" if any_reject else "NEEDS_HUMAN"
    )
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
