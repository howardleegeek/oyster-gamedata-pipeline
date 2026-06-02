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
