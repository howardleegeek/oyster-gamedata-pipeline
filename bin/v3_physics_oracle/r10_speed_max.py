"""V₃ Physics-Oracle R10 — speed magnitude hard ceiling.

Zero-LLM hardcoded ground truth: any |speed| > ABSOLUTE_CEIL is
physically impossible in vanilla Minecraft (teleport / cheat / encoding
error). Between NORMAL_MOVE_CEIL and ABSOLUTE_CEIL, V₃ ABSTAINs (could be
elytra+rocket / horse / cheat-mod — V₃ can't disambiguate without state).

Constants are textbook game-engine facts (BFT IL3 — independence proof
required to edit).

Generated 2026-05-06 via dispatch_qwen_to_minipc.sh + deepseek-v3.2,
verified 10/10 pytest, surgically integrated (note=None → "" for dataclass
compat with frozen=True OracleResult).
"""
from __future__ import annotations

import math

from bin.v3_physics_oracle.residuals import OracleResult, Verdict

# Minecraft vanilla movement upper bounds (1 block = 1 m)
WALK_SPEED       = 4.317   # m/s
SPRINT_SPEED     = 5.612   # m/s
SPRINT_JUMP      = 7.127   # m/s   (sprinting + bunny-hop measured)
HORSE_MAX        = 14.23   # m/s   (max-stat horse galloping)
NORMAL_MOVE_CEIL = 20.0    # m/s   above this = ABSTAIN (elytra/rocket/mod territory)
ABSOLUTE_CEIL    = 50.0    # m/s   HARD ceiling — above this = teleport/cheat/encoding


def r10_speed_max(rec: dict) -> OracleResult:
    """Validate frame's speed magnitude is within physical limits.

    Args:
        rec: per-frame record. Expected key 'speed' = list[float] length 3.

    Returns:
        OracleResult with verdict ∈ {PASS, FAIL, ABSTAIN}.
    """
    name = "r10_speed_max"
    expected = ABSOLUTE_CEIL

    # Rule 1: artifact-absent → ABSTAIN (IL10 honesty)
    if "speed" not in rec:
        return OracleResult(name, Verdict.ABSTAIN, expected, None, math.nan, "missing speed key")
    speed = rec["speed"]
    if not isinstance(speed, list) or len(speed) != 3:
        return OracleResult(name, Verdict.ABSTAIN, expected, None, math.nan, "malformed speed shape")
    try:
        x, y, z = float(speed[0]), float(speed[1]), float(speed[2])
    except (ValueError, TypeError):
        return OracleResult(name, Verdict.ABSTAIN, expected, None, math.nan, "non-numeric speed components")

    magnitude = math.sqrt(x * x + y * y + z * z)

    # Rule 2: above hard ceiling → FAIL
    if magnitude > ABSOLUTE_CEIL:
        return OracleResult(
            name, Verdict.FAIL, expected, magnitude,
            magnitude - ABSOLUTE_CEIL,
            f"|v|={magnitude:.3f} exceeds ABSOLUTE_CEIL={ABSOLUTE_CEIL}",
        )

    # Rule 3: between normal and ceiling → ABSTAIN (V₃ can't disambiguate)
    if magnitude > NORMAL_MOVE_CEIL:
        return OracleResult(
            name, Verdict.ABSTAIN, expected, magnitude, math.nan,
            f"|v|={magnitude:.3f} in elytra/horse/mod range",
        )

    # Otherwise → PASS
    return OracleResult(name, Verdict.PASS, expected, magnitude, 0.0, "")
