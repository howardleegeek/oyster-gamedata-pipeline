"""V₂ MiniMax R13/R18/R21 unit tests (BFT N=4 redundancy)."""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

from bin.v2_minimax_residuals.residuals import (
    r13_keycode_replay,
    r18_session_manifest,
    r21_monotonic_frame,
)


def _write_inputs(events, fps=30.0, with_session_start=True):
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    ) as f:
        if with_session_start:
            f.write(
                json.dumps({"event_type": "session_start", "timestamp_ms": 0, "fps": fps}) + "\n"
            )
        for ev in events:
            f.write(json.dumps(ev) + "\n")
        return f.name


class TestV2R13(unittest.TestCase):
    def test_pass_w_held(self):
        path = _write_inputs(
            [
                {"event_type": "key_down", "key_code": 87, "timestamp_ms": 0},
                {"event_type": "key_up", "key_code": 87, "timestamp_ms": 2000},
            ]
        )
        r = r13_keycode_replay({"frame": 30, "keyCode": [87]}, inputs_path=path)
        self.assertTrue(r["passed"])
        self.assertEqual(r["residual"], 0.0)

    def test_fi02_mutation_caught(self):
        path = _write_inputs(
            [
                {"event_type": "key_down", "key_code": 87, "timestamp_ms": 0},
                {"event_type": "key_up", "key_code": 87, "timestamp_ms": 2000},
            ]
        )
        r = r13_keycode_replay({"frame": 30, "keyCode": [88]}, inputs_path=path)
        self.assertFalse(r["passed"])
        self.assertEqual(r["residual"], 2.0)

    def test_abstain_no_path(self):
        r = r13_keycode_replay({"frame": 0, "keyCode": []}, inputs_path=None)
        self.assertFalse(r["passed"])
        self.assertTrue(math.isnan(r["residual"]))
        self.assertIn("ABSTAIN:no_inputs_path", r["note"])

    def test_abstain_fps_out_of_band(self):
        path = _write_inputs([], fps=25.0)
        r = r13_keycode_replay({"frame": 0, "keyCode": []}, inputs_path=path)
        self.assertFalse(r["passed"])
        self.assertIn("ABSTAIN:fps_out_of_band", r["note"])


class TestV2R18(unittest.TestCase):
    def test_match_passes(self):
        sid = "uuid-aaaa"
        with tempfile.TemporaryDirectory() as tmp:
            mp = Path(tmp) / "m.json"
            mp.write_text(json.dumps({"session_id": sid}))
            r = r18_session_manifest({"session_id": sid}, manifest_path=mp)
        self.assertTrue(r["passed"])
        self.assertEqual(r["residual"], 0.0)

    def test_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            mp = Path(tmp) / "m.json"
            mp.write_text(json.dumps({"session_id": "A"}))
            r = r18_session_manifest({"session_id": "B"}, manifest_path=mp)
        self.assertFalse(r["passed"])
        self.assertEqual(r["residual"], 1.0)

    def test_abstain_empty_sid(self):
        with tempfile.TemporaryDirectory() as tmp:
            mp = Path(tmp) / "m.json"
            mp.write_text(json.dumps({"session_id": ""}))
            r = r18_session_manifest({"session_id": "x"}, manifest_path=mp)
        self.assertFalse(r["passed"])
        self.assertTrue(math.isnan(r["residual"]))
        self.assertIn("ABSTAIN:manifest_session_id_empty", r["note"])


class TestV2R21(unittest.TestCase):
    def test_in_order(self):
        r = r21_monotonic_frame({"frame": 0}, {"frame": 1})
        self.assertTrue(r["passed"])
        self.assertEqual(r["residual"], 0.0)

    def test_reorder(self):
        r = r21_monotonic_frame({"frame": 5}, {"frame": 4})
        self.assertFalse(r["passed"])
        self.assertEqual(r["residual"], 2.0)

    def test_no_neighbor(self):
        r = r21_monotonic_frame({"frame": 9000}, None)
        self.assertTrue(r["passed"])

    def test_abstain_missing_frame(self):
        r = r21_monotonic_frame({"keyCode": [87]}, {"frame": 1})
        self.assertFalse(r["passed"])
        self.assertTrue(math.isnan(r["residual"]))
        self.assertIn("ABSTAIN:frame_index_missing", r["note"])


if __name__ == "__main__":
    unittest.main()
