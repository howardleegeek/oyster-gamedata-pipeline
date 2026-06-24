"""D18 tests — D5 action_camera classifier 3-tier classification.

Howard 2026-05-07: validates that the new tier-0 ``_real_game_state``
flag tier-jumps the classifier above the variance-based tiers, so
real-but-stationary mod-driven sessions are no longer mis-classified
as placeholder.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Load tarball_authenticity_check as a module (bin/ isn't a package).
_spec = importlib.util.spec_from_file_location(
    "tarball_authenticity_check",
    REPO_ROOT / "bin" / "tarball_authenticity_check.py",
)
tac = importlib.util.module_from_spec(_spec)
sys.modules["tarball_authenticity_check"] = tac
_spec.loader.exec_module(tac)


def _write_action_camera(tmp_path: Path, records: list[dict]) -> Path:
    p = tmp_path / "action_camera.json"
    p.write_text(json.dumps(records))
    return p


def test_tier0_real_game_state_flag_classifies_REAL(tmp_path):
    """When _real_game_state=True is present in any record, that's the
    canonical proof of mod-driven real data — short-circuit to REAL."""
    records = [
        {
            "frame": i,
            "_real_game_state": True,
            "camera_position": [0.0, 64.0, 0.0],  # stationary - would normally fail tier 2
            "player_position": [0.0, 64.0, 0.0],
        }
        for i in range(100)
    ]
    p = _write_action_camera(tmp_path, records)
    state, evidence = tac._classify_action_camera(p)
    assert state == tac.REAL
    assert "_real_game_state" in evidence
    assert "mod-driven" in evidence


def test_tier1_is_padded_flag_classifies_REAL(tmp_path):
    records = [
        {"frame": i, "is_padded": False, "camera_position": [i, 64, 0]} for i in range(20)
    ] + [{"frame": i, "is_padded": True, "camera_position": [0, 64, 0]} for i in range(20, 100)]
    p = _write_action_camera(tmp_path, records)
    state, evidence = tac._classify_action_camera(p)
    assert state == tac.REAL
    assert "is_padded" in evidence


def test_tier2_fingerprint_variance_classifies_REAL(tmp_path):
    """No flags, but record fingerprints vary → REAL."""
    records = [
        {
            "frame": i,
            "camera_position": [i * 0.1, 64.0, 0.0],
            "camera_rotation_oula": [0, i, 0],
            "yaw": float(i),
            "pitch": 0.0,
        }
        for i in range(100)
    ]
    p = _write_action_camera(tmp_path, records)
    state, evidence = tac._classify_action_camera(p)
    assert state == tac.REAL
    assert "fingerprint" in evidence


def test_no_flags_and_low_variance_classifies_PLACEHOLDER(tmp_path):
    """No flags, all fingerprints identical → PLACEHOLDER."""
    records = [
        {
            "frame": i,
            "camera_position": [0.0, 64.0, 0.0],
            "camera_rotation_oula": [0, 0, 0],
            "yaw": 0.0,
            "pitch": 0.0,
        }
        for i in range(100)
    ]
    p = _write_action_camera(tmp_path, records)
    state, evidence = tac._classify_action_camera(p)
    assert state == tac.PLACEHOLDER
    assert "mostly identical" in evidence


def test_string_real_game_state_does_NOT_classify_REAL(tmp_path):
    """Type-confusion defence — only boolean True qualifies for tier 0."""
    records = [
        {
            "frame": i,
            "_real_game_state": "true",  # string, not bool — must NOT pass tier 0
            "camera_position": [0.0, 64.0, 0.0],
        }
        for i in range(100)
    ]
    p = _write_action_camera(tmp_path, records)
    state, evidence = tac._classify_action_camera(p)
    # Falls through tier 0 (because is True check fails on string), no
    # is_padded flag, identical fingerprints → PLACEHOLDER.
    assert state == tac.PLACEHOLDER, (
        f"string '_real_game_state' should NOT pass tier 0 — got {state} with evidence: {evidence}"
    )


def test_tier0_takes_priority_over_tier2(tmp_path):
    """If both _real_game_state AND fingerprint variance are present, the
    explicit flag wins (more authoritative + shorter scan)."""
    records = [
        {
            "frame": i,
            "_real_game_state": True,
            "camera_position": [i * 0.1, 64.0, 0.0],
            "yaw": float(i),
        }
        for i in range(50)
    ]
    p = _write_action_camera(tmp_path, records)
    state, evidence = tac._classify_action_camera(p)
    assert state == tac.REAL
    assert "_real_game_state" in evidence
    assert "fingerprint" not in evidence  # tier 2 NOT consulted
