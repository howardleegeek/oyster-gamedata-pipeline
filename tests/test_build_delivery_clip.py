"""Tests for ``bin/build_delivery_clip.py``.

These tests define the contract for the buyer-ready "co-extensive" delivery
clip builder: video, pose (action_camera), inputs, and audio are all trimmed
to the exact frame window where real player pose exists, so EVERY delivered
frame has a real (interpolated) pose.

The tests use a tiny synthetic session generated with ffmpeg lavfi so they run
fast and hermetically (no dependency on the large real ``New Session``). The
real-session acceptance run lives in the task report, not here.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# --- Import the module under test by path (bin/ is not a package) -----------
_BIN = Path(__file__).resolve().parents[1] / "bin" / "build_delivery_clip.py"
_spec = importlib.util.spec_from_file_location("build_delivery_clip", _BIN)
assert _spec and _spec.loader
bdc = importlib.util.module_from_spec(_spec)
sys.modules["build_delivery_clip"] = bdc
_spec.loader.exec_module(bdc)


# --- Helpers ----------------------------------------------------------------

_HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
requires_ffmpeg = pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")

FPS = 24.0
TOTAL = 60  # synthetic source video frames
START = 12  # first pose-covered frame
END = 47  # last pose-covered frame (inclusive)
WINDOW = END - START + 1  # == 36


def _make_video(path: Path, frames: int, fps: float, w: int = 64, h: int = 48) -> None:
    """Generate a tiny CFR test video with exactly ``frames`` frames."""
    duration = frames / fps
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={duration}:size={w}x{h}:rate={fps}",
            "-frames:v",
            str(frames),
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def _make_audio(path: Path, seconds: float) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            "-c:a",
            "flac",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def _action_record(frame: int, fps: float, covered: bool) -> dict:
    """Build a single action_camera record matching the real schema."""
    t = round(frame / fps, 6)
    return {
        "frame": frame,
        "time": t,
        "timestamp": 1780306174.0 + t,
        "fps": fps,
        "route_type": 0,
        "mouse_x": 0.5,
        "mouse_y": 0.5,
        "mouse_dx": 0.0,
        "mouse_dy": 0.0,
        "keyCode": 17 if covered else 0,
        "camera_position": [float(frame), 1.62, 0.0],
        "camera_rotation_oula": [0.0, 0.0, 0.0],
        "camera_rotation_quaternion": [0.0, 0.0, 0.0, 1.0],
        "camera_intrinsics": {"fx": 50.0, "fy": 50.0, "cx": 32.0, "cy": 24.0},
        "camera_speed": 0.0,
        "player_position": [float(frame), 0.0, 0.0],
        "player_rotation_oula": [0.0, 0.0, 0.0],
        "player_rotation_quaternion": [0.0, 0.0, 0.0, 1.0],
        "player_speed": 0.0,
        "metric_scale": 1.0,
        "pose_valid": bool(covered),
        "pose_source": "interpolated" if covered else "extrapolated_no_pose",
        "pose_dt_ms": 10.0 if covered else 5000.0,
    }


def _write_session(
    root: Path,
    *,
    with_audio: bool = False,
    with_input_map: bool = False,
    with_depth: bool = False,
    video_name: str = "recording.mp4",
) -> Path:
    """Create a synthetic session and return its directory."""
    root.mkdir(parents=True, exist_ok=True)
    _make_video(root / video_name, TOTAL, FPS)

    records = [_action_record(f, FPS, covered=(START <= f <= END)) for f in range(TOTAL)]
    (root / "action_camera.json").write_text(json.dumps(records))

    alignment = {
        "anchor_source": "video_start_event",
        "fps": FPS,
        "total_frames": TOTAL,
        "covered_frame_range": [START, END],
        "recommended_clip_trim": {"start_frame": START, "end_frame": END},
    }
    (root / "frame_alignment.json").write_text(json.dumps(alignment))

    if with_audio:
        _make_audio(root / "audio.flac", TOTAL / FPS)

    if with_input_map:
        events = [
            # before window -> dropped
            {
                "event_type": "KEYBOARD",
                "frame": 3,
                "frame_time_s": round(3 / FPS, 6),
                "keyCode": 65,
                "epoch": 1.0,
            },
            # at window start -> kept, re-indexed to 0
            {
                "event_type": "KEYBOARD",
                "frame": START,
                "frame_time_s": round(START / FPS, 6),
                "keyCode": 87,
                "epoch": 2.0,
            },
            # middle -> kept
            {
                "event_type": "MOUSE_BUTTON",
                "frame": 30,
                "frame_time_s": round(30 / FPS, 6),
                "button": 1,
                "epoch": 3.0,
            },
            # at window end -> kept, re-indexed to WINDOW-1
            {
                "event_type": "KEYBOARD",
                "frame": END,
                "frame_time_s": round(END / FPS, 6),
                "keyCode": 83,
                "epoch": 4.0,
            },
            # after window -> dropped
            {
                "event_type": "KEYBOARD",
                "frame": 55,
                "frame_time_s": round(55 / FPS, 6),
                "keyCode": 68,
                "epoch": 5.0,
            },
        ]
        (root / "input_frame_map.json").write_text(json.dumps(events))

    if with_depth:
        depth = root / "zbuffer"
        depth.mkdir()
        for f in range(TOTAL):
            (depth / f"tick_{f:06d}.bin").write_bytes(f.to_bytes(2, "little"))

    return root


# --- Pure logic tests (no ffmpeg needed) ------------------------------------


def test_read_trim_range_reads_not_hardcodes(tmp_path: Path):
    align = {
        "fps": 24.0,
        "total_frames": 7825,
        "recommended_clip_trim": {"start_frame": 137, "end_frame": 7752},
    }
    p = tmp_path / "frame_alignment.json"
    p.write_text(json.dumps(align))
    start, end, fps, total = bdc.read_trim_range(p)
    assert (start, end, fps, total) == (137, 7752, 24.0, 7825)


def test_reindex_action_records_zero_based_and_pose_valid():
    src = [_action_record(f, FPS, covered=(START <= f <= END)) for f in range(TOTAL)]
    out = bdc.reindex_action_records(src, START, END, FPS)
    assert len(out) == WINDOW
    # frame 0..N-1, time == frame/fps
    for i, rec in enumerate(out):
        assert rec["frame"] == i
        assert rec["time"] == round(i / FPS, 6)
        assert rec["pose_valid"] is True
    # schema preserved (intrinsics + camera data kept)
    assert set(out[0].keys()) == set(src[0].keys())
    assert out[0]["camera_intrinsics"] == {"fx": 50.0, "fy": 50.0, "cx": 32.0, "cy": 24.0}
    # original frame START's payload follows into delivered frame 0
    assert out[0]["camera_position"] == [float(START), 1.62, 0.0]


def test_reindex_raises_if_any_pose_invalid():
    # window that includes an uncovered frame must blow up loudly
    src = [_action_record(f, FPS, covered=(START <= f <= END)) for f in range(TOTAL)]
    with pytest.raises(ValueError, match="pose_valid"):
        bdc.reindex_action_records(src, START - 1, END, FPS)


def test_reindex_input_events_dropped_and_reindexed():
    events = [
        {"event_type": "KEYBOARD", "frame": 3, "frame_time_s": 0.125, "keyCode": 65},
        {"event_type": "KEYBOARD", "frame": START, "frame_time_s": 0.5, "keyCode": 87},
        {"event_type": "MOUSE_BUTTON", "frame": 30, "frame_time_s": 1.25, "button": 1},
        {"event_type": "KEYBOARD", "frame": END, "frame_time_s": 1.95, "keyCode": 83},
        {"event_type": "KEYBOARD", "frame": 55, "frame_time_s": 2.29, "keyCode": 68},
    ]
    out = bdc.reindex_input_events(events, START, END, FPS)
    frames = [e["frame"] for e in out]
    assert frames == [0, 30 - START, END - START]  # dropped 3 and 55
    assert out[0]["frame_time_s"] == round(0 / FPS, 6)
    assert out[-1]["frame_time_s"] == round((END - START) / FPS, 6)
    # other fields preserved
    assert out[0]["keyCode"] == 87


# --- Full build tests (need ffmpeg) -----------------------------------------


@requires_ffmpeg
def test_build_minimal_session_frame_accurate(tmp_path: Path):
    session = _write_session(tmp_path / "min")
    manifest = bdc.build_delivery_clip(session)

    delivery = session / "delivery"
    assert delivery.is_dir()

    # action_camera delivered length == window
    delivered = json.loads((delivery / "action_camera.json").read_text())
    assert len(delivered) == WINDOW
    assert all(r["pose_valid"] is True for r in delivered)
    assert delivered[0]["frame"] == 0 and delivered[-1]["frame"] == WINDOW - 1

    # delivered video frame count (ffprobe) == window == action_camera length
    vframes = bdc.count_video_frames(delivery / "video.mp4")
    assert vframes == WINDOW == len(delivered)

    # resolution + fps preserved (no fabrication)
    probe = bdc.probe_stream(delivery / "video.mp4")
    assert [probe["width"], probe["height"]] == [64, 48]
    assert abs(probe["fps"] - FPS) < 0.01

    # manifest fields
    assert manifest["schema"] == "oyster.delivery_clip/v1"
    assert manifest["delivered_frame_count"] == WINDOW
    assert manifest["trim_frame_range"] == [START, END]
    assert manifest["source_total_frames"] == TOTAL
    assert manifest["fps"] == FPS
    assert manifest["resolution"] == [64, 48]
    assert manifest["trim_time_range_s"] == [
        round(START / FPS, 6),
        round((END + 1) / FPS, 6),
    ]
    assert manifest["artifacts"]["video"] == "video.mp4"
    assert manifest["artifacts"]["action_camera"] == "action_camera.json"
    # absent artifacts honestly recorded
    assert manifest["artifacts"]["audio"] == "absent"
    assert manifest["artifacts"]["input_frame_map"] == "absent"
    assert manifest["artifacts"]["depth"] == "absent"

    # manifest persisted on disk matches return value
    on_disk = json.loads((delivery / "delivery_manifest.json").read_text())
    assert on_disk == manifest


@requires_ffmpeg
def test_build_with_audio_input_and_depth(tmp_path: Path):
    session = _write_session(
        tmp_path / "full",
        with_audio=True,
        with_input_map=True,
        with_depth=True,
    )
    manifest = bdc.build_delivery_clip(session)
    delivery = session / "delivery"

    # audio trimmed to the window time range and present
    assert (delivery / "audio.flac").is_file()
    adur = bdc.audio_duration(delivery / "audio.flac")
    expected = (END + 1) / FPS - START / FPS
    assert abs(adur - expected) < 0.10  # flac frame granularity tolerance
    assert manifest["artifacts"]["audio"] == "audio.flac"

    # input_frame_map kept/re-indexed/dropped
    inp = json.loads((delivery / "input_frame_map.json").read_text())
    assert [e["frame"] for e in inp] == [0, 30 - START, END - START]
    assert manifest["artifacts"]["input_frame_map"] == "input_frame_map.json"

    # depth copied + re-indexed: window count, names start at 0
    dout = sorted((delivery / "zbuffer").glob("*.bin"))
    assert len(dout) == WINDOW
    assert dout[0].name == "tick_000000.bin"
    assert dout[-1].name == f"tick_{WINDOW - 1:06d}.bin"
    # content carried from source frame START (non-destructive copy)
    assert dout[0].read_bytes() == START.to_bytes(2, "little")
    assert manifest["artifacts"]["depth"] == "zbuffer"


@requires_ffmpeg
def test_originals_untouched(tmp_path: Path):
    session = _write_session(
        tmp_path / "immutable",
        with_audio=True,
        with_input_map=True,
        with_depth=True,
    )
    # snapshot every source file's bytes + mtime before building
    sources = [p for p in session.rglob("*") if p.is_file() and "delivery" not in p.parts]
    before = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in sources}

    bdc.build_delivery_clip(session)

    for p, (size, mtime) in before.items():
        assert p.exists(), f"source removed: {p}"
        assert p.stat().st_size == size, f"source size changed: {p}"
        assert p.stat().st_mtime_ns == mtime, f"source mtime changed: {p}"


@requires_ffmpeg
def test_alternate_video_name(tmp_path: Path):
    session = _write_session(tmp_path / "altname", video_name="video.mp4")
    bdc.build_delivery_clip(session)
    assert bdc.count_video_frames(session / "delivery" / "video.mp4") == WINDOW
