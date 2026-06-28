"""Tests for the BFT consensus orchestrator tally + collect_votes paths."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
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
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".jsonl",
        delete=False,
        encoding="utf-8",
    ) as f:
        f.write(
            json.dumps(
                {
                    "event_type": "session_start",
                    "timestamp_ms": 0,
                    "fps": fps,
                    "frame_count": 9000,
                }
            )
            + "\n"
        )
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    return Path(f.name)


def _vote(verifier: str, residual: str, verdict: str) -> Vote:
    return Vote(
        verifier_id=verifier, residual=residual, verdict=verdict, residual_value=0.0, threshold=0.0
    )


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
        inputs_path = _write_inputs_jsonl(
            [
                {"event_type": "key_down", "key_code": 87, "timestamp_ms": 0},
                {"event_type": "key_up", "key_code": 87, "timestamp_ms": 5000},
            ]
        )
        # Frame 30 → t_end ~1033ms, W still held → keyCode=[87] matches replay.
        records = []
        for i in range(30, 33):
            r = _make_min_frame(i)
            r["keyCode"] = [87]
            records.append(r)
        try:
            result = aggregate_dataset(records, fps=30.0, inputs_path=str(inputs_path))
        finally:
            inputs_path.unlink(missing_ok=True)
        self.assertIn(
            "R13", result["residuals"], msg=f"R13 missing; got {sorted(result['residuals'])}"
        )
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
            with (
                mock.patch(
                    "bin.v1_claude_residuals.r15_fps_consistency.shutil.which",
                    return_value="/usr/bin/ffprobe",
                ),
                mock.patch(
                    "bin.v1_claude_residuals.r15_fps_consistency.subprocess.run",
                    return_value=_FakeProc(),
                ),
            ):
                result = aggregate_dataset(records, fps=30.0, video_path=video_path)
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
            result = aggregate_dataset(records, fps=30.0, depth_dir=tmp, video_duration_sec=10.0)
        self.assertIn("R16", result["residuals"])
        # 60 files at 10s × 6fps → diff 0 → COMMIT.
        self.assertEqual(result["residuals"]["R16"]["COMMIT"], 1)

    def test_aggregate_without_aux_args_unchanged_behavior(self) -> None:
        """No multimodal args → no R13/R15/R16 keys (backward compatibility).

        R20a..R20e are also absent here because the 3-frame sample is below
        their ``min_frames=10`` ABSTAIN gate, and ABSTAIN votes are filtered
        out of the tally per IL10/IL11."""
        records = [_make_min_frame(i) for i in range(3)]
        result = aggregate_dataset(records, fps=30.0)
        self.assertNotIn("R13", result["residuals"])
        self.assertNotIn("R15", result["residuals"])
        self.assertNotIn("R16", result["residuals"])
        for r in ("R20a", "R20b", "R20c", "R20d", "R20e", "R22", "R23"):
            self.assertNotIn(r, result["residuals"], msg=f"unexpected {r} in tally")
        # Frames-pair count + dataset_decision should still be populated.
        self.assertEqual(result["frames"], 2)
        self.assertIn(result["dataset_decision"], {"PASS", "FAIL", "NEEDS_HUMAN"})


def _drift_baseline(n: int = 30, fps: float = 30.0) -> list[dict]:
    """Honest drift-grade dataset (≥ R20 min_frames=10)."""
    t0 = datetime(2026, 5, 6, 12, 0, 0)
    dt = 1.0 / fps
    out: list[dict] = []
    for i in range(n):
        t = t0 + timedelta(seconds=i * dt)
        rec = _make_min_frame(i)
        rec["time"] = t.strftime("%Y-%m-%d %H:%M:%S.") + f"{t.microsecond // 1000:03d}"
        rec["camera_speed"] = [0.5, 0.0, 0.0]
        rec["camera_rotation_oula"] = [0.0, i * 0.1, 0.0]
        rec["mouse_x"] = [0.5 + i * 1e-6]
        rec["mouse_dx"] = [1e-6]
        out.append(rec)
    return out


def _write_depth_dir(tmp: Path, count: int) -> dict[str, str]:
    """Write deterministic ``depth_<n>.exr`` files; return manifest mapping."""
    manifest: dict[str, str] = {}
    for i in range(count):
        name = f"depth_{i:05d}.exr"
        body = f"frame-{i}-payload-{'x' * (i % 7)}".encode("utf-8")
        (tmp / name).write_bytes(body)
        manifest[name] = hashlib.sha256(body).hexdigest()
    return manifest


class DatasetLevelResidualTests(unittest.TestCase):
    """R20 (drift) + R22 (depth manifest) + R23 (video codec) wiring."""

    def test_aggregate_with_full_aux_args_emits_r20_r22_r23(self) -> None:
        """All aux args present → R20a..e + R22 + R23 votes appear."""
        records = _drift_baseline(n=30)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            manifest = _write_depth_dir(tmp_path, 30)
            mpath = tmp_path / "_manifest.json"
            mpath.write_text(json.dumps(manifest), encoding="utf-8")
            with tempfile.NamedTemporaryFile(
                suffix=".mp4",
                delete=False,
            ) as vf:
                vf.write(b"\x00")
                video_path = vf.name
            ffprobe_payload = json.dumps(
                {
                    "streams": [
                        {
                            "codec_type": "video",
                            "avg_frame_rate": "30/1",
                            "codec_name": "hevc",
                            "width": 1920,
                            "height": 1080,
                        }
                    ]
                }
            )

            class _FakeProc:
                stdout = ffprobe_payload
                stderr = ""
                returncode = 0

            try:
                with (
                    mock.patch(
                        "bin.v1_claude_residuals.r15_fps_consistency.shutil.which",
                        return_value="/usr/bin/ffprobe",
                    ),
                    mock.patch(
                        "bin.v1_claude_residuals.r15_fps_consistency.subprocess.run",
                        return_value=_FakeProc(),
                    ),
                    mock.patch(
                        "bin.v1_claude_residuals.r23_video_codec.shutil.which",
                        return_value="/usr/bin/ffprobe",
                    ),
                    mock.patch(
                        "bin.v1_claude_residuals.r23_video_codec.subprocess.run",
                        return_value=_FakeProc(),
                    ),
                ):
                    result = aggregate_dataset(
                        records,
                        fps=30.0,
                        video_path=video_path,
                        depth_dir=str(tmp_path),
                        depth_manifest_path=str(mpath),
                        video_duration_sec=float(len(records)) / 30.0,
                    )
            finally:
                Path(video_path).unlink(missing_ok=True)

        for r in ("R20a", "R20b", "R20c", "R20d", "R20e", "R22", "R23"):
            self.assertIn(
                r, result["residuals"], msg=f"{r} missing; got {sorted(result['residuals'])}"
            )
            self.assertEqual(
                result["residuals"][r]["COMMIT"], 1, msg=f"{r} should COMMIT on honest baseline"
            )

    def test_aggregate_records_only_r20_fires_no_r22_r23(self) -> None:
        """Just records (≥ min_frames) → R20a..e fire, R22/R23 absent."""
        records = _drift_baseline(n=30)
        result = aggregate_dataset(records, fps=30.0)
        for r in ("R20a", "R20b", "R20c", "R20d", "R20e"):
            self.assertIn(r, result["residuals"], msg=f"{r} missing on drift-grade sample")
            self.assertEqual(result["residuals"][r]["COMMIT"], 1)
        # R22 + R23 require aux args — must be absent.
        self.assertNotIn("R22", result["residuals"])
        self.assertNotIn("R23", result["residuals"])

    def test_drift_attack_quat_norm_offset_r20a_fails(self) -> None:
        """50 frames with persistent quat-norm offset → R20a REJECTs.

        Per the spec C-01 fixture: rescale ‖q‖ to 1+5e-5, well above the
        1e-5 max_offset default → R20a sample_stat exceeds threshold.
        """
        records = _drift_baseline(n=50)
        scale = 1.0 + 5e-5
        for r in records:
            r["camera_rotation_quaternion"] = [0.0, 0.0, 0.0, scale]
        result = aggregate_dataset(records, fps=30.0)
        self.assertIn("R20a", result["residuals"])
        # Single dataset-level vote that FAILed → REJECT bucket = 1.
        self.assertEqual(
            result["residuals"]["R20a"]["REJECT"],
            1,
            msg=f"R20a should REJECT under quat drift, got {result['residuals']['R20a']}",
        )
        self.assertEqual(result["residuals"]["R20a"]["COMMIT"], 0)

    def test_depth_manifest_mismatch_r22_fails(self) -> None:
        """Manifest references files that don't match content → R22 REJECTs."""
        records = _drift_baseline(n=15)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            manifest = _write_depth_dir(tmp_path, 5)
            mpath = tmp_path / "_manifest.json"
            mpath.write_text(json.dumps(manifest), encoding="utf-8")
            # Adversary action: rewrite one file's bytes after manifest is
            # written, simulating a shuffle/swap attack (D-04).
            (tmp_path / "depth_00002.exr").write_bytes(b"TAMPERED-BYTES")
            result = aggregate_dataset(
                records,
                fps=30.0,
                depth_dir=str(tmp_path),
                depth_manifest_path=str(mpath),
            )
        self.assertIn("R22", result["residuals"])
        self.assertEqual(
            result["residuals"]["R22"]["REJECT"],
            1,
            msg=f"R22 should REJECT on hash mismatch, got {result['residuals']['R22']}",
        )
        self.assertEqual(result["residuals"]["R22"]["COMMIT"], 0)


if __name__ == "__main__":
    unittest.main()
