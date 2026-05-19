"""V₃ — Physics-table Oracle (zero-LLM hardcoded ground truth).

🟢 GREEN node in BFT N=4, f=1 system. Distinguishing properties:
1. Zero LLM dependency (no API calls, no LLM-generated code paths)
2. Hand-tabulated values from textbook Hamilton axis-angle math
3. ABSTAIN (not PASS/FAIL) for inputs outside the lookup table
4. Independent of V₁ Claude / V₂ MiniMax / producer source

Anchor sources (NOT a PRD prose read):
- Hamilton quaternion: q = (v_x·sin(θ/2), v_y·sin(θ/2), v_z·sin(θ/2), cos(θ/2))
- Reference: Dunn & Parberry, "3D Math Primer for Graphics and Game Development"
- Reference: Wikipedia "Quaternions and spatial rotation" (math, not PRD)

V₃ is the BFT tiebreaker for V₁/V₂ disagreements. When all 4 nodes vote and
≥3 agree, dataset commits. When V₁ and V₂ disagree on a residual that V₃ has
in its table, V₃'s vote breaks the tie. Outside V₃'s table → ABSTAIN.

IRON LAW IL3 enforced: this module imports nothing from V₁/V₂/V₄/producer.
"""
from .residuals import (
    Verdict,
    OracleResult,
    r02_oula_quat_table,
    r08_intrinsics_symmetric,
    r09_keycode_vk_known,
    r12_fps_fixed_30,
    r07_mouse_range_strict,
    r01_quat_unit_norm,
)

__all__ = [
    "Verdict",
    "OracleResult",
    "r02_oula_quat_table",
    "r08_intrinsics_symmetric",
    "r09_keycode_vk_known",
    "r12_fps_fixed_30",
    "r07_mouse_range_strict",
    "r01_quat_unit_norm",
]
