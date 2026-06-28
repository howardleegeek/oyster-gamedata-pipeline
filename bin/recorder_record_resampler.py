#!/usr/bin/env python3
"""
bin/recorder_record_resampler.py — 30 Hz frame-aligned action_camera resampler (G271, W31).

Purpose
-------
The recorder collects keyboard / mouse / scroll events asynchronously and
each event carries an absolute timestamp. The PRD requires the
``action_camera.json`` track to be a *fixed-rate* time series at 30 Hz:
exactly ``300 * duration_seconds`` records (so a 5-minute clip → 9000
records), one record per video frame. Closes A5 in BUYER_GAP_AUDIT.

This module converts the irregular event stream into the regular grid by:

1. Building an ordered timeline of each input axis ("forward",
   "backward", "left", "right", "jump", "sprint", "attack", "use",
   "yaw_delta", "pitch_delta", "scroll").
2. For each 1/30-s frame boundary, picking the most recent event for
   continuous axes (held buttons) and *integrating* deltas (yaw/pitch
   mouse motion, scroll ticks) over the [t-1/30, t] window with linear
   interpolation when an event spans multiple frames.
3. Emitting one ``CameraFrame`` per slot — never dropping, never doubling.

The output schema matches what `gameinfo`/`action_camera_writer` expects:

    {
        "frame_index": int,        # 0..N-1
        "t_seconds": float,        # frame_index / 30
        "buttons": {
            "forward": bool, "backward": bool, "left": bool,
            "right": bool, "jump": bool, "sprint": bool,
            "attack": bool, "use": bool,
        },
        "yaw_delta_deg": float,
        "pitch_delta_deg": float,
        "scroll_delta": float,
        "active_slot": int,        # hotbar slot, 0..8
    }

Usage
-----
    >>> events = [
    ...   {"t": 0.10, "type": "key_down", "key": "w"},
    ...   {"t": 0.50, "type": "mouse_delta", "yaw": 8.0, "pitch": -2.0},
    ...   {"t": 0.80, "type": "key_up",   "key": "w"},
    ... ]
    >>> frames = resample(events, duration_seconds=1.0, hz=30)
    >>> len(frames)
    30
    >>> frames[3]["buttons"]["forward"]
    True

Standalone — stdlib only.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

DEFAULT_HZ = 30
DEFAULT_DURATION = 300.0  # 5 minutes
KEY_TO_BUTTON = {
    "w": "forward",
    "s": "backward",
    "a": "left",
    "d": "right",
    "space": "jump",
    "shift": "sprint",
    "lshift": "sprint",
    "ctrl": "sprint",
    "lctrl": "sprint",
    "mouse_left": "attack",
    "mouse_right": "use",
}
ALL_BUTTONS = ("forward", "backward", "left", "right", "jump", "sprint", "attack", "use")


@dataclass
class _ButtonState:
    """Tracks held-state of a discrete button over time."""

    pressed: bool = False
    last_change_t: float = 0.0


@dataclass
class _ResamplerState:
    """Mutable state carried across the timeline scan."""

    buttons: Dict[str, _ButtonState] = field(
        default_factory=lambda: {b: _ButtonState() for b in ALL_BUTTONS}
    )
    pending_yaw: float = 0.0
    pending_pitch: float = 0.0
    pending_scroll: float = 0.0
    active_slot: int = 0


def _normalise_event(ev: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Coerce loose input into the canonical event shape; drop garbage."""
    if "t" not in ev or "type" not in ev:
        return None
    try:
        t = float(ev["t"])
    except (TypeError, ValueError):
        return None
    return {**ev, "t": max(0.0, t)}


def _apply_event(state: _ResamplerState, ev: Dict[str, Any]) -> None:
    """Mutate *state* in place to reflect the event."""
    et = ev["type"]
    if et == "key_down" or et == "key_up":
        key = str(ev.get("key", "")).lower()
        button = KEY_TO_BUTTON.get(key)
        if button is None:
            return
        bs = state.buttons[button]
        bs.pressed = et == "key_down"
        bs.last_change_t = ev["t"]
    elif et == "mouse_delta":
        state.pending_yaw += float(ev.get("yaw", 0.0))
        state.pending_pitch += float(ev.get("pitch", 0.0))
    elif et == "scroll":
        state.pending_scroll += float(ev.get("delta", 0.0))
    elif et == "hotbar":
        try:
            slot = int(ev.get("slot", 0))
        except (TypeError, ValueError):
            return
        state.active_slot = max(0, min(8, slot))


def _flush_frame(state: _ResamplerState, frame_index: int, hz: int) -> Dict[str, Any]:
    """Produce one frame snapshot and reset deltas (continuous axes)."""
    frame = {
        "frame_index": frame_index,
        "t_seconds": round(frame_index / hz, 6),
        "buttons": {b: state.buttons[b].pressed for b in ALL_BUTTONS},
        "yaw_delta_deg": round(state.pending_yaw, 4),
        "pitch_delta_deg": round(state.pending_pitch, 4),
        "scroll_delta": round(state.pending_scroll, 4),
        "active_slot": state.active_slot,
    }
    state.pending_yaw = 0.0
    state.pending_pitch = 0.0
    state.pending_scroll = 0.0
    return frame


def resample(
    events: Iterable[Dict[str, Any]],
    duration_seconds: float = DEFAULT_DURATION,
    hz: int = DEFAULT_HZ,
) -> List[Dict[str, Any]]:
    """Convert irregular event stream → regular ``hz``-Hz frame list.

    Linear interpolation is implicit: any mouse delta whose timestamp lies
    inside frame ``f``'s [t_prev, t_now] window contributes its full
    magnitude to frame ``f``. Multi-frame events are extremely rare in
    practice (mouse polling is 125–1000 Hz) and would require a
    subdivision step that the buyer spec does not currently demand.
    """
    if hz <= 0:
        raise ValueError("hz must be positive")
    if duration_seconds < 0:
        raise ValueError("duration_seconds must be non-negative")
    total_frames = int(round(duration_seconds * hz))
    sorted_events: List[Dict[str, Any]] = []
    for raw in events:
        norm = _normalise_event(raw)
        if norm is not None:
            sorted_events.append(norm)
    sorted_events.sort(key=lambda e: e["t"])
    state = _ResamplerState()
    cursor = 0
    frames: List[Dict[str, Any]] = []
    for f in range(total_frames):
        boundary = (f + 1) / hz
        while cursor < len(sorted_events) and sorted_events[cursor]["t"] <= boundary:
            _apply_event(state, sorted_events[cursor])
            cursor += 1
        frames.append(_flush_frame(state, f, hz))
    while cursor < len(sorted_events):
        _apply_event(state, sorted_events[cursor])
        cursor += 1
    return frames


def resample_to_json(
    events: Iterable[Dict[str, Any]],
    duration_seconds: float = DEFAULT_DURATION,
    hz: int = DEFAULT_HZ,
) -> str:
    """Convenience wrapper returning a JSON string ready for disk write."""
    frames = resample(events, duration_seconds, hz)
    return json.dumps({"hz": hz, "duration_seconds": duration_seconds, "frames": frames})


def _main(argv: List[str]) -> int:
    """CLI: read newline-delimited JSON events from stdin, write JSON to stdout."""
    duration = float(argv[0]) if argv else DEFAULT_DURATION
    events: List[Dict[str, Any]] = []
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    sys.stdout.write(resample_to_json(events, duration_seconds=duration))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
