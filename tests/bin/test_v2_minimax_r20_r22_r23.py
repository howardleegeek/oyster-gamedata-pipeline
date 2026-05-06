"""V₂ MiniMax R20a-e/R22/R23 unit tests (BFT N=4 redundancy).

Mirrors V₁ scenarios. Dict return shape; ABSTAIN: passed=False, residual=NaN.
"""
from __future__ import annotations

import hashlib
import json
import math
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from bin.v2_minimax_residuals.residuals import (
    r20a_quat_norm_distribution,
    r20b_mouse_dx_cumulative,
    r20c_fps_jitter,
    r20d_speed_profile,
    r20e_yaw_turn_rate,
    r22_depth_hash,
    r23_video_codec,
)

N = 600
T0 = datetime(2026, 5, 6, 12, 0, 0)


def _baseline(n=N, fps=30.0, yaw_step=0.1):
    dt = 1.0 / fps
    out = []
    for i in range(n):
        t = T0 + timedelta(seconds=i * dt)
        out.append({
            "frame": i, "fps": fps,
            "time": t.strftime("%Y-%m-%d %H:%M:%S.")
                    + f"{t.microsecond // 1000:03d}000",
            "camera_rotation_quaternion": [0.0, 0.0, 0.0, 1.0],
            "camera_rotation_oula": [0.0, i * yaw_step, 0.0],
            "camera_speed": [0.5, 0.0, 0.0],
            "mouse_x": [0.5 + i * 1e-6],
            "mouse_dx": [1e-6],
        })
    return out


class TestV2R20Honest(unittest.TestCase):
    def test_baseline_passes_all(self):
        recs = _baseline()
        self.assertTrue(r20a_quat_norm_distribution(recs)["passed"])
        self.assertTrue(r20b_mouse_dx_cumulative(recs)["passed"])
        self.assertTrue(r20c_fps_jitter(recs)["passed"])
        self.assertTrue(r20d_speed_profile(recs)["passed"])
        self.assertTrue(r20e_yaw_turn_rate(recs)["passed"])


class TestV2R20Adversarial(unittest.TestCase):
    def test_C01_quat_norm_drift(self):
        recs = _baseline()
        for r in recs:
            r["camera_rotation_quaternion"] = [0.0, 0.0, 0.0, 1.0 + 5e-5]
        res = r20a_quat_norm_distribution(recs)
        self.assertFalse(res["passed"])
        self.assertGreater(res["residual"], 1e-5)

    def test_C02_mouse_dx_cumulative_drift(self):
        recs = _baseline()
        for r in recs:
            r["mouse_dx"] = [r["mouse_dx"][0] + 5e-5]
        res = r20b_mouse_dx_cumulative(recs)
        self.assertFalse(res["passed"])

    def test_C04_speed_outliers(self):
        recs = _baseline()
        for i in range(100):
            recs[i]["camera_speed"] = [40.0, 0.0, 0.0]
        res = r20d_speed_profile(recs)
        self.assertFalse(res["passed"])

    def test_C05_yaw_extreme(self):
        recs = _baseline()
        for i in range(0, 200, 2):
            recs[i]["camera_rotation_oula"] = [0.0, 0.0, 0.0]
            recs[i + 1]["camera_rotation_oula"] = [0.0, 90.0, 0.0]
        res = r20e_yaw_turn_rate(recs)
        self.assertFalse(res["passed"])


class TestV2R20Abstain(unittest.TestCase):
    def test_empty_records(self):
        for fn in (r20a_quat_norm_distribution, r20b_mouse_dx_cumulative,
                   r20c_fps_jitter, r20d_speed_profile, r20e_yaw_turn_rate):
            r = fn([])
            self.assertFalse(r["passed"])
            self.assertTrue(math.isnan(r["residual"]))
            self.assertIn("ABSTAIN:empty_records", r["note"])

    def test_insufficient_sample(self):
        r = r20a_quat_norm_distribution(_baseline(n=5))
        self.assertIn("ABSTAIN:insufficient_sample", r["note"])

    def test_malformed_field(self):
        recs = _baseline()
        for r in recs:
            r["mouse_dx"] = "bad"
        r = r20b_mouse_dx_cumulative(recs)
        self.assertIn("ABSTAIN:malformed_field", r["note"])


class TestV2R22(unittest.TestCase):
    def test_pass_all_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "depth"
            d.mkdir()
            payload = b"depth-pixels"
            (d / "00000.exr").write_bytes(payload)
            sha = hashlib.sha256(payload).hexdigest()
            mp = Path(tmp) / "manifest.json"
            mp.write_text(json.dumps({"00000.exr": sha}))
            r = r22_depth_hash({}, depth_dir=d, manifest_path=mp)
        self.assertTrue(r["passed"])
        self.assertEqual(r["residual"], 0.0)

    def test_fail_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "depth"
            d.mkdir()
            (d / "00000.exr").write_bytes(b"tampered")
            mp = Path(tmp) / "manifest.json"
            mp.write_text(json.dumps({"00000.exr": "00" * 32}))
            r = r22_depth_hash({}, depth_dir=d, manifest_path=mp)
        self.assertFalse(r["passed"])
        self.assertEqual(r["residual"], 1.0)

    def test_abstain_no_dir(self):
        r = r22_depth_hash({}, depth_dir=None, manifest_path=None)
        self.assertFalse(r["passed"])
        self.assertTrue(math.isnan(r["residual"]))
        self.assertIn("ABSTAIN:no_depth_dir", r["note"])

    def test_abstain_bad_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "depth"
            d.mkdir()
            mp = Path(tmp) / "m.json"
            mp.write_text(json.dumps([1, 2, 3]))
            r = r22_depth_hash({}, depth_dir=d, manifest_path=mp)
        self.assertIn("ABSTAIN:manifest_bad_shape", r["note"])


class TestV2R23(unittest.TestCase):
    def test_abstain_no_path(self):
        r = r23_video_codec({}, video_path=None)
        self.assertFalse(r["passed"])
        self.assertTrue(math.isnan(r["residual"]))
        self.assertIn("ABSTAIN:no_video_file", r["note"])

    def test_abstain_missing_file(self):
        r = r23_video_codec({}, video_path="/nonexistent/path/v.mp4")
        self.assertIn("ABSTAIN:no_video_file", r["note"])


if __name__ == "__main__":
    unittest.main()
