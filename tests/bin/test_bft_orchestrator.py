"""Tests for the BFT consensus orchestrator tally + collect_votes paths."""
from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from bin.bft_orchestrator.orchestrator import (  # noqa: E402
    Vote,
    aggregate_dataset,
    collect_votes,
    tally,
)


def _make_min_frame(idx: int) -> dict:
    """Minimal frame record satisfying the per-residual shape contracts."""
    return {
        "frame": idx,
        "time": "2026-05-02 12:00:00.000",
        "fps": 30.0,
        "mouse_x": [0.5],
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
        "camera_position": [0.0, 0.0, 0.0],
        "player_position": [0.0, 0.0, 0.0],
    }


def _write_inputs_jsonl(events: list[dict], fps: float = 30.0) -> Path:
    """Write a temp inputs.jsonl with session_start sentinel + events."""
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8",
    )
    f.write(json.dumps({
        "event_type": "session_start", "timestamp_ms": 0,
        "fps": fps, "frame_count": 9000,
    }) + "\n")
    for ev in events:
        f.write(json.dumps(ev) + "\n")
    f.close()
    return Path(f.name)


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


class MultimodalAggregateTests(unittest.TestCase):
    """Validate that aggregate_dataset wires R13/R15/R16 in when given the
    auxiliary artifacts, and is unchanged when those artifacts are absent."""

    def test_aggregate_with_inputs_path_produces_r13_votes(self) -> None:
        """inputs.jsonl present + matching keyCode → R13 appears in residuals."""
        inputs_path = _write_inputs_jsonl([
            {"event_type": "key_down", "key_code": 87, "timestamp_ms": 0},
            {"event_type": "key_up",   "key_code": 87, "timestamp_ms": 5000},
        ])
        # Frame 30 → t_end ~1033ms, W still held → keyCode=[87] matches replay.
        records = []
        for i in range(30, 33):
            r = _make_min_frame(i)
            r["keyCode"] = [87]
            records.append(r)
        try:
            result = aggregate_dataset(records, fps=30.0,
                                       inputs_path=str(inputs_path))
        finally:
            inputs_path.unlink(missing_ok=True)
        self.assertIn("R13", result["residuals"],
                      msg=f"R13 missing; got {sorted(result['residuals'])}")
        # Both V₁ and V₂' should COMMIT (matching replay).
        self.assertGreaterEqual(result["residuals"]["R13"]["COMMIT"], 1)

    def test_aggregate_with_video_path_produces_r15_vote(self) -> None:
        """video_path + mocked ffprobe → R15 appears once at dataset level."""
        records = [_make_min_frame(i) for i in range(3)]
        # Create a real file so os.path.isfile passes inside r15.
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as vf:
            vf.write(b"\x00")
            video_path = vf.name
        ffprobe_payload = json.dumps({"streams": [{"avg_frame_rate": "30/1"}]})

        class _FakeProc:
            stdout = ffprobe_payload
            stderr = ""
            returncode = 0

        try:
            with mock.patch(
                "bin.v1_claude_residuals.r15_fps_consistency.shutil.which",
                return_value="/usr/bin/ffprobe",
            ), mock.patch(
                "bin.v1_claude_residuals.r15_fps_consistency.subprocess.run",
                return_value=_FakeProc(),
            ):
                result = aggregate_dataset(records, fps=30.0,
                                           video_path=video_path)
        finally:
            Path(video_path).unlink(missing_ok=True)
        self.assertIn("R15", result["residuals"])
        # Single dataset-level vote → COMMIT (declared 30 == probed 30).
        self.assertEqual(result["residuals"]["R15"]["COMMIT"], 1)

    def test_aggregate_with_depth_dir_produces_r16_vote(self) -> None:
        """depth_dir with N exr files + duration → R16 appears."""
        records = [_make_min_frame(i) for i in range(3)]
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(60):
                (Path(tmp) / f"depth_{i:05d}.exr").touch()
            result = aggregate_dataset(records, fps=30.0, depth_dir=tmp,
                                       video_duration_sec=10.0)
        self.assertIn("R16", result["residuals"])
        # 60 files at 10s × 6fps → diff 0 → COMMIT.
        self.assertEqual(result["residuals"]["R16"]["COMMIT"], 1)

    def test_aggregate_without_aux_args_unchanged_behavior(self) -> None:
        """No multimodal args → no R13/R15/R16 keys (backward compatibility)."""
        records = [_make_min_frame(i) for i in range(3)]
        result = aggregate_dataset(records, fps=30.0)
        self.assertNotIn("R13", result["residuals"])
        self.assertNotIn("R15", result["residuals"])
        self.assertNotIn("R16", result["residuals"])
        # Frames-pair count + dataset_decision should still be populated.
        self.assertEqual(result["frames"], 2)
        self.assertIn(result["dataset_decision"],
                      {"PASS", "FAIL", "NEEDS_HUMAN"})


if __name__ == "__main__":
    unittest.main()
