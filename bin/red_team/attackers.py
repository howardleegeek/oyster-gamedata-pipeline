"""Red Team attackers — buckets B (multi-field), C (sub-threshold), D (temporal).

Bucket A (single-field) lives in bft_adversarial_harness.py for historical
continuity. This module adds 15 more attacks targeting BFT architectural
limits beyond simple mutations.

Each attacker takes (baseline_frame, neighbor_frame) and returns an
``AttackResult`` describing the mutated input plus metadata. The Blue
team scoring harness uses ``ATTACK_CATALOG`` to iterate all attacks.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class AttackResult:
    """One mutated input ready to feed into BFT verifiers."""
    fi_id: str
    bucket: str  # "A" | "B" | "C" | "D"
    label: str
    severity: str  # "critical" | "high" | "medium" | "low"
    mutated_rec: dict
    mutated_neighbor: dict | None
    expected_residuals: tuple[str, ...]  # which residuals SHOULD catch
    notes: str = ""


@dataclass(frozen=True)
class AttackCase:
    fi_id: str
    bucket: str
    label: str
    severity: str
    expected_residuals: tuple[str, ...]
    mutator: Callable[[dict, dict], AttackResult]


# ---------------------------------------------------------------------------
# Bucket B — multi-field coordinated mutations
# ---------------------------------------------------------------------------

def _b01_oula_quat_consistent_swap(rec: dict, neighbor: dict) -> AttackResult:
    """Mutate oula AND quat to a different SELF-CONSISTENT Hamilton pair.

    R02 (oula↔quat) passes because both fields lie. Catches: R03 if pos
    not updated to match (kinematics breaks); R13 if inputs say W but
    new yaw doesn't match expected camera turn.
    """
    new = copy.deepcopy(rec)
    # Original was yaw=90°. Mutate to yaw=180° + matching quat.
    new["camera_rotation_oula"] = [0.0, 180.0, 0.0]
    # Hamilton: yaw=180° → q = (0, sin(90°), 0, cos(90°)) = (0, 1, 0, 0)
    new["camera_rotation_quaternion"] = [0.0, 1.0, 0.0, 0.0]
    # leave position + speed alone — kinematics won't match new orientation
    # but R03 only checks pos/speed/fps relationship, not pos/orientation
    return AttackResult(
        fi_id="B-01",
        bucket="B",
        label="oula+quat swap to self-consistent Hamilton pair (yaw 90→180)",
        severity="high",
        mutated_rec=new,
        mutated_neighbor=neighbor,
        expected_residuals=("R13",),  # only multimodal can detect
        notes="R02 passes by design (Hamilton consistent); only R13 multimodal catches",
    )


def _b02_pos_speed_consistent_scale(rec: dict, neighbor: dict) -> AttackResult:
    """Scale position AND speed by 2× — R03 (kinematics) still passes."""
    new = copy.deepcopy(rec)
    new_neighbor = copy.deepcopy(neighbor)
    cp = new["camera_position"]
    np_ = new_neighbor["camera_position"]
    new["camera_position"] = [c * 2.0 for c in cp]
    new_neighbor["camera_position"] = [c * 2.0 for c in np_]
    cs = new["camera_speed"]
    new["camera_speed"] = [c * 2.0 for c in cs]
    return AttackResult(
        fi_id="B-02",
        bucket="B",
        label="pos+speed both ×2 (R03 kinematics still satisfied)",
        severity="medium",
        mutated_rec=new,
        mutated_neighbor=new_neighbor,
        expected_residuals=("R10",),  # speed cap might catch if scaled high enough
        notes="R03 passes by construction; R10 catches only if v_max exceeded",
    )


def _b03_keycode_W_to_B_full_swap(rec: dict, neighbor: dict) -> AttackResult:
    """Swap W→B in keyCode AND inputs.jsonl simultaneously. Defeats R13
    if inputs is also faked. Only buyer-signed V₄ reference catches."""
    new = copy.deepcopy(rec)
    # In test, inputs.jsonl will be set by harness to also contain B
    new["keyCode"] = [88]
    return AttackResult(
        fi_id="B-03",
        bucket="B",
        label="keyCode + inputs.jsonl coordinated W→B swap",
        severity="critical",
        mutated_rec=new,
        mutated_neighbor=neighbor,
        expected_residuals=(),  # NONE — this is V₄ buyer-signed scope
        notes="V₁..V₃ all fooled; only V₄ buyer-byte-diff or video-frame OCR could detect",
    )


def _b04_time_fps_proportional_dilate(rec: dict, neighbor: dict) -> AttackResult:
    """Dilate timestamps uniformly (×1.5) — single-frame R05 sees correct
    dt for stated fps. R15 ffprobe video.fps cross-check would catch."""
    new = copy.deepcopy(rec)
    new_neighbor = copy.deepcopy(neighbor)
    # If original was 30fps with dt=33ms, attacker writes dt=50ms but
    # also fakes fps=20 in the field — internally consistent.
    new["fps"] = 20.0
    new_neighbor["fps"] = 20.0
    new_neighbor["time"] = "2026-05-05 19:30:00.050"  # 50ms after 0.000
    return AttackResult(
        fi_id="B-04",
        bucket="B",
        label="time+fps proportionally dilated (internally consistent)",
        severity="high",
        mutated_rec=new,
        mutated_neighbor=new_neighbor,
        expected_residuals=("R12", "R15"),  # R12 catches fps≠30, R15 cross-checks ffprobe
        notes="R05 self-consistent; only R12 (fps range) + R15 (vs video) catch",
    )


def _b05_dataset_replay_splice(rec: dict, neighbor: dict) -> AttackResult:
    """Splice clip A's action_camera with clip B's video. Each individual
    frame internally valid; only multimodal cross-check (R15 fps, R16
    depth count, V₄ frame-OCR) catches — *unless* the recorder stamps a
    session_id, in which case R18 trips immediately on the bound
    manifest.

    The action_camera stream from session B is concatenated under a
    manifest claiming session A. Mutate ``session_id`` to a *different*
    UUID to model the splice: every spliced frame now carries the foreign
    session token while ``session_manifest.json`` still says
    ``AAA-canonical-uuid-1``.
    """
    new = copy.deepcopy(rec)
    new["session_id"] = "BBB-different-uuid-2"
    return AttackResult(
        fi_id="B-05",
        bucket="B",
        label="full-dataset replay splice (action_camera A + video B)",
        severity="critical",
        mutated_rec=new,
        mutated_neighbor=neighbor,
        # R18 binds frame ↔ manifest; R15/R16 stay listed for the
        # legacy multimodal path even though R18 alone now suffices.
        expected_residuals=("R18", "R15", "R16"),
        notes=(
            "session_id swapped to foreign UUID; R18 catches immediately. "
            "R15+R16 retained as legacy multimodal backup."
        ),
    )


# ---------------------------------------------------------------------------
# Bucket C — sub-threshold perturbations
# ---------------------------------------------------------------------------

def _c01_quat_norm_just_under_threshold(rec: dict, neighbor: dict) -> AttackResult:
    new = copy.deepcopy(rec)
    q = list(new["camera_rotation_quaternion"])
    # Scale so ‖q‖² = 1 + 5e-7 (just under R01 threshold 1e-6)
    eps = 5e-7
    scale = math.sqrt(1.0 + eps)
    new["camera_rotation_quaternion"] = [c * scale for c in q]
    return AttackResult(
        fi_id="C-01",
        bucket="C",
        label="quaternion norm 1 + 5e-7 (R01 threshold 1e-6)",
        severity="low",
        mutated_rec=new,
        mutated_neighbor=neighbor,
        expected_residuals=("R20a",),  # R20 distribution-level drift
        notes="single-frame undetectable; R20a catches via dataset-level drift",
    )


def _c02_mouse_dx_subepsilon(rec: dict, neighbor: dict) -> AttackResult:
    new = copy.deepcopy(rec)
    new_neighbor = copy.deepcopy(neighbor)
    # Add 5e-7 (under R04 threshold 1e-6) to mouse_dx
    dx = new_neighbor.get("mouse_dx", [0.0])
    new_neighbor["mouse_dx"] = [dx[0] + 5e-7]
    return AttackResult(
        fi_id="C-02",
        bucket="C",
        label="mouse_dx + 5e-7 (R04 threshold 1e-6)",
        severity="low",
        mutated_rec=new,
        mutated_neighbor=new_neighbor,
        expected_residuals=("R20b",),
        notes="cumulative drift over 9000 frames = 4.5e-3 — R20b catches in aggregate",
    )


def _c03_fps_at_lower_bound(rec: dict, neighbor: dict) -> AttackResult:
    new = copy.deepcopy(rec)
    new["fps"] = 29.51  # R12 [29.5, 30.5] just inside
    return AttackResult(
        fi_id="C-03",
        bucket="C",
        label="fps=29.51 (R12 lower bound 29.5 + ε)",
        severity="low",
        mutated_rec=new,
        mutated_neighbor=neighbor,
        expected_residuals=("R20c",),
        notes="boundary case — R20c fps-jitter drift catches declared-vs-actual mismatch",
    )


def _c04_pitch_180p001_overshoot(rec: dict, neighbor: dict) -> AttackResult:
    new = copy.deepcopy(rec)
    new["camera_rotation_oula"] = [180.001, 0.0, 0.0]
    # Quat won't match — but C-04 tests ONLY R06 (range)
    return AttackResult(
        fi_id="C-04",
        bucket="C",
        label="pitch=180.001° (R06 [-180, 180] just over)",
        severity="medium",
        mutated_rec=new,
        mutated_neighbor=neighbor,
        expected_residuals=("R06", "R02"),  # R06 range + R02 inconsistency
        notes="boundary — strict R06 catches; R02 also catches due to oula/quat mismatch",
    )


def _c05_speed_just_under_v_max(rec: dict, neighbor: dict) -> AttackResult:
    new = copy.deepcopy(rec)
    # Set speed magnitude to 49.99 (just under R10 default 50 m/s)
    new["camera_speed"] = [49.99, 0.0, 0.0]
    return AttackResult(
        fi_id="C-05",
        bucket="C",
        label="speed=49.99 m/s (R10 v_max 50 — physically absurd but slips R10)",
        severity="medium",
        mutated_rec=new,
        mutated_neighbor=neighbor,
        expected_residuals=("R03", "R20d"),  # kinematics + drift profile
        notes="R10 lets it through; R03 catches via Δpos mismatch, R20d via dataset μ_speed",
    )


# ---------------------------------------------------------------------------
# Bucket D — replay / temporal attacks
# ---------------------------------------------------------------------------

def _d01_timestamp_skip(rec: dict, neighbor: dict) -> AttackResult:
    new = copy.deepcopy(rec)
    new_neighbor = copy.deepcopy(neighbor)
    # Original dt 33ms → attacker writes dt = 66ms (skipped a frame)
    new_neighbor["time"] = "2026-05-05 19:30:00.066"
    return AttackResult(
        fi_id="D-01",
        bucket="D",
        label="timestamp jump 33ms→66ms (skip frame in time)",
        severity="high",
        mutated_rec=new,
        mutated_neighbor=new_neighbor,
        expected_residuals=("R05",),
        notes="R05 dt deviation > 5ms threshold → catches",
    )


def _d02_frame_index_reorder(rec: dict, neighbor: dict) -> AttackResult:
    new = copy.deepcopy(rec)
    new_neighbor = copy.deepcopy(neighbor)
    # Swap frame indices: rec.frame=5, neighbor.frame=4 (out of order)
    new["frame"] = 5
    new_neighbor["frame"] = 4
    return AttackResult(
        fi_id="D-02",
        bucket="D",
        label="frame index reordered (5 then 4)",
        severity="medium",
        mutated_rec=new,
        mutated_neighbor=new_neighbor,
        expected_residuals=(),  # NO existing residual checks monotonicity
        notes="ARCHITECTURAL GAP — need new residual: monotonic frame index",
    )


def _d03_inputs_jsonl_truncated(rec: dict, neighbor: dict) -> AttackResult:
    """Tester would normally send full inputs.jsonl. Attacker truncates
    after frame 1000 — R13 will ABSTAIN for frames 1000+ (no events)."""
    new = copy.deepcopy(rec)
    return AttackResult(
        fi_id="D-03",
        bucket="D",
        label="inputs.jsonl truncated mid-session",
        severity="medium",
        mutated_rec=new,
        mutated_neighbor=neighbor,
        expected_residuals=(),  # ABSTAIN ≠ FAIL — silent gap
        notes="ABSTAIN-tainted: should R13 escalate to FAIL when full dataset has truncation?",
    )


def _d04_depth_files_shuffled(rec: dict, neighbor: dict) -> AttackResult:
    """Depth/*.exr count correct but content shuffled across sessions.

    Models the same Frankenstein-splice family as B-05: the depth
    directory is borrowed from a different recording session, which the
    recorder would have stamped with its own session_id. Mutating
    ``session_id`` here lets us prove R18 is *cross-applicable* and
    closes one more critical-severity gap on the way.
    """
    new = copy.deepcopy(rec)
    new["session_id"] = "CCC-different-uuid-3"
    return AttackResult(
        fi_id="D-04",
        bucket="D",
        label="depth/*.exr count correct but content shuffled",
        severity="critical",
        mutated_rec=new,
        mutated_neighbor=neighbor,
        expected_residuals=("R18", "R22"),  # session_id mismatch + per-file hash
        notes=(
            "Cross-session shuffled depth ⇒ session_id differs from "
            "manifest (R18) AND per-file SHA-256 hashes mismatch (R22)."
        ),
    )


def _d05_video_codec_mismatch(rec: dict, neighbor: dict) -> AttackResult:
    new = copy.deepcopy(rec)
    return AttackResult(
        fi_id="D-05",
        bucket="D",
        label="video.mp4 transcoded with H.264 instead of H.265",
        severity="medium",
        mutated_rec=new,
        mutated_neighbor=neighbor,
        expected_residuals=("R23",),  # codec/format binding
        notes="R23 ffprobe codec check catches H.264 (PRD requires HEVC)",
    )


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

bucket_b_attacks: tuple[AttackCase, ...] = (
    AttackCase("B-01", "B", "oula+quat self-consistent swap", "high",
               ("R13", "R24"), _b01_oula_quat_consistent_swap),
    AttackCase("B-02", "B", "pos+speed ×2 coordinated", "medium",
               ("R10",), _b02_pos_speed_consistent_scale),
    AttackCase("B-03", "B", "keyCode + inputs.jsonl W→B coordinated", "critical",
               ("R24",), _b03_keycode_W_to_B_full_swap),
    AttackCase("B-04", "B", "time+fps proportional dilate", "high",
               ("R12", "R15"), _b04_time_fps_proportional_dilate),
    AttackCase("B-05", "B", "dataset replay splice (AC[A] + video[B])", "critical",
               ("R18", "R15", "R16", "R24"), _b05_dataset_replay_splice),
)

bucket_c_attacks: tuple[AttackCase, ...] = (
    AttackCase("C-01", "C", "quat norm 1 + 5e-7 (sub-R01-threshold)", "low",
               ("R20a",), _c01_quat_norm_just_under_threshold),
    AttackCase("C-02", "C", "mouse_dx +5e-7 (sub-R04-threshold)", "low",
               ("R20b",), _c02_mouse_dx_subepsilon),
    AttackCase("C-03", "C", "fps=29.51 (R12 lower-bound)", "low",
               ("R20c",), _c03_fps_at_lower_bound),
    AttackCase("C-04", "C", "pitch=180.001 (R06 over)", "medium",
               ("R06", "R02"), _c04_pitch_180p001_overshoot),
    AttackCase("C-05", "C", "speed=49.99 (R10 just under)", "medium",
               ("R03", "R20d"), _c05_speed_just_under_v_max),
)

bucket_d_attacks: tuple[AttackCase, ...] = (
    AttackCase("D-01", "D", "timestamp 33→66ms skip", "high",
               ("R05",), _d01_timestamp_skip),
    AttackCase("D-02", "D", "frame index out-of-order", "medium",
               (), _d02_frame_index_reorder),
    AttackCase("D-03", "D", "inputs.jsonl truncated", "medium",
               (), _d03_inputs_jsonl_truncated),
    AttackCase("D-04", "D", "depth content shuffled", "critical",
               ("R18", "R22"), _d04_depth_files_shuffled),
    AttackCase("D-05", "D", "video codec mismatch", "medium",
               ("R23",), _d05_video_codec_mismatch),
)

ATTACK_CATALOG: tuple[AttackCase, ...] = (
    *bucket_b_attacks, *bucket_c_attacks, *bucket_d_attacks,
)
