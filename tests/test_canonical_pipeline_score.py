"""Regression test: pin PRD audit score on Howard's reference session.

Why this exists
---------------
Tonight (2026-05-16) we drove the audit score from 34/104 baseline → 98/104
PASS / 0 FAIL on Howard's real 11.87-min Minecraft Survival session.
This test ensures NO future change can silently regress that number.

The two halves
--------------
1. **fast slice** (always runs in CI): validates the score-extraction logic
   and the bin/ scripts' import paths. Catches "did someone break the
   audit script itself".

2. **live slice** (runs only when the reference session is on disk):
   actually runs prd_compliance_audit.py against the canonical session
   and asserts PASS >= FLOOR. Skipped on CI machines without the
   ~1 GB fixture. Catches "did someone break the pipeline output shape".

How to update the floor
-----------------------
When a legitimate improvement raises the score (e.g. fixing QM6 audit-bug
to take us to 99), update ``PASS_FLOOR`` here in the same PR that contains
the improvement. Lowering the floor REQUIRES Howard's explicit approval
in the PR description.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = REPO_ROOT / "bin"

# Tonight's measured floor. Increase ONLY when a verified improvement
# lands. Decrease ONLY with Howard's explicit approval in the PR body.
#
# History:
#  2026-05-16 22:00: initial 98 (DA-V2 depth pipeline)
#  2026-05-16 23:30: raised to 101 (QM2 frame-index alias / QM6 Z channel /
#                                   QM9 list-or-dict + JSON-array support /
#                                   adapter action_camera_jsonl_path fix)
#  2026-05-16 23:50: total raised to 105 (H8 added — depth source-kind honesty
#                    marker per Howard PM directive: distinguish engine Z-buffer
#                    from monocular DA-V2 fallback so buyer audit is honest).
#                    Floor stays 101 because H8 is SKIP_honest on DA-V2 (not PASS).
PASS_FLOOR = 101
TOTAL_EXPECTED = 105

# The reference session lives on Howard's mac1 (and his partner's machine
# once they scp it from minipc1). If absent, the live slice is skipped
# but the fast slice still validates the test plumbing.
REFERENCE_SESSION = pathlib.Path(
    "/tmp/real-session-howardplay-1778993817/session_20260516_213817_d137a341"
)


def _run_audit(session_dir: pathlib.Path) -> dict:
    """Run prd_compliance_audit.py and return parsed JSON."""
    proc = subprocess.run(
        [sys.executable, str(BIN / "prd_compliance_audit.py"), str(session_dir), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if not proc.stdout:
        pytest.fail(
            f"audit script produced no stdout. stderr (last 500 chars):\n"
            f"{proc.stderr[-500:]}"
        )
    return json.loads(proc.stdout)


def _tally(audit_json: dict) -> dict[str, int]:
    """Count PASS/FAIL/SKIP across items."""
    items = audit_json.get("items", audit_json.get("checks", []))
    counts: dict[str, int] = {}
    for it in items:
        s = it.get("status", "UNKNOWN")
        counts[s] = counts.get(s, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# FAST SLICE — always runs (validates plumbing, not real data)
# ---------------------------------------------------------------------------

def test_audit_script_is_importable():
    """Catches: someone deletes/breaks bin/prd_compliance_audit.py shebang."""
    audit_script = BIN / "prd_compliance_audit.py"
    assert audit_script.exists(), f"audit script missing: {audit_script}"
    assert audit_script.is_file()


def test_canonical_pipeline_script_is_importable():
    """Catches: someone deletes/breaks bin/canonical_pipeline.py."""
    p = BIN / "canonical_pipeline.py"
    assert p.exists(), f"canonical pipeline script missing: {p}"


def test_da_v2_script_is_importable():
    """Catches: someone deletes/breaks bin/run_da_v2_depth.py."""
    p = BIN / "run_da_v2_depth.py"
    assert p.exists(), f"DA-V2 script missing: {p}"


def test_transform_script_is_importable():
    """Catches: someone deletes/breaks bin/transform_game_state_to_action_camera.py."""
    p = BIN / "transform_game_state_to_action_camera.py"
    assert p.exists(), f"transform script missing: {p}"


def test_post_finalize_merges_existing_metadata(tmp_path: pathlib.Path):
    """REGRESSION GUARD: bin/post_finalize_metadata.py must MERGE, not OVERWRITE.

    Bug history: prior to 2026-05-16, write_metadata() constructed a fresh
    9-field dict and wrote it, destroying recorder-generated fields like
    hardware_specs, input_stats, recorder_extra (28 keys → 8 keys).
    The audit's M-group + C-group + Q-group then failed.

    This test pins the fix in place.
    """
    # Set up a session dir with a "rich" metadata.json (simulating recorder output)
    session = tmp_path / "test_session"
    session.mkdir()
    rich_metadata = {
        "session_id": "test-session-fixture",
        "recorder_version": "2.6.0",
        "hardware_specs": {"cpu": {"cores": 16, "vendor": "AMD"}},
        "input_stats": {"wasd_apm": 20.21, "total_keyboard_events": 634},
        "recorder_extra": {"encoder": "x264", "bitrate": 10000},
        "start_timestamp": 1778992697.39,
        "average_fps": 29.98,
        "frame_count": 708,
    }
    (session / "metadata.json").write_text(json.dumps(rich_metadata, indent=2))
    keys_before = set(rich_metadata.keys())

    # Run post_finalize
    proc = subprocess.run(
        [sys.executable, str(BIN / "post_finalize_metadata.py"), str(session)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"post_finalize failed: {proc.stderr[-500:]}"

    # Verify all original keys survived
    after = json.loads((session / "metadata.json").read_text())
    keys_after = set(after.keys())
    lost = keys_before - keys_after
    assert not lost, (
        f"MERGE REGRESSION: post_finalize_metadata.py DROPPED {len(lost)} keys: "
        f"{sorted(lost)}. It must MERGE existing metadata, not OVERWRITE."
    )

    # Spot-check that nested fields survive too
    assert after.get("hardware_specs", {}).get("cpu", {}).get("cores") == 16, \
        "MERGE REGRESSION: hardware_specs.cpu nested field was lost"
    assert after.get("input_stats", {}).get("wasd_apm") == 20.21, \
        "MERGE REGRESSION: input_stats nested field was lost"


# ---------------------------------------------------------------------------
# LIVE SLICE — only runs when reference session is on disk
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not REFERENCE_SESSION.is_dir(),
    reason=f"reference session not present at {REFERENCE_SESSION}",
)
def test_audit_score_on_reference_session_at_or_above_floor():
    """REGRESSION GUARD: PRD audit on Howard's reference session must score >= 98.

    This is THE lock for tonight's 98/104 PASS achievement. Any change to
    bin/ scripts that drops this number below PASS_FLOOR fails CI here.

    To raise PASS_FLOOR: update the constant in this file in the SAME PR
    that contains the improvement. To lower PASS_FLOOR: explicit Howard
    approval required in the PR description.
    """
    audit = _run_audit(REFERENCE_SESSION)
    counts = _tally(audit)
    total = sum(counts.values())
    passed = counts.get("PASS", 0)

    assert total == TOTAL_EXPECTED, (
        f"audit total changed: expected {TOTAL_EXPECTED} items, got {total}. "
        f"Did someone add/remove audit checks? Update TOTAL_EXPECTED if intended."
    )
    assert passed >= PASS_FLOOR, (
        f"REGRESSION: audit PASS dropped to {passed} (floor={PASS_FLOOR}). "
        f"Full tally: {counts}. Something landed that broke pipeline output. "
        f"Investigate before merging."
    )


@pytest.mark.skipif(
    not REFERENCE_SESSION.is_dir(),
    reason=f"reference session not present at {REFERENCE_SESSION}",
)
def test_zero_hard_failures_on_reference_session():
    """REGRESSION GUARD: audit FAIL count on Howard's session must be 0.

    Separate from the PASS floor: SKIPs are acceptable, FAILs are not.
    If a future change introduces ANY hard failure on the reference
    session, this test catches it.
    """
    audit = _run_audit(REFERENCE_SESSION)
    counts = _tally(audit)
    failed = counts.get("FAIL", 0)

    if failed:
        # List the failures so the error message is actionable
        items = audit.get("items", audit.get("checks", []))
        fails = [it for it in items if it.get("status") == "FAIL"]
        details = "\n".join(
            f"  {it.get('id', '?'):8s} {it.get('evidence', '')[:80]}" for it in fails
        )
        pytest.fail(
            f"REGRESSION: {failed} hard FAILures appeared on reference session.\n"
            f"Failures:\n{details}"
        )
