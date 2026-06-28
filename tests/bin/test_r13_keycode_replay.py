"""Tests for V₁ R13 keycode-replay residual.

Critical: R13 must catch the FI-02 mutation that R09 misses
(W → B both valid VK codes; only multimodal replay finds the swap).
"""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

from bin.v1_claude_residuals.r13_keycode_replay import r13_keycode_replay


def _write_inputs_jsonl(events: list[dict], fps: float = 30.0) -> Path:
    """Write a temp inputs.jsonl and return its path."""
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


class TestR13KeyCodeReplay(unittest.TestCase):
    def test_w_held_for_2_seconds(self) -> None:
        """W down at t=0, up at t=2000ms. Frame 30 (t_end=1033ms) → {87}."""
        path = _write_inputs_jsonl(
            [
                {"event_type": "key_down", "key_code": 87, "timestamp_ms": 0},
                {"event_type": "key_up", "key_code": 87, "timestamp_ms": 2000},
            ]
        )
        rec = {"frame": 30, "keyCode": [87]}
        r = r13_keycode_replay(rec, inputs_path=str(path))
        self.assertTrue(r.passed, msg=f"expected match, got {r}")
        self.assertEqual(r.residual, 0.0)

    def test_fi02_w_to_b_mutation_caught(self) -> None:
        """FI-02 attack: producer wrote keyCode=[88] (B) but inputs.jsonl
        shows W (87) was held. R13 must detect."""
        path = _write_inputs_jsonl(
            [
                {"event_type": "key_down", "key_code": 87, "timestamp_ms": 0},
                {"event_type": "key_up", "key_code": 87, "timestamp_ms": 2000},
            ]
        )
        rec = {"frame": 30, "keyCode": [88]}  # mutated W→B
        r = r13_keycode_replay(rec, inputs_path=str(path))
        self.assertFalse(r.passed)
        self.assertEqual(r.residual, 2.0)  # symmetric_diff: {87, 88}

    def test_no_keys_held(self) -> None:
        """No key events. Frame N → empty set. keyCode=[] should pass."""
        path = _write_inputs_jsonl([])
        rec = {"frame": 5, "keyCode": []}
        r = r13_keycode_replay(rec, inputs_path=str(path))
        self.assertTrue(r.passed)

    def test_abstain_missing_path(self) -> None:
        """No inputs_path → ABSTAIN."""
        rec = {"frame": 0, "keyCode": [87]}
        r = r13_keycode_replay(rec, inputs_path=None)
        self.assertFalse(r.passed)
        self.assertTrue(math.isnan(r.residual))
        self.assertIn("ABSTAIN", r.note)

    def test_abstain_file_not_exist(self) -> None:
        """Path doesn't exist → ABSTAIN, not silent PASS (IL10)."""
        rec = {"frame": 0, "keyCode": []}
        r = r13_keycode_replay(rec, inputs_path="/tmp/nonexistent_xyz_qqq.jsonl")
        self.assertFalse(r.passed)
        self.assertTrue(math.isnan(r.residual))
        self.assertIn("ABSTAIN", r.note)

    def test_abstain_no_session_start(self) -> None:
        """inputs.jsonl without session_start sentinel → ABSTAIN."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as f:
            f.write(
                json.dumps(
                    {
                        "event_type": "key_down",
                        "key_code": 87,
                        "timestamp_ms": 0,
                    }
                )
                + "\n"
            )
        rec = {"frame": 0, "keyCode": [87]}
        r = r13_keycode_replay(rec, inputs_path=f.name)
        self.assertFalse(r.passed)
        self.assertIn("ABSTAIN", r.note)

    def test_abstain_fps_out_of_band(self) -> None:
        """fps=25 → ABSTAIN (orchestrator should reject the dataset on R12,
        but R13 itself doesn't take a side)."""
        path = _write_inputs_jsonl([], fps=25.0)
        rec = {"frame": 0, "keyCode": []}
        r = r13_keycode_replay(rec, inputs_path=str(path))
        self.assertFalse(r.passed)
        self.assertIn("ABSTAIN", r.note)

    def test_multiple_keys_held(self) -> None:
        """W + A pressed simultaneously. Frame 30 → {65, 87}."""
        path = _write_inputs_jsonl(
            [
                {"event_type": "key_down", "key_code": 87, "timestamp_ms": 0},
                {"event_type": "key_down", "key_code": 65, "timestamp_ms": 100},
            ]
        )
        rec = {"frame": 30, "keyCode": [87, 65]}
        r = r13_keycode_replay(rec, inputs_path=str(path))
        self.assertTrue(r.passed)

    def test_d03_truncation_abstains(self) -> None:
        """D-03 attack: inputs.jsonl events stop at t=1000ms but
        action_camera.json claims frame 300 (t_end ≈ 10033ms). Without the
        truncation gate, frame 300 with keyCode=[] would silently PASS
        because both sides are empty (IL10 violation). Must ABSTAIN."""
        path = _write_inputs_jsonl(
            [
                {"event_type": "key_down", "key_code": 87, "timestamp_ms": 0},
                {"event_type": "key_up", "key_code": 87, "timestamp_ms": 1000},
            ]
        )
        rec = {"frame": 300, "keyCode": []}  # would silently pass without gate
        r = r13_keycode_replay(rec, inputs_path=str(path))
        self.assertFalse(r.passed)
        self.assertTrue(math.isnan(r.residual))
        self.assertIn("inputs_truncated", r.note)

    def test_truncation_gate_within_5s_window_still_passes(self) -> None:
        """Edge case: last event at t=9000ms, frame 300 ends at t≈10033ms.
        Gap is ~1s, well under the 5s ABSTAIN threshold — must still PASS,
        no false ABSTAIN."""
        path = _write_inputs_jsonl(
            [
                {"event_type": "key_down", "key_code": 87, "timestamp_ms": 0},
                {"event_type": "key_up", "key_code": 87, "timestamp_ms": 9000},
            ]
        )
        rec = {"frame": 300, "keyCode": []}  # W released by 9000ms, frame=300 → empty
        r = r13_keycode_replay(rec, inputs_path=str(path))
        self.assertTrue(r.passed, msg=f"expected pass, got {r}")
        self.assertEqual(r.residual, 0.0)


if __name__ == "__main__":
    unittest.main()
