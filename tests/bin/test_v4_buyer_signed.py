"""Tests for V₄ buyer-signed reference verifier.

Closes critical-gap coverage on B-01 (self-consistent Hamilton oula+quat
swap) and B-03 (coordinated keyCode + inputs.jsonl W→B swap) per
``docs/SPEC_V4_BUYER_SIGNED_PROTOCOL.md`` § 5.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bin.v4_buyer_signed import (
    compute_signature,
    v4_buyer_reference_diff,
)

SECRET = "test-buyer-shared-secret-v4-only"
DATASET_ID = "ds-2026-05-06-abc123"


def _frame(idx: int, key: int = 87, oula=(0.0, 1.0, 0.0)) -> dict:
    return {
        "frame": idx,
        "session_id": DATASET_ID,
        "keyCode": [key],
        "camera_rotation_oula": list(oula),
        "camera_rotation_quaternion": [0.0, 0.0087, 0.0, 0.99996],
        "camera_position": [0.0, 1.62, 0.0],
        "mouse_x": [0.0],
    }


def _make_reference(snapshots: list[dict], dataset_id: str = DATASET_ID) -> Path:
    payload = {
        "schema_version": "buyer-reference/v1",
        "dataset_id": dataset_id,
        "frame_indices": [s["frame"] for s in snapshots],
        "snapshots": snapshots,
        "video_frame_hashes": ["sha256:placeholder"] * len(snapshots),
    }
    payload["signature"] = compute_signature(payload, SECRET)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        f.write(json.dumps(payload))
        return Path(f.name)


class TestV4BuyerSigned(unittest.TestCase):
    def setUp(self) -> None:
        self.env = mock.patch.dict(os.environ, {"BUYER_SHARED_SECRET": SECRET})
        self.env.start()
        self.snapshots = [
            _frame(0),
            _frame(142),
            _frame(2700, key=87, oula=(0.0, 1.5, 0.0)),
            _frame(4500),
            _frame(8999),
        ]
        self.ref_path = _make_reference(self.snapshots)

    def tearDown(self) -> None:
        self.env.stop()
        self.ref_path.unlink(missing_ok=True)

    def test_happy_matching_reference_frame_passes(self) -> None:
        r = v4_buyer_reference_diff(self.snapshots[1], buyer_reference_path=str(self.ref_path))
        self.assertTrue(r.passed, msg=f"expected PASS, got {r}")
        self.assertEqual(r.residual, 0.0)
        self.assertIn("byte_match", r.note)

    def test_b01_hamilton_swap_caught(self) -> None:
        """B-01: oula mutated + Hamilton-consistent quat → byte-diff FAILs."""
        mutated = dict(self.snapshots[1])
        mutated["camera_rotation_oula"] = [0.0, 5.0, 0.0]
        mutated["camera_rotation_quaternion"] = [0.0, 0.04362, 0.0, 0.99905]
        r = v4_buyer_reference_diff(mutated, buyer_reference_path=str(self.ref_path))
        self.assertFalse(r.passed, msg=f"B-01 should FAIL, got {r}")
        self.assertIn("FAIL:byte_diff", r.note)

    def test_b03_keycode_swap_caught(self) -> None:
        """B-03: keyCode 87 → 88 (both valid VK) → byte-diff FAILs."""
        mutated = dict(self.snapshots[1])
        mutated["keyCode"] = [88]
        r = v4_buyer_reference_diff(mutated, buyer_reference_path=str(self.ref_path))
        self.assertFalse(r.passed, msg=f"B-03 should FAIL, got {r}")
        self.assertIn("FAIL:byte_diff", r.note)

    def test_frame_not_in_reference_passes(self) -> None:
        out_of_scope = _frame(5000)
        r = v4_buyer_reference_diff(out_of_scope, buyer_reference_path=str(self.ref_path))
        self.assertTrue(r.passed)
        self.assertEqual(r.note, "not_a_reference_frame")

    def test_missing_buyer_reference_path_abstains(self) -> None:
        r = v4_buyer_reference_diff(self.snapshots[1], buyer_reference_path=None)
        self.assertFalse(r.passed)
        self.assertTrue(r.note.startswith("ABSTAIN:reference_missing"))

    def test_missing_signature_abstains(self) -> None:
        broken = json.loads(self.ref_path.read_text())
        broken.pop("signature")
        # File missing required signature key → schema_mismatch ABSTAIN
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as bad:
            bad.write(json.dumps(broken))
        try:
            r = v4_buyer_reference_diff(self.snapshots[1], buyer_reference_path=bad.name)
            self.assertFalse(r.passed)
            self.assertTrue(r.note.startswith("ABSTAIN:reference_schema_mismatch"))
        finally:
            Path(bad.name).unlink(missing_ok=True)

    def test_invalid_signature_abstains(self) -> None:
        tampered = json.loads(self.ref_path.read_text())
        tampered["signature"] = "deadbeef" * 8
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as bad:
            bad.write(json.dumps(tampered))
        try:
            r = v4_buyer_reference_diff(self.snapshots[1], buyer_reference_path=bad.name)
            self.assertFalse(r.passed)
            self.assertTrue(r.note.startswith("ABSTAIN:signature_invalid"))
        finally:
            Path(bad.name).unlink(missing_ok=True)

    def test_dataset_id_mismatch_fails(self) -> None:
        """Sig valid but rec.session_id ≠ ref.dataset_id → FAIL (not ABSTAIN)."""
        wrong = dict(self.snapshots[1])
        wrong["session_id"] = "ds-different-session-uuid"
        r = v4_buyer_reference_diff(wrong, buyer_reference_path=str(self.ref_path))
        self.assertFalse(r.passed)
        self.assertIn("FAIL:dataset_id_mismatch", r.note)


if __name__ == "__main__":
    unittest.main()
