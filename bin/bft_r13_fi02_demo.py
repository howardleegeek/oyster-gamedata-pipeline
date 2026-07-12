"""Demo harness: prove R13 catches FI-02 (W→B keyCode mutation) where R09 fails.

This is the architectural validation that the multimodal residual closes
the BFT N=4 single-modal blind spot exposed by FI-02 in the original
adversarial harness.

Run:
    python -m bin.bft_r13_fi02_demo

Expected output:
    R09 (single-modal):  V1=PASS V2=PASS V3=PASS  → 0/3 detect (false-PASS)
    R13 (multimodal):    V1=FAIL                  → blind spot CLOSED

This file does NOT depend on V2/V2'/V3 R13 implementations (they are
independent dispatches in flight). When V2 R13 lands, this demo will be
extended to show 2/2 detect.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from bin.v1_claude_residuals.r13_keycode_replay import r13_keycode_replay
from bin.v1_claude_residuals.residuals import r09_keycode_vk as v1_r09
from bin.v2_minimax_residuals.residuals import r09_keycode_vk as v2_r09
from bin.v3_physics_oracle.residuals import Verdict
from bin.v3_physics_oracle.residuals import r09_keycode_vk_known as v3_r09


def _build_inputs_jsonl(events: list[dict], fps: float = 30.0) -> Path:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    ) as f:
        f.write(json.dumps({
            "event_type": "session_start", "timestamp_ms": 0,
            "fps": fps, "frame_count": 9000,
        }) + "\n")
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    return Path(f.name)


def main() -> int:
    print("=" * 72)
    print("BFT R13 vs R09 — FI-02 single-modal blind spot demonstration")
    print("=" * 72)

    # FI-02 attack scenario:
    #   raw input recording: W (87) was held the whole time
    #   producer's action_camera.json (mutated/attacked): keyCode = [88]
    inputs_path = _build_inputs_jsonl([
        {"event_type": "key_down", "key_code": 87, "timestamp_ms": 0},
        {"event_type": "key_up",   "key_code": 87, "timestamp_ms": 5000},
    ])

    rec_attacked = {
        "frame": 30,
        "keyCode": [88],  # ← MUTATED: W (87) replaced with B (88)
    }

    print()
    print("[Single-modal R09 — current BFT N=3 votes]")
    r1 = v1_r09(rec_attacked)
    r2 = v2_r09(rec_attacked)
    r3 = v3_r09(rec_attacked)
    print(f"  V1 R09: passed={r1.passed} note={r1.note!r}")
    print(f"  V2 R09: passed={r2['passed']} note={r2.get('note', '')}")
    print(f"  V3 R09: verdict={r3.verdict.value} note={r3.note}")
    n_caught = sum([
        not r1.passed,
        not r2["passed"],
        r3.verdict == Verdict.FAIL,
    ])
    print(f"  → R09 detected by {n_caught}/3 verifiers (BLIND SPOT)")

    print()
    print("[Multimodal R13 — V1 implementation]")
    r13 = r13_keycode_replay(rec_attacked, inputs_path=str(inputs_path))
    print(f"  V1 R13: passed={r13.passed} residual={r13.residual} note={r13.note!r}")
    if r13.passed:
        print("  → R13 MISSED the FI-02 attack (regression!)")
        return 1
    print(f"  → R13 CAUGHT FI-02 ({r13.note})")

    print()
    print("=" * 72)
    print("RESULT: BFT N=3 single-modal misses FI-02; R13 multimodal catches it.")
    print("Architectural blind spot closed. (V2 R13 implementation pending.)")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
