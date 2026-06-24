"""Tests for V₁ R21 monotonic frame-index residual.

Critical: R21 must catch the D-02 attack — frame indices reordered or
duplicated. R09 / R13 only inspect single-record semantics, so a swap of
frames 4 ↔ 5 in the stream silently passes them. R21 enforces the global
ordering invariant on adjacent pairs.
"""

from __future__ import annotations

import math
import unittest

from bin.v1_claude_residuals.r21_monotonic_frame import r21_monotonic_frame


class TestR21MonotonicFrame(unittest.TestCase):
    def test_in_order_pass(self) -> None:
        """frame 0 followed by frame 1 → strict increase, residual 0."""
        r = r21_monotonic_frame({"frame": 0}, {"frame": 1})
        self.assertTrue(r.passed, msg=f"expected PASS, got {r}")
        self.assertEqual(r.residual, 0.0)

    def test_reorder_caught(self) -> None:
        """D-02 attack: frame 5 written before frame 4 → FAIL, residual=2."""
        r = r21_monotonic_frame({"frame": 5}, {"frame": 4})
        self.assertFalse(r.passed)
        self.assertEqual(r.residual, 2.0)

    def test_duplicate_caught(self) -> None:
        """Two adjacent records share frame 5 → FAIL, residual=1."""
        r = r21_monotonic_frame({"frame": 5}, {"frame": 5})
        self.assertFalse(r.passed)
        self.assertEqual(r.residual, 1.0)

    def test_no_neighbor_pass(self) -> None:
        """Last frame in stream (neighbor=None) → PASS, nothing to compare."""
        r = r21_monotonic_frame({"frame": 9000}, None)
        self.assertTrue(r.passed)
        self.assertEqual(r.residual, 0.0)

    def test_abstain_missing_frame_key(self) -> None:
        """No 'frame' key on rec → ABSTAIN per IL10, residual=NaN."""
        r = r21_monotonic_frame({"keyCode": [87]}, {"frame": 1})
        self.assertFalse(r.passed)
        self.assertTrue(math.isnan(r.residual))
        self.assertIn("ABSTAIN:frame_index_missing", r.note)


if __name__ == "__main__":
    unittest.main()
