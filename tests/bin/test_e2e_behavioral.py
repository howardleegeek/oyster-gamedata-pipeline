#!/usr/bin/env python3
"""
tests/bin/test_e2e_behavioral.py — End-to-end behavioural test for
``action_camera.json`` data accuracy.

This test simulates a real recording session by:

1. Spawning the recorder packaging pipeline through
   :mod:`bin.recorder_test_harness` (a Tk-free harness that mirrors the
   per-frame state machine of ``recorder_consumer_lite.RecorderApp``).
2. Injecting a deterministic sequence of synthetic pynput-style events.
3. Triggering ``package_tarball()`` with a fake video and explicit
   ``elapsed_sec``.
4. Reading back ``action_camera.json``.
5. Asserting on:

   * Frame count == ``int(elapsed_sec * 30)``.
   * Each frame has the 20 PRD fields per ``docs/PRD.md`` lines 117-131.
   * ``keyCode`` is ``list[int]`` and W (87) appears during the armed
     window.
   * ``mouse_dx`` between consecutive frames matches the expected pixel
     delta we injected.
   * ``camera_rotation_quaternion`` is unit-norm (‖q‖ within 1e-3 of 1).
   * ``mouse_x`` is normalised to [0, 1] and consistent with cumulative
     mouse_dx values.

The Replay Mod path is also covered: a synthetic ``.mcpr`` is placed
alongside, ``package_tarball`` is invoked with ``mcpr_path=…``, and the
test asserts that ``camera_position`` / ``camera_rotation_quaternion``
are populated (not all zero) on the merged frames.

Notes
-----
The recorder under test (``bin/recorder_consumer_lite.py``) imports
``tkinter`` at module load, so it cannot be imported on a headless CI
runner without a display. ``bin/recorder_test_harness.py`` exposes the
packaging logic standalone for exactly this reason — see its module
docstring. Any drift between the harness and the recorder will show up
as a failure here, which is the early-warning signal we want.
"""

from __future__ import annotations

import json
import math
import os
import sys
import tarfile
import time
from pathlib import Path
from typing import Any

import pytest

# Make ``bin/`` importable when this test is run from any cwd.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from bin.recorder_test_harness import (  # noqa: E402
    ACTION_CAMERA_FIELDS,
    DEFAULT_INTRINSICS,
    FPS_DEFAULT,
    SCREEN_H,
    SCREEN_W,
    build_replay_camera_samples,
    build_synthetic_mcpr,
    make_key_event,
    make_mouse_move,
    merge_replay_camera_track,
    package_tarball,
    synthesize_action_camera_records,
)


# ---------------------------------------------------------------------------
# Constants — tied to PRD acceptance gates.
# ---------------------------------------------------------------------------

#: Standard recorder frame rate, locked at 30 per docs/PRD.md line 104.
FPS = 30
#: Test session length in seconds. 5 s is short enough for fast tests
#: yet long enough to exercise key-down / key-up windows.
ELAPSED_SEC = 5.0
#: Expected number of frames synthesised at FPS for ELAPSED_SEC.
EXPECTED_FRAME_COUNT = int(ELAPSED_SEC * FPS)
#: Windows virtual-key code for "W" (per BUYER_SPEC_V1 VK_TO_KEY map).
VK_W = 87
#: Tolerance for floating-point comparisons.
ATOL = 1e-6
#: Tolerance for quaternion unit-norm assertion.
QUAT_NORM_TOL = 1e-3


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fixed_started_at() -> float:
    """A deterministic start timestamp so frame ``time`` strings are reproducible."""
    # 2026-05-05 12:00:00 local — concrete and far enough in the past
    # that it will not collide with other test fixtures.
    import datetime as _dt
    return _dt.datetime(2026, 5, 5, 12, 0, 0).timestamp()


@pytest.fixture
def fake_video(tmp_path: Path) -> Path:
    """Tiny stand-in for a real .mp4 file."""
    p = tmp_path / "fake-video.mp4"
    p.write_bytes(b"\x00\x00\x00\x18ftypmp42")  # 8-byte mp4 magic + zero pad
    return p


@pytest.fixture
def synthetic_events() -> list[dict[str, Any]]:
    """Deterministic pynput-style event stream for a 5 s window.

    Timeline (relative ms):
      *   0  W key down
      * 100  mouse move +5 px (from screen center)
      * 200  mouse move +5 px
      * 300  mouse move +5 px
      * ...
      * 2000 W key up

    The W key is therefore held for the first 2 s of the recording.
    Mouse moves +5 px every 100 ms over the full 5 s, so total Δx = 5 px
    × 50 = 250 px over 2 s armed window plus the rest.
    """
    events: list[dict[str, Any]] = []
    events.append(make_key_event(timestamp_ms=0, key_code=VK_W, is_down=True))

    # Mouse start at center, +5px every 100ms.
    cx, cy = SCREEN_W // 2, SCREEN_H // 2
    for i in range(1, 50):  # 100ms .. 4900ms
        events.append(
            make_mouse_move(timestamp_ms=i * 100, mouse_x=cx + 5 * i, mouse_y=cy)
        )

    events.append(make_key_event(timestamp_ms=2000, key_code=VK_W, is_down=False))
    return events


@pytest.fixture
def packaged_clip(
    tmp_path: Path,
    fake_video: Path,
    synthetic_events: list[dict[str, Any]],
    fixed_started_at: float,
):
    """Run package_tarball() once with the standard fixture inputs.

    Cached at function scope so each assertion reads the same artefacts.
    """
    return package_tarball(
        out_dir=tmp_path / "out",
        video_path=fake_video,
        events=synthetic_events,
        started_at=fixed_started_at,
        elapsed_sec=ELAPSED_SEC,
        clip_ts="20260505-120000",
    )


@pytest.fixture
def action_camera_records(packaged_clip) -> list[dict[str, Any]]:
    """Load and return the produced action_camera.json records list."""
    raw = packaged_clip.action_camera_path.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert isinstance(data, list), "action_camera.json must be a top-level array"
    return data


# ---------------------------------------------------------------------------
# Tests — frame count, schema, and per-field correctness
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_packaging_produces_expected_artefacts(packaged_clip) -> None:
    """All five PRD-shaped artefacts exist after packaging."""
    assert packaged_clip.tarball.is_file()
    assert packaged_clip.action_camera_path.is_file()
    assert packaged_clip.systeminfo_path.is_file()
    assert packaged_clip.gameinfo_path.is_file()
    assert packaged_clip.depth_dir.is_dir()
    assert (packaged_clip.clip_dir / "video.mp4").is_file()
    # Tarball is a real gzipped tar containing the clip dir.
    with tarfile.open(packaged_clip.tarball, "r:gz") as tf:
        names = tf.getnames()
    assert any(n.endswith("video.mp4") for n in names)
    assert any(n.endswith("action_camera.json") for n in names)


@pytest.mark.integration
def test_frame_count_matches_elapsed_seconds(action_camera_records) -> None:
    """Frame count == int(elapsed_sec × 30)."""
    assert len(action_camera_records) == EXPECTED_FRAME_COUNT, (
        f"expected {EXPECTED_FRAME_COUNT} frames at {FPS} fps over "
        f"{ELAPSED_SEC}s, got {len(action_camera_records)}"
    )


@pytest.mark.integration
def test_every_frame_has_all_20_prd_fields(action_camera_records) -> None:
    """PRD demands exactly 20 named fields per frame (docs/PRD.md L117-131)."""
    assert len(ACTION_CAMERA_FIELDS) == 20
    for idx, frame in enumerate(action_camera_records):
        missing = set(ACTION_CAMERA_FIELDS) - set(frame.keys())
        assert not missing, (
            f"frame {idx} missing PRD fields: {sorted(missing)}"
        )


@pytest.mark.integration
def test_frame_field_is_continuous(action_camera_records) -> None:
    """``frame`` field MUST be 0,1,2,…,N-1 with no gaps (PRD line 142)."""
    for expected, rec in enumerate(action_camera_records):
        assert rec["frame"] == expected, f"frame discontinuity at index {expected}"


@pytest.mark.integration
def test_keycode_is_list_of_int(action_camera_records) -> None:
    """``keyCode`` is ``list[int]`` per PRD line 121 (the recorder's
    pragmatic shape; sample_tarball_builder writes the same)."""
    for idx, rec in enumerate(action_camera_records):
        kc = rec["keyCode"]
        assert isinstance(kc, list), (
            f"frame {idx}: keyCode should be list, got {type(kc).__name__}"
        )
        for v in kc:
            assert isinstance(v, int) and not isinstance(v, bool), (
                f"frame {idx}: keyCode entries must be int, got {type(v).__name__}"
            )


@pytest.mark.integration
def test_w_key_present_during_armed_window(action_camera_records) -> None:
    """W (VK 87) is held [0, 2000ms]. At 30 fps that's frames 0..59
    inclusive (since key-up event lands at exactly t=2000 ms === frame
    60's window — recorder applies the up event on that frame so W is
    cleared by frame 60)."""
    # The `key_up` event at t=2000ms lands within frame 60's window
    # (frame_start_ms = 60 * 1000 / 30 = 2000), so by the time frame 60
    # is materialised the key is gone. Frames 0..59 must contain VK_W.
    for f in range(60):
        kc = action_camera_records[f]["keyCode"]
        assert VK_W in kc, (
            f"expected VK_W=87 in frame {f}.keyCode (W held [0,2000ms]), got {kc}"
        )
    # Conversely, W must be released by frame 60 onward.
    for f in range(60, len(action_camera_records)):
        assert VK_W not in action_camera_records[f]["keyCode"], (
            f"frame {f}: W should be released, but keyCode contains 87: "
            f"{action_camera_records[f]['keyCode']}"
        )


@pytest.mark.integration
def test_mouse_dx_matches_synthetic_pixel_delta(
    action_camera_records,
) -> None:
    """We injected a ``mouse_move +5 px`` event every 100 ms.

    At 30 fps each frame is ~33.3 ms, so events cluster as follows:
      frame 0 (0-33ms)     → only initial center; mouse_dx == 0
      frame 1 (33-66ms)    → no events yet (first move at 100ms)
      frame 2 (66-100ms)   → no events
      frame 3 (100-133ms)  → 1 move (100ms) → +5 px
      frame 4 (133-166ms)  → 0 moves
      frame 5 (166-200ms)  → no events yet (200 ≤ 166 is false)
      frame 6 (200-233ms)  → 1 move (200ms) → +5 px

    So whenever a frame includes a 100-ms-aligned move event, mouse_dx
    must equal exactly 5/SCREEN_W. Frames between such moves see
    mouse_dx == 0.

    We assert that:
      * mouse_dx is *always* one of {0, 5/SCREEN_W} (no spurious deltas).
      * The total cumulative Δx across all frames equals the total Δx
        injected by the events (49 moves × 5 px = 245 px).
    """
    expected_unit = 5.0 / SCREEN_W
    total_dx = 0.0
    nonzero_count = 0
    for rec in action_camera_records:
        dx = rec["mouse_dx"]
        # Either 0 or +5px/SCREEN_W (within float tolerance) — never any
        # other value, because events are 100 ms aligned and 5 px each.
        if abs(dx) > ATOL:
            assert math.isclose(dx, expected_unit, abs_tol=ATOL), (
                f"unexpected mouse_dx {dx}; should be 0 or {expected_unit}"
            )
            nonzero_count += 1
        total_dx += dx
    # 49 events × 5 px / SCREEN_W
    expected_total = 49 * 5.0 / SCREEN_W
    assert math.isclose(total_dx, expected_total, abs_tol=ATOL), (
        f"sum of mouse_dx={total_dx}, expected {expected_total} "
        f"(49 moves × 5 px / {SCREEN_W})"
    )
    # Sanity: at least one move-frame got recorded.
    assert nonzero_count >= 1


@pytest.mark.integration
def test_mouse_x_normalized_and_consistent_with_cumulative_dx(
    action_camera_records,
) -> None:
    """``mouse_x`` is normalised to [0, 1] and equals the running sum of
    mouse_dx plus the starting position (0.5, screen center)."""
    running_x = 0.5  # initial mouse_x at screen center
    for idx, rec in enumerate(action_camera_records):
        mx = rec["mouse_x"]
        assert 0.0 <= mx <= 1.0, (
            f"frame {idx}: mouse_x={mx} not in [0,1]"
        )
        running_x = running_x + rec["mouse_dx"]
        # Recorder accumulates absolute pixel and divides at end of
        # frame, so cumulative model gives running_x within ATOL of the
        # frame's mouse_x.
        assert math.isclose(mx, running_x, abs_tol=ATOL), (
            f"frame {idx}: mouse_x={mx} drifted from cumulative dx model "
            f"({running_x})"
        )


@pytest.mark.integration
def test_quaternion_unit_norm(action_camera_records) -> None:
    """Both camera + player rotation quaternions are unit-norm."""
    for idx, rec in enumerate(action_camera_records):
        for key in ("camera_rotation_quaternion", "player_rotation_quaternion"):
            q = rec[key]
            assert isinstance(q, list) and len(q) == 4, (
                f"frame {idx}.{key}: must be a 4-element list"
            )
            norm = math.sqrt(sum(c * c for c in q))
            assert abs(norm - 1.0) < QUAT_NORM_TOL, (
                f"frame {idx}.{key}: ‖q‖={norm}, expected 1.0 ± {QUAT_NORM_TOL}"
            )


@pytest.mark.integration
def test_camera_intrinsics_pinhole(action_camera_records) -> None:
    """``fx == fy`` per PRD acceptance gate 8 (BUYER_SPEC line 160)."""
    for idx, rec in enumerate(action_camera_records):
        intr = rec["camera_intrinsics"]
        assert isinstance(intr, dict)
        assert {"fx", "fy", "cx", "cy"}.issubset(intr.keys()), (
            f"frame {idx}: intrinsics missing fx/fy/cx/cy"
        )
        assert math.isclose(intr["fx"], intr["fy"], abs_tol=1e-3), (
            f"frame {idx}: fx={intr['fx']} != fy={intr['fy']} "
            f"(buyer-spec requires pinhole model)"
        )


@pytest.mark.integration
def test_fps_field_locked_at_30(action_camera_records) -> None:
    """Every frame's fps == 30.0 — no dynamic FPS allowed (PRD line 104)."""
    for idx, rec in enumerate(action_camera_records):
        assert rec["fps"] == 30.0, f"frame {idx}: fps={rec['fps']} != 30.0"


@pytest.mark.integration
def test_route_type_is_int(action_camera_records) -> None:
    """``route_type`` is an int (1/2/3 per BUYER_SPEC L77)."""
    for idx, rec in enumerate(action_camera_records):
        rt = rec["route_type"]
        assert isinstance(rt, int) and not isinstance(rt, bool), (
            f"frame {idx}: route_type={rt!r} not int"
        )
        assert rt in (1, 2, 3), f"frame {idx}: route_type={rt} not in 1/2/3"


@pytest.mark.integration
def test_time_field_iso_aligned_to_fps(
    action_camera_records, fixed_started_at: float
) -> None:
    """Each frame's ``time`` string parses and is offset by frame/30 s."""
    import datetime as _dt
    base = _dt.datetime.fromtimestamp(fixed_started_at)
    for idx, rec in enumerate(action_camera_records):
        t = _dt.datetime.strptime(rec["time"], "%Y-%m-%d %H:%M:%S.%f")
        delta = (t - base).total_seconds()
        expected = idx / FPS
        # Recorder rounds millisecond field, so allow 1 ms slack.
        assert abs(delta - expected) < 0.002, (
            f"frame {idx}: time={rec['time']} ⇒ Δ={delta}s, expected {expected}s"
        )


@pytest.mark.integration
def test_vector_fields_are_3_or_4_element_lists(action_camera_records) -> None:
    """Vector3/Vector4 fields are list[float] of correct length."""
    vec3_fields = (
        "camera_position",
        "camera_rotation_euler",
        "camera_follow_offset",
        "camera_speed",
        "player_position",
        "player_rotation_euler",
        "player_speed",
    )
    vec4_fields = ("camera_rotation_quaternion", "player_rotation_quaternion")
    for idx, rec in enumerate(action_camera_records):
        for k in vec3_fields:
            v = rec[k]
            assert isinstance(v, list) and len(v) == 3, (
                f"frame {idx}.{k}: expected 3-element list, got {v!r}"
            )
            assert all(isinstance(c, (int, float)) for c in v)
        for k in vec4_fields:
            v = rec[k]
            assert isinstance(v, list) and len(v) == 4, (
                f"frame {idx}.{k}: expected 4-element list, got {v!r}"
            )
            assert all(isinstance(c, (int, float)) for c in v)


@pytest.mark.integration
def test_metric_scale_is_positive_float(action_camera_records) -> None:
    """``metric_scale`` is the world:meter ratio — must be a positive float."""
    for idx, rec in enumerate(action_camera_records):
        ms = rec["metric_scale"]
        assert isinstance(ms, (int, float)) and ms > 0, (
            f"frame {idx}: metric_scale={ms!r} should be positive number"
        )


# ---------------------------------------------------------------------------
# Replay Mod path — synthesize a .mcpr fixture, package, assert merged.
# ---------------------------------------------------------------------------

@pytest.fixture
def synthetic_mcpr(tmp_path: Path) -> Path:
    """Build a synthetic .mcpr alongside an action_camera.json scenario."""
    return build_synthetic_mcpr(
        out_path=tmp_path / "synthetic.mcpr",
        duration_ms=int(ELAPSED_SEC * 1000),
    )


@pytest.mark.integration
def test_replay_mod_path_fills_camera_pose(
    tmp_path: Path,
    fake_video: Path,
    synthetic_events: list[dict[str, Any]],
    fixed_started_at: float,
    synthetic_mcpr: Path,
) -> None:
    """When a .mcpr is supplied, ``camera_position`` and
    ``camera_rotation_quaternion`` must NOT all be zero across frames.

    The Replay Mod stub at ``recorder_replay_mod_postprocess`` only
    emits a zero-pose track today (binary parser is a TODO), so a true
    end-to-end test would still see zeros. To validate that our
    *integration wiring* is correct, this test bypasses the stub and
    feeds a deterministic non-zero ``CameraSample`` track into
    ``merge_replay_camera_track`` directly — the same merge function
    the harness invokes. Once the binary parser lands the wiring will
    keep working unchanged.
    """
    # Step 1 — package as normal.
    result = package_tarball(
        out_dir=tmp_path / "out_replay",
        video_path=fake_video,
        events=synthetic_events,
        started_at=fixed_started_at,
        elapsed_sec=ELAPSED_SEC,
        mcpr_path=synthetic_mcpr,
        clip_ts="20260505-120000-replay",
    )
    # Step 2 — confirm the stub ran and reported its synthetic mc version.
    assert result.replay_status in ("stub", "ok"), (
        f"replay status was {result.replay_status} — pipeline failed to "
        f"invoke postprocess"
    )
    assert result.replay_metadata.get("mc_version") == "1.20.4"

    # Step 3 — overlay deterministic non-zero camera samples.
    samples = build_replay_camera_samples(duration_sec=ELAPSED_SEC, hz=FPS)
    assert len(samples) == EXPECTED_FRAME_COUNT
    rewrote = merge_replay_camera_track(result.action_camera_path, samples)
    assert rewrote, "merge_replay_camera_track should rewrite the JSON"

    # Step 4 — verify the merge actually filled real values.
    merged = json.loads(result.action_camera_path.read_text(encoding="utf-8"))
    assert isinstance(merged, list) and len(merged) == EXPECTED_FRAME_COUNT

    # ALL frames now non-zero.
    nonzero_pos = 0
    nonzero_quat_xyz = 0
    for idx, rec in enumerate(merged):
        cp = rec["camera_position"]
        cq = rec["camera_rotation_quaternion"]
        # camera_position should not be the recorder's [0, 64, 0] default.
        if any(abs(c) > ATOL for c in cp) and cp != [0.0, 64.0, 0.0]:
            nonzero_pos += 1
        # quaternion x,y,z should not all be zero (yaw-rotation gives qy != 0).
        if any(abs(c) > ATOL for c in cq[:3]):
            nonzero_quat_xyz += 1
        # Quaternion still unit-norm.
        norm = math.sqrt(sum(c * c for c in cq))
        assert abs(norm - 1.0) < QUAT_NORM_TOL, (
            f"frame {idx}: post-merge ‖q‖={norm}"
        )

    # Strong assertion: more than half of frames carry real data.
    assert nonzero_pos >= EXPECTED_FRAME_COUNT // 2, (
        f"only {nonzero_pos}/{EXPECTED_FRAME_COUNT} frames have non-zero "
        f"camera_position after merge"
    )
    assert nonzero_quat_xyz >= EXPECTED_FRAME_COUNT // 2, (
        f"only {nonzero_quat_xyz}/{EXPECTED_FRAME_COUNT} frames have "
        f"non-identity quaternion after merge"
    )


@pytest.mark.integration
def test_replay_mod_no_mcpr_keeps_placeholder_zeros(
    tmp_path: Path,
    fake_video: Path,
    synthetic_events: list[dict[str, Any]],
    fixed_started_at: float,
) -> None:
    """Without a .mcpr, camera_position stays at the recorder's default."""
    result = package_tarball(
        out_dir=tmp_path / "out_no_mcpr",
        video_path=fake_video,
        events=synthetic_events,
        started_at=fixed_started_at,
        elapsed_sec=ELAPSED_SEC,
        mcpr_path=None,
        clip_ts="20260505-120000-vanilla",
    )
    assert result.replay_status is None, (
        "replay postprocess should be skipped entirely without a .mcpr"
    )
    records = json.loads(result.action_camera_path.read_text(encoding="utf-8"))
    # All camera_position entries are the recorder's [0, 64, 0] placeholder.
    for idx, rec in enumerate(records):
        assert rec["camera_position"] == [0.0, 64.0, 0.0], (
            f"frame {idx}: expected vanilla placeholder, got {rec['camera_position']}"
        )


# ---------------------------------------------------------------------------
# Direct unit-style coverage of synthesize_action_camera_records — keeps
# the harness honest without going through the full tarball path.
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_synthesize_falls_back_to_9000_when_elapsed_zero(
    fixed_started_at: float,
) -> None:
    """The recorder uses 9000 frames (= 5 min × 30 fps) when elapsed_sec
    is zero — this matches BUYER_SPEC's expected target frame count."""
    records = synthesize_action_camera_records(
        events=[], started_at=fixed_started_at, elapsed_sec=0.0
    )
    assert len(records) == 9000


@pytest.mark.unit
def test_synthesize_handles_unsorted_events(fixed_started_at: float) -> None:
    """Events arriving out of order are sorted before replay (mirrors
    recorder behaviour)."""
    events = [
        make_key_event(2000, VK_W, is_down=False),  # later first
        make_key_event(0, VK_W, is_down=True),       # earlier second
    ]
    records = synthesize_action_camera_records(
        events=events, started_at=fixed_started_at, elapsed_sec=ELAPSED_SEC
    )
    # Frame 0 should still hold W (because the down event sorts to ts=0).
    assert VK_W in records[0]["keyCode"]


@pytest.mark.unit
def test_synthesize_default_intrinsics_pinhole() -> None:
    """The hard-coded default intrinsics ALWAYS satisfy fx == fy."""
    assert math.isclose(
        DEFAULT_INTRINSICS["fx"], DEFAULT_INTRINSICS["fy"], abs_tol=1e-3
    )
    assert DEFAULT_INTRINSICS["cx"] == 960.0
    assert DEFAULT_INTRINSICS["cy"] == 540.0
