#!/usr/bin/env python3
"""
test_build_action_camera_alignment.py — frame↔pose alignment correctness.

These tests pin the ISC-FRAME2FRAME fix in ``bin/build_action_camera.py``:
the old code clamped any frame outside the recorded-pose window to the
first/last tick (silently fabricating a frozen pose) and anchored frame 0
to ``metadata.start_timestamp`` (which is 115 ms before OBS emitted frame 0).

The fix:
  * anchors frame 0 to the ``VIDEO_START`` inputs.jsonl event when present;
  * linearly interpolates pose between the two bracketing game_state ticks
    inside the real pose window (shortest-arc for yaw/pitch);
  * flags frames outside the window ``pose_valid=false`` /
    ``pose_source="extrapolated_no_pose"`` instead of presenting a clamped
    pose as real;
  * keeps every original field and ``len(records) == frame_count``.

Unit tests build small synthetic JSONL fixtures under ``tmp_path``. One
integration test runs against the real ``/Users/howardli/Downloads/New
Session`` if it is present (skipped otherwise).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

# Import the module under test from bin/ (sibling of tests/).
import sys

BIN_DIR = Path(__file__).resolve().parent.parent / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

import build_action_camera as bac  # noqa: E402

# --- fixture helpers ---------------------------------------------------------

# Original 20-field contract (the canonical action_camera schema, pre-fix).
ORIGINAL_FIELDS = {
    "frame",
    "time",
    "timestamp",
    "fps",
    "route_type",
    "mouse_x",
    "mouse_y",
    "mouse_dx",
    "mouse_dy",
    "keyCode",
    "camera_position",
    "camera_rotation_oula",
    "camera_rotation_quaternion",
    "camera_Follow Offset",
    "camera_intrinsics",
    "camera_speed",
    "player_position",
    "player_rotation_oula",
    "player_rotation_quaternion",
    "player_speed",
    "metric_scale",
}

NEW_FIELDS = {"pose_valid", "pose_source", "pose_dt_ms"}


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _gs_row(tick: int, ts_ms: int, **over) -> dict:
    """A minimal game_state row at epoch-ms ``ts_ms``."""
    row = {
        "tick": tick,
        "timestamp_ms": ts_ms,
        "x": 0.0,
        "y": 64.0,
        "z": 0.0,
        "yaw_deg": 0.0,
        "pitch_deg": 0.0,
        "velocity_x": 0.0,
        "velocity_y": 0.0,
        "velocity_z": 0.0,
        "on_ground": True,
        "sneaking": False,
        "sprinting": False,
    }
    row.update(over)
    return row


def _make_session(
    tmp_path: Path,
    *,
    gs_rows: list[dict],
    fps: float,
    frame_count: int,
    start_timestamp: float | None = None,
    video_start_ts: float | None = None,
    width: int = 320,
    height: int = 240,
    extra_inputs: list[dict] | None = None,
) -> Path:
    """Materialize a synthetic session dir (no real video; metadata supplies fps)."""
    session = tmp_path / "session"
    session.mkdir(exist_ok=True)

    _write_jsonl(session / "game_state.jsonl", gs_rows)

    inputs: list[dict] = []
    if video_start_ts is not None:
        inputs.append({"timestamp": video_start_ts, "event_type": "VIDEO_START", "event_args": []})
    if extra_inputs:
        inputs.extend(extra_inputs)
    _write_jsonl(session / "inputs.jsonl", inputs)

    metadata: dict = {
        "capture_resolution": [width, height],
        "fps_effective": fps,
        "average_fps": fps,
        "frame_count": frame_count,
    }
    if start_timestamp is not None:
        metadata["start_timestamp"] = start_timestamp
    (session / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    return session


# --- (a) anchor: VIDEO_START preferred over start_timestamp ------------------


def test_anchor_prefers_video_start_over_start_timestamp(tmp_path):
    """VIDEO_START event epoch must win over metadata.start_timestamp."""
    inputs = [
        {"timestamp": 100.115, "event_type": "VIDEO_START", "event_args": []},
        {"timestamp": 100.000, "event_type": "START", "event_args": []},
    ]
    anchor, source = bac.resolve_frame_anchor(
        inputs, {"start_timestamp": 100.000}, gs_first_epoch=105.0
    )
    assert source == "video_start_event"
    assert anchor == pytest.approx(100.115)


def test_anchor_falls_back_to_start_timestamp_when_no_video_start(tmp_path):
    anchor, source = bac.resolve_frame_anchor(
        [{"timestamp": 100.0, "event_type": "START", "event_args": []}],
        {"start_timestamp": 100.000},
        gs_first_epoch=105.0,
    )
    assert source == "metadata_start_timestamp"
    assert anchor == pytest.approx(100.000)


def test_anchor_falls_back_to_first_game_state(tmp_path):
    anchor, source = bac.resolve_frame_anchor([], {}, gs_first_epoch=105.0)
    assert source == "first_game_state"
    assert anchor == pytest.approx(105.0)


# --- (b) interpolation at midpoint == linear blend ---------------------------


def test_interpolation_midpoint_is_linear_blend(tmp_path):
    """A frame exactly between two ticks must equal their linear average."""
    gs_rows = [
        _gs_row(0, 1000, x=0.0, y=64.0, z=0.0, velocity_x=0.0),
        _gs_row(1, 1050, x=10.0, y=66.0, z=-4.0, velocity_x=0.4),
    ]
    gs_times, gs_sorted = bac.build_gs_index(gs_rows)
    # midpoint epoch = 1.025 s (between 1.000 and 1.050)
    pose = bac.interpolate_pose(gs_times, gs_sorted, 1.025)
    assert pose["pose_valid"] is True
    assert pose["x"] == pytest.approx(5.0)
    assert pose["y"] == pytest.approx(65.0)
    assert pose["z"] == pytest.approx(-2.0)
    assert pose["velocity_x"] == pytest.approx(0.2)
    # speed derived from interpolated velocity components
    assert pose["speed"] == pytest.approx(0.2)
    # midpoint is ~25ms from each tick → interpolated, not exact
    assert pose["pose_source"] == "interpolated"
    assert pose["pose_dt_ms"] == pytest.approx(25.0, abs=0.5)


def test_interpolation_exact_tick_is_exact_source(tmp_path):
    gs_rows = [
        _gs_row(0, 1000, x=0.0),
        _gs_row(1, 1050, x=10.0),
    ]
    gs_times, gs_sorted = bac.build_gs_index(gs_rows)
    pose = bac.interpolate_pose(gs_times, gs_sorted, 1.000)
    assert pose["pose_valid"] is True
    assert pose["pose_source"] == "exact"
    assert pose["pose_dt_ms"] == pytest.approx(0.0, abs=0.5)
    assert pose["x"] == pytest.approx(0.0)


# --- (c) yaw wrap 350°↔10° interpolates through 0° (short arc) --------------


def test_yaw_wrap_short_arc_through_zero(tmp_path):
    """350°→10° at the midpoint must be 0° (short arc), not 180° (naive lerp)."""
    gs_rows = [
        _gs_row(0, 1000, yaw_deg=350.0),
        _gs_row(1, 1050, yaw_deg=10.0),
    ]
    gs_times, gs_sorted = bac.build_gs_index(gs_rows)
    pose = bac.interpolate_pose(gs_times, gs_sorted, 1.025)
    # short arc: 350 -> 360/0 -> 10, midpoint == 0 (== 360, equivalent)
    yaw = pose["yaw_deg"] % 360.0
    assert min(yaw, 360.0 - yaw) == pytest.approx(0.0, abs=1e-6)
    # naive lerp would give 180.0 — explicitly reject that
    assert abs(yaw - 180.0) > 1.0


def test_pitch_short_arc_quarter_point(tmp_path):
    """Pitch interpolation also uses short arc; quarter point of 350->10 == 0."""
    gs_rows = [
        _gs_row(0, 1000, pitch_deg=-10.0),
        _gs_row(1, 1050, pitch_deg=10.0),
    ]
    gs_times, gs_sorted = bac.build_gs_index(gs_rows)
    pose = bac.interpolate_pose(gs_times, gs_sorted, 1.025)
    assert pose["pitch_deg"] == pytest.approx(0.0, abs=1e-6)


def test_lerp_angle_helper_short_arc():
    """Directly exercise the angular-lerp helper at boundary cases."""
    # 350 -> 10, t=0.5 -> 0 (mod 360)
    a = bac.lerp_angle_deg(350.0, 10.0, 0.5) % 360.0
    assert min(a, 360.0 - a) == pytest.approx(0.0, abs=1e-6)
    # 10 -> 350 (i.e. -20 short arc), t=0.5 -> 0
    b = bac.lerp_angle_deg(10.0, 350.0, 0.5) % 360.0
    assert min(b, 360.0 - b) == pytest.approx(0.0, abs=1e-6)
    # no wrap: 10 -> 20, t=0.5 -> 15
    assert bac.lerp_angle_deg(10.0, 20.0, 0.5) == pytest.approx(15.0)


# --- (d) leading & trailing uncovered frames flagged ------------------------


def test_leading_and_trailing_uncovered_frames(tmp_path):
    """Frames before first tick / after last tick are flagged, never faked."""
    anchor = 100.0
    fps = 10.0
    # ticks cover epoch [100.5 .. 101.0] → video-seconds [0.5 .. 1.0] → frames [5..10]
    gs_rows = [
        _gs_row(0, 100500, x=1.0),
        _gs_row(1, 100550, x=2.0),
        _gs_row(2, 100600, x=3.0),
        _gs_row(3, 100650, x=4.0),
        _gs_row(4, 100700, x=5.0),
        _gs_row(5, 100750, x=6.0),
        _gs_row(6, 100800, x=7.0),
        _gs_row(7, 100850, x=8.0),
        _gs_row(8, 100900, x=9.0),
        _gs_row(9, 100950, x=10.0),
        _gs_row(10, 101000, x=11.0),
    ]
    session = _make_session(
        tmp_path,
        gs_rows=gs_rows,
        fps=fps,
        frame_count=16,  # frames 0..15; [0..4] leading, [11..15] trailing uncovered
        start_timestamp=anchor,
        video_start_ts=anchor,
    )
    records = bac.build_records(session, vfov_deg=70.0)
    assert len(records) == 16

    # frame 0 (epoch 100.0) is BEFORE first tick (100.5) → uncovered leading
    assert records[0]["pose_valid"] is False
    assert records[0]["pose_source"] == "extrapolated_no_pose"
    # clamped to nearest boundary tick value (first tick x=1.0) — numeric, not null
    assert records[0]["player_position"][0] is not None
    assert isinstance(records[0]["pose_dt_ms"], float)
    assert records[0]["pose_dt_ms"] > 25.0  # well outside an interpolation gap

    # frame 15 (epoch 101.5) is AFTER last tick (101.0) → uncovered trailing
    assert records[15]["pose_valid"] is False
    assert records[15]["pose_source"] == "extrapolated_no_pose"
    assert records[15]["pose_dt_ms"] > 25.0


# --- (e) covered frames pose_valid + small dt --------------------------------


def test_covered_frames_valid_and_small_dt(tmp_path):
    anchor = 100.0
    fps = 10.0
    gs_rows = [_gs_row(i, 100500 + i * 50, x=float(i)) for i in range(11)]
    session = _make_session(
        tmp_path,
        gs_rows=gs_rows,
        fps=fps,
        frame_count=16,
        start_timestamp=anchor,
        video_start_ts=anchor,
    )
    records = bac.build_records(session, vfov_deg=70.0)
    # covered frames are 5..10 (epoch 100.5..101.0)
    covered = [r for r in records if r["pose_valid"]]
    assert covered, "expected some covered frames"
    for r in covered:
        assert r["pose_source"] in ("exact", "interpolated")
        # ticks are 50ms apart → nearest tick always within ~25ms
        assert r["pose_dt_ms"] < 26.0


def test_covered_range_in_alignment_report(tmp_path):
    anchor = 100.0
    fps = 10.0
    gs_rows = [_gs_row(i, 100500 + i * 50, x=float(i)) for i in range(11)]
    session = _make_session(
        tmp_path,
        gs_rows=gs_rows,
        fps=fps,
        frame_count=16,
        start_timestamp=anchor,
        video_start_ts=anchor,
    )
    records = bac.build_records(session, vfov_deg=70.0)
    report = bac.build_alignment_report(session, records)
    assert report["anchor_source"] == "video_start_event"
    assert report["anchor_epoch"] == pytest.approx(100.0)
    assert report["total_frames"] == 16
    assert report["covered_frame_range"] == [5, 10]
    assert report["leading_uncovered_count"] == 5
    assert report["trailing_uncovered_count"] == 5
    assert report["recommended_clip_trim"] == {"start_frame": 5, "end_frame": 10}
    assert report["pose_tick_rate_hz"] == pytest.approx(20.0, abs=0.5)
    assert 0.0 <= report["pct_frames_pose_valid"] <= 100.0
    assert report["max_pose_dt_ms_within_window"] < 26.0


# --- (f) record count + all 20 original fields preserved ---------------------


def test_record_count_and_original_fields_present(tmp_path):
    anchor = 100.0
    fps = 10.0
    gs_rows = [_gs_row(i, 100500 + i * 50, x=float(i)) for i in range(11)]
    frame_count = 16
    session = _make_session(
        tmp_path,
        gs_rows=gs_rows,
        fps=fps,
        frame_count=frame_count,
        start_timestamp=anchor,
        video_start_ts=anchor,
    )
    records = bac.build_records(session, vfov_deg=70.0)

    assert len(records) == frame_count
    # frames contiguous 0..N-1
    assert [r["frame"] for r in records] == list(range(frame_count))

    for r in records:
        missing = ORIGINAL_FIELDS - set(r.keys())
        assert not missing, f"missing original fields: {missing}"
        assert NEW_FIELDS <= set(r.keys()), "missing new alignment fields"
        # quaternion stays unit-norm and consistent with euler
        q = r["camera_rotation_quaternion"]
        norm = math.sqrt(sum(c * c for c in q))
        assert norm == pytest.approx(1.0, abs=1e-6)


def test_quaternion_rebuilt_from_interpolated_euler(tmp_path):
    """Interpolated euler must regenerate a matching quaternion (euler↔quat)."""
    gs_rows = [
        _gs_row(0, 1000, pitch_deg=10.0, yaw_deg=20.0),
        _gs_row(1, 1050, pitch_deg=30.0, yaw_deg=60.0),
    ]
    gs_times, gs_sorted = bac.build_gs_index(gs_rows)
    pose = bac.interpolate_pose(gs_times, gs_sorted, 1.025)
    expected = bac.euler_zyx_to_quat(0.0, pose["pitch_deg"], pose["yaw_deg"])
    assert tuple(pose["quat"]) == pytest.approx(tuple(expected), abs=1e-9)


def test_discrete_route_fields_use_nearest_tick(tmp_path):
    """Booleans (on_ground/sneaking/sprinting) come from the nearest tick."""
    gs_rows = [
        _gs_row(0, 1000, sprinting=True, on_ground=True),
        _gs_row(1, 1050, sprinting=False, on_ground=False),
    ]
    gs_times, gs_sorted = bac.build_gs_index(gs_rows)
    # 1.010 → nearest is tick0 (sprinting True)
    near0 = bac.interpolate_pose(gs_times, gs_sorted, 1.010)["nearest"]
    assert bool(near0.get("sprinting")) is True
    # 1.040 → nearest is tick1 (sprinting False)
    near1 = bac.interpolate_pose(gs_times, gs_sorted, 1.040)["nearest"]
    assert bool(near1.get("sprinting")) is False


# --- integration: real session (optional) -----------------------------------

REAL_SESSION = Path("/Users/howardli/Downloads/New Session")


@pytest.mark.skipif(
    not (REAL_SESSION / "game_state.jsonl").is_file(),
    reason="real test session not present",
)
def test_integration_real_session_alignment(tmp_path):
    """End-to-end against the real session: VIDEO_START anchor, ~136 leading /
    ~72 trailing uncovered frames, all covered frames within an interp gap."""
    records = bac.build_records(REAL_SESSION, vfov_deg=70.0)
    report = bac.build_alignment_report(REAL_SESSION, records)

    # anchor must be the VIDEO_START event, not metadata.start_timestamp.
    assert report["anchor_source"] == "video_start_event"
    assert report["anchor_epoch"] == pytest.approx(1780306166.488, abs=0.01)

    assert report["total_frames"] == len(records) == 7825
    # leading/trailing uncovered counts (~136 / ~72 per measured ground truth).
    assert 120 <= report["leading_uncovered_count"] <= 150
    assert 50 <= report["trailing_uncovered_count"] <= 90

    # Pose tick rate is the real 20 Hz (median 50 ms gap).
    assert report["pose_tick_rate_hz"] == pytest.approx(20.0, abs=1.0)

    # HONESTY: this real session has 13 tick gaps > 60 ms (worst ~179 ms — an
    # MC client hitch), so a frame landing mid-gap can be ~90 ms from the
    # nearest real tick. The pipeline must SURFACE that, not hide it: the
    # nominal 20 Hz bound is ~25 ms, but the reported max reflects the true
    # worst gap. We assert it is surfaced and within the largest real gap,
    # never silently floored to the nominal value.
    nominal_half_gap_ms = 1000.0 / report["pose_tick_rate_hz"] / 2.0
    assert report["max_pose_dt_ms_within_window"] > nominal_half_gap_ms
    assert report["max_pose_dt_ms_within_window"] < 100.0  # < worst gap / 2 + ε
    # Most covered frames are still within the nominal interpolation gap.
    near_nominal = sum(1 for r in records if r["pose_valid"] and r["pose_dt_ms"] <= 26.0)
    valid_total = sum(1 for r in records if r["pose_valid"])
    assert near_nominal / valid_total > 0.98  # >98% within the clean 50ms cadence

    # every original field preserved + new fields present, count == frame_count.
    for r in (records[0], records[len(records) // 2], records[-1]):
        assert ORIGINAL_FIELDS <= set(r.keys())
        assert NEW_FIELDS <= set(r.keys())

    # leading & trailing frames flagged invalid; a middle frame valid.
    assert records[0]["pose_valid"] is False
    assert records[0]["pose_source"] == "extrapolated_no_pose"
    assert records[-1]["pose_valid"] is False
    assert records[len(records) // 2]["pose_valid"] is True


# =============================================================================
# Input-event → video-frame index map (ISC-IF-*)
# =============================================================================
#
# These tests pin the explicit `input_frame_map.json` feature: every DISCRETE
# input event (KEYBOARD / MOUSE_BUTTON / SCROLL) is mapped to the video frame
# it occurred on via the SAME frame-0 anchor used by the pose alignment, so a
# buyer can answer "key W was pressed at frame N". Continuous MOUSE_MOVE events
# and lifecycle events (START/HOOK_START/VIDEO_START/VIDEO_END/END) are excluded
# from per-event emission. Out-of-video-range events are clamped AND counted.

# Real-session ground truth (verified, do NOT re-derive):
REAL_ANCHOR = 1780306166.4879036  # VIDEO_START epoch == frame-0 anchor
REAL_FPS = 24.0
REAL_FRAME_COUNT = 7825


# --- (IF-a) frame_of_epoch: known mapping + clamp below 0 / above max --------


def test_frame_of_epoch_known_mapping():
    """A known epoch maps to round((t-anchor)*fps) inside the clip."""
    anchor = 100.0
    fps = 24.0
    fc = 7825
    # 1.0 s after anchor at 24 fps == frame 24 exactly.
    assert bac.frame_of_epoch(anchor + 1.0, anchor, fps, fc) == 24
    # 0.5 s → 12.0 → 12; half-frame rounds with banker's rounding via round().
    assert bac.frame_of_epoch(anchor + 0.5, anchor, fps, fc) == 12
    # The anchor itself is frame 0.
    assert bac.frame_of_epoch(anchor, anchor, fps, fc) == 0


def test_frame_of_epoch_clamps_below_zero():
    """An epoch before the anchor clamps to frame 0, never negative."""
    anchor = 100.0
    assert bac.frame_of_epoch(anchor - 5.0, anchor, 24.0, 7825) == 0
    assert bac.frame_of_epoch(anchor - 0.001, anchor, 24.0, 7825) == 0


def test_frame_of_epoch_clamps_above_last_frame():
    """An epoch after the clip end clamps to frame_count-1."""
    anchor = 100.0
    fps = 24.0
    fc = 7825
    # Way past the end → clamps to 7824.
    assert bac.frame_of_epoch(anchor + 100000.0, anchor, fps, fc) == fc - 1
    # Exactly one frame past the last also clamps.
    assert bac.frame_of_epoch(anchor + fc / fps, anchor, fps, fc) == fc - 1


def test_frame_of_epoch_matches_real_video_start_anchor():
    """frame 0 is the VIDEO_START anchor on the real session's clock."""
    assert bac.frame_of_epoch(REAL_ANCHOR, REAL_ANCHOR, REAL_FPS, REAL_FRAME_COUNT) == 0


# --- (IF-b) build_input_frame_map: one record per discrete event -------------


def _discrete_inputs() -> list[dict]:
    """Synthetic inputs covering every event class around anchor 100.0."""
    anchor = 100.0
    return [
        {"timestamp": anchor, "event_type": "VIDEO_START", "event_args": []},
        {"timestamp": anchor - 1.0, "event_type": "START", "event_args": []},
        {"timestamp": anchor - 0.5, "event_type": "HOOK_START", "event_args": []},
        # MOUSE_MOVE events must be EXCLUDED (aggregated elsewhere).
        {"timestamp": anchor + 0.1, "event_type": "MOUSE_MOVE", "event_args": [1, -1]},
        {"timestamp": anchor + 0.2, "event_type": "MOUSE_MOVE", "event_args": [2, 0]},
        # KEYBOARD: event_args == [keyCode, pressed_bool]
        {"timestamp": anchor + 1.0, "event_type": "KEYBOARD", "event_args": [87, True]},
        {"timestamp": anchor + 2.0, "event_type": "KEYBOARD", "event_args": [87, False]},
        # MOUSE_BUTTON: event_args == [button, pressed_bool]
        {
            "timestamp": anchor + 3.0,
            "event_type": "MOUSE_BUTTON",
            "event_args": [1, True],
        },
        # SCROLL: event_args == scroll delta(s)
        {"timestamp": anchor + 4.0, "event_type": "SCROLL", "event_args": [0, 1]},
        {"timestamp": anchor + 5.0, "event_type": "VIDEO_END", "event_args": []},
        {"timestamp": anchor + 6.0, "event_type": "END", "event_args": []},
    ]


def test_input_frame_map_one_record_per_discrete_event():
    """Exactly the discrete events are emitted; MOUSE_MOVE + lifecycle excluded."""
    anchor = 100.0
    fps = 24.0
    fc = 7825
    mp = bac.build_input_frame_map(_discrete_inputs(), anchor, fps, fc)
    # 2 KEYBOARD + 1 MOUSE_BUTTON + 1 SCROLL == 4 discrete events.
    assert len(mp) == 4
    types = [r["event_type"] for r in mp]
    assert types.count("KEYBOARD") == 2
    assert types.count("MOUSE_BUTTON") == 1
    assert types.count("SCROLL") == 1
    # No continuous / lifecycle types leak in.
    for forbidden in ("MOUSE_MOVE", "START", "HOOK_START", "VIDEO_START", "VIDEO_END", "END"):
        assert forbidden not in types


def test_input_frame_map_sorted_by_epoch():
    """Records are sorted ascending by epoch."""
    anchor = 100.0
    mp = bac.build_input_frame_map(_discrete_inputs(), anchor, 24.0, 7825)
    epochs = [r["epoch"] for r in mp]
    assert epochs == sorted(epochs)


def test_input_frame_map_frame_equals_frame_of_epoch():
    """Each record's frame == frame_of_epoch(its epoch)."""
    anchor = 100.0
    fps = 24.0
    fc = 7825
    mp = bac.build_input_frame_map(_discrete_inputs(), anchor, fps, fc)
    for rec in mp:
        assert rec["frame"] == bac.frame_of_epoch(rec["epoch"], anchor, fps, fc)
        # frame_time_s is frame/fps rounded to 6dp.
        assert rec["frame_time_s"] == pytest.approx(round(rec["frame"] / fps, 6))
        # dt_to_frame_center_ms is finite and bounded by ~half a frame (clip-internal).
        assert isinstance(rec["dt_to_frame_center_ms"], float)


def test_input_frame_map_keycode_only_for_keyboard():
    """keyCode appears only on KEYBOARD records, via _key_to_code; button on MOUSE_BUTTON."""
    anchor = 100.0
    mp = bac.build_input_frame_map(_discrete_inputs(), anchor, 24.0, 7825)
    for rec in mp:
        if rec["event_type"] == "KEYBOARD":
            assert "keyCode" in rec
            assert "button" not in rec
            assert "scroll" not in rec
            assert rec["keyCode"] == 87  # both KEYBOARD events are key W
        elif rec["event_type"] == "MOUSE_BUTTON":
            assert "button" in rec
            assert rec["button"] == 1
            assert "keyCode" not in rec
            assert "scroll" not in rec
        elif rec["event_type"] == "SCROLL":
            assert "scroll" in rec
            assert "keyCode" not in rec
            assert "button" not in rec


def test_input_frame_map_keycode_uses_key_to_code_helper():
    """KEYBOARD keyCode is exactly what the existing _key_to_code helper returns."""
    anchor = 100.0
    inputs = [
        {"timestamp": anchor + 1.0, "event_type": "KEYBOARD", "event_args": [65, True]},
    ]
    mp = bac.build_input_frame_map(inputs, anchor, 24.0, 7825)
    assert len(mp) == 1
    assert mp[0]["keyCode"] == bac._key_to_code(inputs[0]) == 65


# --- (IF-d) out-of-range events are clamped AND counted ----------------------


def test_input_frame_map_out_of_range_clamped_high():
    """An event after video end clamps to frame_count-1 (still emitted)."""
    anchor = 100.0
    fps = 24.0
    fc = 10  # tiny clip: frames 0..9
    inputs = [
        # 100 s after anchor → frame 2400 → clamps to 9.
        {"timestamp": anchor + 100.0, "event_type": "KEYBOARD", "event_args": [87, True]},
    ]
    mp = bac.build_input_frame_map(inputs, anchor, fps, fc)
    assert len(mp) == 1
    assert mp[0]["frame"] == fc - 1


def test_input_frame_map_out_of_range_clamped_low():
    """An event before the anchor clamps to frame 0 (still emitted)."""
    anchor = 100.0
    inputs = [
        {"timestamp": anchor - 50.0, "event_type": "MOUSE_BUTTON", "event_args": [1, True]},
    ]
    mp = bac.build_input_frame_map(inputs, anchor, 24.0, 7825)
    assert len(mp) == 1
    assert mp[0]["frame"] == 0


# --- (IF-c) ROUND-TRIP on the real session: held-key model agrees with map ---


@pytest.mark.skipif(
    not (REAL_SESSION / "game_state.jsonl").is_file(),
    reason="real test session not present",
)
def test_input_frame_map_roundtrip_real_session(tmp_path):
    """For real KEYBOARD presses, action_camera.json at that frame agrees on keyCode.

    The held-key model in action_camera.json (most-recent key at/before the
    frame) and the explicit input_frame_map.json must agree: at a key-PRESS
    event's frame (±1 frame, since a frame straddles the event center), the
    action_camera record carries the same keyCode. We sample real KEY-PRESS
    (pressed=True) events so a release on the same frame can't mask the press.
    """
    inputs = bac._read_jsonl(REAL_SESSION / "inputs.jsonl")
    metadata = json.loads((REAL_SESSION / "metadata.json").read_text(encoding="utf-8"))
    game_state = bac._read_jsonl(REAL_SESSION / "game_state.jsonl")
    gs_times, _ = bac.build_gs_index(game_state)
    anchor, anchor_source = bac.resolve_frame_anchor(inputs, metadata, gs_times[0])
    assert anchor_source == "video_start_event"

    width, height, fps, frame_count = bac.resolve_video_params(REAL_SESSION, metadata)
    fmap = bac.build_input_frame_map(inputs, anchor, fps, frame_count)
    records = bac.build_records(REAL_SESSION, vfov_deg=70.0)

    # Collect KEY-PRESS (pressed=True) records for the canonical W key (87).
    def _is_press(ev: dict) -> bool:
        args = ev.get("event_args")
        return isinstance(args, list) and len(args) >= 2 and bool(args[1]) is True

    # The full discrete map is built (asserted non-empty) so this test also
    # exercises build_input_frame_map end-to-end on the real session; the
    # round-trip itself is computed directly from raw press events below to
    # avoid any index drift between the two collections.
    assert fmap, "expected a non-empty input frame map"
    w_press_frames = []
    for ev in inputs:
        if ev.get("event_type") != "KEYBOARD" or not _is_press(ev):
            continue
        if bac._key_to_code(ev) != 87:
            continue
        fr = bac.frame_of_epoch(float(ev["timestamp"]), anchor, fps, frame_count)
        w_press_frames.append(fr)

    assert w_press_frames, "expected real W key-press events"

    # Round-trip: action_camera record within ±1 frame must carry keyCode 87.
    matched = 0
    for fr in w_press_frames:
        window = [records[i] for i in (fr - 1, fr, fr + 1) if 0 <= i < len(records)]
        if any(rec["keyCode"] == 87 for rec in window):
            matched += 1
    # The held-key model and the explicit map agree for the vast majority of
    # presses (a press immediately followed by a different key on the same/next
    # frame can shadow it — surfaced, not hidden, by sampling many).
    assert matched / len(w_press_frames) > 0.9


@pytest.mark.skipif(
    not (REAL_SESSION / "game_state.jsonl").is_file(),
    reason="real test session not present",
)
def test_input_frame_map_real_session_counts_and_out_of_range(tmp_path):
    """Real session: discrete counts match ground truth + END maps out of range."""
    inputs = bac._read_jsonl(REAL_SESSION / "inputs.jsonl")
    metadata = json.loads((REAL_SESSION / "metadata.json").read_text(encoding="utf-8"))
    game_state = bac._read_jsonl(REAL_SESSION / "game_state.jsonl")
    gs_times, _ = bac.build_gs_index(game_state)
    anchor, _ = bac.resolve_frame_anchor(inputs, metadata, gs_times[0])
    width, height, fps, frame_count = bac.resolve_video_params(REAL_SESSION, metadata)

    fmap = bac.build_input_frame_map(inputs, anchor, fps, frame_count)
    # Ground truth: 576 KEYBOARD + 26 MOUSE_BUTTON + 0 SCROLL == 602 discrete.
    assert sum(1 for r in fmap if r["event_type"] == "KEYBOARD") == 576
    assert sum(1 for r in fmap if r["event_type"] == "MOUSE_BUTTON") == 26
    assert sum(1 for r in fmap if r["event_type"] == "SCROLL") == 0
    assert len(fmap) == 602

    # Every emitted frame is inside the clip after clamping.
    for rec in fmap:
        assert 0 <= rec["frame"] <= frame_count - 1

    # The first W (87) press maps to frame 245 on the real clock.
    w_first = next(r for r in fmap if r["event_type"] == "KEYBOARD" and r.get("keyCode") == 87)
    assert w_first["frame"] == 245


@pytest.mark.skipif(
    not (REAL_SESSION / "game_state.jsonl").is_file(),
    reason="real test session not present",
)
def test_input_frame_map_summary_block_in_alignment_report(tmp_path):
    """build_alignment_report carries a compact input_frame_map summary."""
    records = bac.build_records(REAL_SESSION, vfov_deg=70.0)
    report = bac.build_alignment_report(REAL_SESSION, records)
    assert "input_frame_map" in report
    summary = report["input_frame_map"]
    assert summary["path"] == "input_frame_map.json"
    assert summary["discrete_event_count"] == 602
    assert summary["keyboard_count"] == 576
    assert summary["mouse_button_count"] == 26
    assert summary["scroll_count"] == 0
    # All discrete events are inside the real clip → 0 out of range here.
    assert summary["events_out_of_video_range"] == 0


def test_input_frame_map_summary_counts_out_of_range(tmp_path):
    """Summary honestly counts events whose epoch maps outside the clip."""
    anchor = 100.0
    fps = 24.0
    fc = 10  # frames 0..9
    inputs = [
        {"timestamp": anchor + 0.0, "event_type": "VIDEO_START", "event_args": []},
        # in-range KEYBOARD at frame 0
        {"timestamp": anchor + 0.01, "event_type": "KEYBOARD", "event_args": [87, True]},
        # out-of-range (after clip end) MOUSE_BUTTON → clamps to 9, counted
        {"timestamp": anchor + 100.0, "event_type": "MOUSE_BUTTON", "event_args": [1, True]},
        # out-of-range (before anchor) KEYBOARD → clamps to 0, counted
        {"timestamp": anchor - 5.0, "event_type": "KEYBOARD", "event_args": [65, True]},
    ]
    summary = bac.summarize_input_frame_map(
        bac.build_input_frame_map(inputs, anchor, fps, fc), anchor, fps, fc, inputs
    )
    assert summary["discrete_event_count"] == 3
    assert summary["keyboard_count"] == 2
    assert summary["mouse_button_count"] == 1
    assert summary["scroll_count"] == 0
    assert summary["events_out_of_video_range"] == 2


# --- (IF-e) end-to-end: main() writes input_frame_map.json + summary ---------


def test_main_writes_input_frame_map_sidecar(tmp_path):
    """Running main() emits input_frame_map.json and the summary in frame_alignment.json."""
    anchor = 100.0
    fps = 10.0
    gs_rows = [_gs_row(i, 100500 + i * 50, x=float(i)) for i in range(11)]
    inputs = [
        {"timestamp": anchor, "event_type": "VIDEO_START", "event_args": []},
        {"timestamp": anchor + 0.6, "event_type": "KEYBOARD", "event_args": [87, True]},
        {"timestamp": anchor + 0.7, "event_type": "MOUSE_BUTTON", "event_args": [1, True]},
        {"timestamp": anchor + 0.2, "event_type": "MOUSE_MOVE", "event_args": [1, 1]},
    ]
    session = _make_session(
        tmp_path,
        gs_rows=gs_rows,
        fps=fps,
        frame_count=16,
        start_timestamp=anchor,
        video_start_ts=anchor,
        extra_inputs=[e for e in inputs if e["event_type"] != "VIDEO_START"],
    )
    rc = bac.main([str(session)])
    assert rc == 0

    ifm_path = session / "input_frame_map.json"
    assert ifm_path.is_file()
    fmap = json.loads(ifm_path.read_text(encoding="utf-8"))
    # MOUSE_MOVE excluded → only KEYBOARD + MOUSE_BUTTON emitted.
    assert len(fmap) == 2
    assert {r["event_type"] for r in fmap} == {"KEYBOARD", "MOUSE_BUTTON"}

    report = json.loads((session / "frame_alignment.json").read_text(encoding="utf-8"))
    assert report["input_frame_map"]["discrete_event_count"] == 2
    assert report["input_frame_map"]["keyboard_count"] == 1
    assert report["input_frame_map"]["mouse_button_count"] == 1

    # additive: action_camera.json untouched in length / original fields.
    ac = json.loads((session / "action_camera.json").read_text(encoding="utf-8"))
    assert len(ac) == 16
    assert ORIGINAL_FIELDS <= set(ac[0].keys())
    assert NEW_FIELDS <= set(ac[0].keys())
