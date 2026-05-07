"""Tests for V₃ R10 speed_max (zero-LLM physics oracle).

Generated 2026-05-06 via dispatch_qwen_to_minipc.sh.
Verified 10/10 PASS on minipc-bwdxs WSL pytest.
"""
import math

import pytest

from bin.v3_physics_oracle.r10_speed_max import r10_speed_max
from bin.v3_physics_oracle.residuals import Verdict


def test_walking_pass():
    r = r10_speed_max({"speed": [4.0, 0.0, 0.0]})
    assert r.verdict == Verdict.PASS
    assert r.residual == 0.0


def test_pythagorean_345_pass():
    """3-4-5 triangle: magnitude exactly 5."""
    r = r10_speed_max({"speed": [3.0, 4.0, 0.0]})
    assert r.verdict == Verdict.PASS


def test_sprint_jump_pass():
    r = r10_speed_max({"speed": [7.0, 0.0, 0.0]})
    assert r.verdict == Verdict.PASS


def test_elytra_abstain():
    """25 m/s is in elytra/rocket/horse range — V₃ ABSTAINs."""
    r = r10_speed_max({"speed": [25.0, 0.0, 0.0]})
    assert r.verdict == Verdict.ABSTAIN
    assert math.isnan(r.residual)


def test_teleport_fail():
    """100 m/s — no vanilla mechanic produces this magnitude."""
    r = r10_speed_max({"speed": [100.0, 0.0, 0.0]})
    assert r.verdict == Verdict.FAIL
    assert r.residual == pytest.approx(100.0 - 50.0)


def test_missing_field_abstain():
    """IL10: missing artifact must surface as ABSTAIN, never silent PASS."""
    r = r10_speed_max({})
    assert r.verdict == Verdict.ABSTAIN


def test_wrong_length_abstain():
    r = r10_speed_max({"speed": [1.0, 2.0]})
    assert r.verdict == Verdict.ABSTAIN


def test_non_numeric_abstain():
    r = r10_speed_max({"speed": [1.0, "fast", 0.0]})
    assert r.verdict == Verdict.ABSTAIN


def test_negative_components_pass():
    """Magnitude is direction-independent."""
    r = r10_speed_max({"speed": [-3.0, -4.0, 0.0]})
    assert r.verdict == Verdict.PASS


def test_ceiling_boundary_fail():
    """50.001 m/s > ABSOLUTE_CEIL=50 → FAIL."""
    r = r10_speed_max({"speed": [50.001, 0.0, 0.0]})
    assert r.verdict == Verdict.FAIL
