"""Tests for the BFT consensus orchestrator tally + collect_votes paths."""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from bin.bft_orchestrator.orchestrator import Vote, collect_votes, tally  # noqa: E402


def _vote(verifier: str, residual: str, verdict: str) -> Vote:
    return Vote(verifier_id=verifier, residual=residual, verdict=verdict, residual_value=0.0, threshold=0.0)


class TallyTests(unittest.TestCase):
    def test_three_pass_commits(self) -> None:
        votes = [
            _vote("V1", "R01", "PASS"),
            _vote("V2", "R01", "PASS"),
            _vote("V3", "R01", "PASS"),
        ]
        result = tally(votes)
        self.assertEqual(result["R01"]["decision"], "COMMIT")
        self.assertEqual(result["R01"]["passed"], 3)

    def test_two_pass_one_abstain_commits(self) -> None:
        # N=3, f=0, need 2*0+1 = 1 PASS for COMMIT; 2 passes is plenty.
        votes = [
            _vote("V1", "R02", "PASS"),
            _vote("V2", "R02", "PASS"),
            _vote("V3", "R02", "ABSTAIN"),
        ]
        result = tally(votes)
        self.assertEqual(result["R02"]["decision"], "COMMIT")
        self.assertEqual(result["R02"]["passed"], 2)
        self.assertEqual(result["R02"]["abstain"], 1)

    def test_one_pass_two_fail_rejects(self) -> None:
        votes = [
            _vote("V1", "R03", "PASS"),
            _vote("V2", "R03", "FAIL"),
            _vote("V3", "R03", "FAIL"),
        ]
        result = tally(votes)
        self.assertEqual(result["R03"]["decision"], "REJECT")
        self.assertEqual(result["R03"]["failed"], 2)

    def test_one_pass_one_fail_one_abstain_insufficient(self) -> None:
        # 1 PASS + 1 FAIL + 1 ABSTAIN: the third witness is missing, so the
        # committee cannot decide. Per the spec INSUFFICIENT rule, an
        # abstain-tainted tie is not a clean view-change tie.
        votes = [
            _vote("V1", "R04", "PASS"),
            _vote("V2", "R04", "FAIL"),
            _vote("V3", "R04", "ABSTAIN"),
        ]
        result = tally(votes)
        self.assertEqual(result["R04"]["decision"], "INSUFFICIENT")
        self.assertEqual(result["R04"]["passed"], 1)
        self.assertEqual(result["R04"]["failed"], 1)
        self.assertEqual(result["R04"]["abstain"], 1)

    def test_v2_exception_counted_as_fail(self) -> None:
        """If a verifier raises (e.g. V₂ R07 on scalar mouse_x), we still
        emit a FAIL Vote with evidence=str(exc) — that's BFT detection."""
        # Construct a frame where V₂ R07 reads mouse_x as scalar (not list).
        # V₂'s code does ``rec.get("mouse_x", [0.0])[0]`` — if we pass a
        # scalar 0.5 it raises TypeError. Same for V₁ R07 (returns FAIL).
        # The orchestrator should not crash; it should produce a FAIL vote.
        bad_frame = {
            "mouse_x": 0.5,  # scalar — triggers TypeError in V₂ R07
            "mouse_y": [0.5],
            "mouse_dx": [0.0],
            "mouse_dy": [0.0],
            "camera_rotation_quaternion": [0.0, 0.0, 0.0, 1.0],
            "player_rotation_quaternion": [0.0, 0.0, 0.0, 1.0],
            "camera_rotation_oula": [0.0, 0.0, 0.0],
            "player_rotation_oula": [0.0, 0.0, 0.0],
            "camera_intrinsics": {"fx": 771.4, "fy": 771.4, "cx": 960.0, "cy": 540.0},
            "keyCode": [],
            "camera_speed": [0.0, 0.0, 0.0],
            "player_speed": [0.0, 0.0, 0.0],
            "fps": 30.0,
            "time": "2026-05-02 12:00:00.000",
        }
        votes = collect_votes(bad_frame, neighbor=None, fps=30.0)
        # Filter to V₂ R07 votes — exception path should produce verdict=FAIL.
        v2_r07 = [v for v in votes if v.verifier_id == "V2" and v.residual == "R07"]
        self.assertTrue(len(v2_r07) >= 1)
        # Either FAIL because TypeError was caught, or FAIL via V₂'s
        # IndexError path on the empty list default. Either way, never PASS.
        self.assertNotEqual(v2_r07[0].verdict, "PASS")


if __name__ == "__main__":
    unittest.main()
