"""V₁ R13 — keyCode replay residual (closes FI-02 single-modal blind spot).

Reads inputs.jsonl (raw pynput event stream the producer writes alongside
action_camera.json) and reconstructs the held-key set per frame. Asserts
set-equality with action_camera.json[N].keyCode field.

PRD criterion #5: "keyCode 触发时机与画面动作完全一致". R09 only validates
VK-code legality (88 is valid), so a W→B mutation passes R09. R13 catches
it because B (88) was never pressed in inputs.jsonl, so set(snapshot) ≠
set(rec.keyCode).

Per IL10: ABSTAIN, never silent PASS, when artifact (inputs.jsonl) absent.

Module isolation: V₁'s implementation; V₂ MiniMax has independent dispatch.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Iterable

from .residuals import ResidualResult


def _parse_inputs_jsonl(path: Path) -> tuple[float | None, list[dict]]:
    """Return (fps_from_session_start, sorted_events). fps None if no
    session_start sentinel found."""
    if not path.exists():
        return None, []
    fps: float | None = None
    events: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("event_type") == "session_start":
                    fps = float(obj.get("fps", 0)) or None
                    continue
                if "timestamp_ms" in obj and "event_type" in obj:
                    events.append(obj)
    except OSError:
        return None, []
    events.sort(key=lambda e: float(e.get("timestamp_ms", 0)))
    return fps, events


def _held_keys_at(events: Iterable[dict], t_ms: float) -> set[int]:
    """Walk events with timestamp <= t_ms, return final held-key set."""
    held: set[int] = set()
    for ev in events:
        if float(ev.get("timestamp_ms", 0)) > t_ms:
            break
        et = ev.get("event_type")
        kc = ev.get("key_code")
        if not isinstance(kc, int):
            continue
        if et == "key_down":
            held.add(kc)
        elif et == "key_up":
            held.discard(kc)
    return held


def r13_keycode_replay(
    rec: dict,
    neighbor: dict | None = None,
    inputs_path: str | Path | None = None,
) -> ResidualResult:
    """V₁ R13: keyCode field equals raw-input replay snapshot.

    Args:
        rec: action_camera frame record (must contain ``frame``, ``keyCode``)
        neighbor: unused (kept for orchestrator API uniformity)
        inputs_path: path to clip's inputs.jsonl. ABSTAIN if None / missing.

    Returns:
        ResidualResult with passed=True iff the held-key set reconstructed
        from inputs.jsonl at frame boundary equals set(rec["keyCode"]).

    ABSTAIN encoding: passed=False, residual=NaN, note="ABSTAIN:<reason>"
    """
    threshold = 0.0
    if inputs_path is None:
        return ResidualResult("R13", False, math.nan, threshold, "ABSTAIN:no_inputs_path")
    p = Path(inputs_path)
    if not p.exists():
        return ResidualResult("R13", False, math.nan, threshold, "ABSTAIN:inputs_jsonl_absent")

    fps, events = _parse_inputs_jsonl(p)
    if fps is None:
        return ResidualResult(
            "R13", False, math.nan, threshold, "ABSTAIN:no_session_start_sentinel"
        )
    if not (29.5 <= fps <= 30.5):
        return ResidualResult("R13", False, math.nan, threshold, f"ABSTAIN:fps_out_of_band ({fps})")

    frame_idx = rec.get("frame")
    if not isinstance(frame_idx, int):
        return ResidualResult("R13", False, math.nan, threshold, "ABSTAIN:frame_index_missing")

    # Frame N spans [N · 1000/fps, (N+1) · 1000/fps] ms. Snapshot at end.
    t_end_ms = (frame_idx + 1) * (1000.0 / fps)

    # D-03 truncation defense: if last event predates this frame's boundary
    # by >5s, inputs.jsonl was truncated relative to action_camera.json.
    # Without this gate, frames past the truncation point silently PASS
    # whenever rec.keyCode == [] (both sides empty) — IL10 violation.
    if events:
        last_ts = float(events[-1].get("timestamp_ms", 0))
        if last_ts < t_end_ms - 5000:
            return ResidualResult(
                "R13",
                False,
                math.nan,
                threshold,
                f"ABSTAIN:inputs_truncated (last event @ {last_ts}ms, frame needs {t_end_ms}ms)",
            )

    snapshot = _held_keys_at(events, t_end_ms)
    actual = set(rec.get("keyCode") or [])
    if not isinstance(actual, set):
        actual = set()

    sym_diff = snapshot ^ actual
    residual = float(len(sym_diff))
    if residual == 0.0:
        return ResidualResult("R13", True, 0.0, threshold, f"replay matched ({len(snapshot)} keys)")
    return ResidualResult(
        "R13",
        False,
        residual,
        threshold,
        f"keyCode mismatch: replay={sorted(snapshot)} vs frame={sorted(actual)}",
    )
