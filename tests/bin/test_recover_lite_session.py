from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
)

import bin.recover_lite_session as recover_mod  # noqa: E402

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="recover_lite_session requires ffmpeg/ffprobe",
)


def _write_mini_video(path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=30",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )


def _write_action_camera(path: Path) -> None:
    frames = []
    for idx in range(30):
        frames.append(
            {
                "frame": idx,
                "time": f"2026-05-28 10:07:{idx / 30:06.3f}",
                "fps": 30.0,
                "route_type": 1,
                "mouse_x": [0.5],
                "mouse_y": [0.5],
                "mouse_dx": [0.0],
                "mouse_dy": [0.0],
                "keyCode": [87] if idx % 5 == 0 else [],
                "camera_position": [float(idx), 65.0, float(idx) / 2.0],
                "camera_rotation_oula": [5.0, 190.0, 0.0],
                "camera_rotation_quaternion": [0.0, 0.0871557, 0.0, 0.9961947],
                "camera_Follow Offset": [0.0, 1.6, -3.0],
                "camera_intrinsics": {"fx": 64.0, "fy": 64.0, "cx": 80.0, "cy": 45.0},
                "camera_speed": [1.0, 0.0, 1.0],
                "player_position": [float(idx), 64.0, float(idx) / 2.0],
                "player_rotation_oula": [5.0, 190.0, 0.0],
                "player_rotation_quaternion": [0.0, 0.0871557, 0.0, 0.9961947],
                "player_speed": [1.0, 0.0, 1.0],
                "metric_scale": 1.0,
                "_dimension": "minecraft:overworld",
                "_on_ground": True,
            }
        )
    path.write_text(json.dumps(frames), encoding="utf-8")


def test_depth_scaffold_frame_count_uses_video_duration_and_cap() -> None:
    assert recover_mod._depth_scaffold_frame_count(230.0) == 1150
    assert recover_mod._depth_scaffold_frame_count(0.2) == 1
    assert recover_mod._depth_scaffold_frame_count(361.0) == 1800


def test_step5d_depth_scaffold_uses_video_duration_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sess = tmp_path / "clip"
    sess.mkdir()

    monkeypatch.setattr(
        recover_mod,
        "_video_info",
        lambda _sess: {"duration": 230.0, "width": 160, "height": 90},
    )

    def fake_depth_base(depth_dir: Path, width: int, height: int) -> Path:
        assert (width, height) == (160, 90)
        base = depth_dir / ".recovered_depth_base"
        base.write_bytes(b"fake-exr")
        return base

    monkeypatch.setattr(recover_mod, "_make_depth_base", fake_depth_base)

    recover_mod.step5d_depth_scaffold(sess)

    depth_dir = sess / "depth"
    assert len(list(depth_dir.glob("*.exr"))) == 1150
    assert (depth_dir / "000000.exr").exists()
    assert (depth_dir / "001149.exr").exists()
    assert not (depth_dir / "001150.exr").exists()

    source = json.loads((depth_dir / ".source").read_text(encoding="utf-8"))
    assert source["frame_count"] == 1150
    assert source["sample_fps"] == 5.0
    assert source["video_duration_sec"] == 230.0


def test_step5d_depth_scaffold_prunes_legacy_recovery_phantoms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sess = tmp_path / "clip"
    depth_dir = sess / "depth"
    depth_dir.mkdir(parents=True)
    for idx in range(1800):
        (depth_dir / f"{idx:06d}.exr").write_bytes(b"legacy")
    (depth_dir / ".source").write_text(
        json.dumps(
            {
                "frame_count": 1800,
                "source": recover_mod.DEPTH_SCAFFOLD_SOURCE,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        recover_mod,
        "_video_info",
        lambda _sess: {"duration": 230.0, "width": 160, "height": 90},
    )
    monkeypatch.setattr(
        recover_mod,
        "_make_depth_base",
        lambda depth_dir, width, height: depth_dir / ".recovered_depth_base",
    )

    recover_mod.step5d_depth_scaffold(sess)

    assert len(list(depth_dir.glob("*.exr"))) == 1150
    assert (depth_dir / "001149.exr").exists()
    assert not (depth_dir / "001150.exr").exists()
    source = json.loads((depth_dir / ".source").read_text(encoding="utf-8"))
    assert source["frame_count"] == 1150


def test_step6_run_audit_timeout_returns_shape_only_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sess = tmp_path / "clip-20260528-100703"
    sess.mkdir()
    for name in recover_mod.SHAPE_ONLY_ARTIFACTS:
        path = sess / name
        if name == "depth":
            path.mkdir()
        else:
            path.write_bytes(b"x")

    def fake_run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
        assert cmd[-1] == "--json"
        assert timeout == recover_mod.AUDIT_TIMEOUT_SEC
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

    monkeypatch.setattr(recover_mod, "_run", fake_run)

    result = recover_mod.step6_run_audit(sess)

    assert result["audit_status"] == "timeout"
    assert result["audit_mode"] == "shape-only"
    assert result["present"] == 12
    assert result["expected"] == 12
    assert result["missing"] == []
    assert (
        recover_mod._format_audit_result(result)
        == 'audit_status="timeout" shape-only=12/12 artifacts present'
    )
    fallback = json.loads((sess / "recovery_audit_fallback.json").read_text(encoding="utf-8"))
    assert fallback["audit_status"] == "timeout"
    assert fallback["present"] == 12
    assert fallback["expected"] == 12


def test_step2_metadata_backfills_existing_metadata_without_second_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sess = tmp_path / "clip-20260528-100703"
    sess.mkdir()
    _write_action_camera(sess / "action_camera.json")
    (sess / "inputs.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"timestamp_ms": 0, "event_type": "keyboard", "key": "W"}),
                json.dumps({"timestamp_ms": 100, "event_type": "keyboard", "key": "A"}),
                json.dumps({"timestamp_ms": 200, "event_type": "mouse_move", "mouseX": 80}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (sess / "session_manifest.json").write_text(
        json.dumps(
            {
                "session_id": "a8ffc5a6-17ef-4279-a38e-8bc16467d2d2",
                "recorder_version": "lite-v0.21.0",
                "recorder_commit": "abc123",
                "start_time": "2026-05-28T10:07:03.420606",
                "frame_count": 30,
                "fps": 30.0,
            }
        ),
        encoding="utf-8",
    )
    (sess / "systeminfo.json").write_text(
        json.dumps(
            {
                "gameProcessName": "javaw.exe",
                "width": 160,
                "height": 90,
                "gpu": "NVIDIA GeForce RTX 4090",
                "cpu": "AMD Ryzen 9",
                "cpuCores": 16,
                "ram_gb": 64,
            }
        ),
        encoding="utf-8",
    )
    existing_metadata = {
        "session_id": "11111111-1111-4111-8111-111111111111",
        "recorder_version": "preserved-version",
        "duration": 2.5,
        "hardware_specs": {"cpu": {"cores": 24}},
        "recorder_extra": {"encoder": "nvenc"},
    }
    (sess / "metadata.json").write_text(
        json.dumps(existing_metadata, indent=2) + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        recover_mod,
        "_video_info",
        lambda _session: {
            "duration": 1.0,
            "fps": 30.0,
            "width": 160,
            "height": 90,
            "frame_count": 30,
        },
    )

    recover_mod.step2_metadata(sess)

    metadata_path = sess / "metadata.json"
    first_text = metadata_path.read_text(encoding="utf-8")
    metadata = json.loads(first_text)
    assert metadata["session_id"] == existing_metadata["session_id"]
    assert metadata["recorder_version"] == "preserved-version"
    assert metadata["duration"] == 2.5
    assert metadata["duration_ns"] == 2_500_000_000
    assert metadata["recorder_commit"] == "abc123"
    assert metadata["hardware_specs"]["cpu"]["cores"] == 24
    assert metadata["hardware_specs"]["gpus"][0]["vendor"] == "NVIDIA"
    assert metadata["input_stats"]["total_keyboard_events"] == 2
    assert metadata["input_stats"]["wasd_apm"] > 0
    assert metadata["recorder_extra"]["encoder"] == "nvenc"
    assert metadata["recorder_extra"]["window_capture"] is False

    recover_mod.step2_metadata(sess)

    assert metadata_path.read_text(encoding="utf-8") == first_text


def test_start_time_uses_min_authoritative_timestamp_when_inputs_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = dt.datetime(2026, 5, 28, 20, 0, 0, tzinfo=dt.timezone.utc)
    dir_time = dt.datetime(2026, 5, 29, 18, 35, 0, tzinfo=dt.timezone.utc)
    systeminfo_start = dt.datetime(2026, 5, 27, 18, 35, 0, tzinfo=dt.timezone.utc)
    mp4_ctime = dt.datetime(2026, 5, 28, 18, 40, 0, tzinfo=dt.timezone.utc)
    sess = tmp_path / f"clip-{dir_time:%Y%m%d-%H%M%S}"
    sess.mkdir()

    monkeypatch.setattr(recover_mod, "_video_ctime_utc", lambda _session: mp4_ctime)

    actual = recover_mod._start_time_from_session(
        sess,
        {"start_time": recover_mod._iso_utc(now - dt.timedelta(days=7))},
        {"start_time": recover_mod._iso_utc(systeminfo_start)},
        now=now,
    )

    assert actual == systeminfo_start
    assert actual < dir_time
    assert actual < mp4_ctime
    assert actual <= now


def test_start_time_caps_authoritative_timestamp_to_now(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = dt.datetime(2026, 5, 28, 20, 0, 0, tzinfo=dt.timezone.utc)
    dir_time = now + dt.timedelta(hours=3)
    systeminfo_start = now + dt.timedelta(minutes=5)
    mp4_ctime = now + dt.timedelta(hours=1)
    sess = tmp_path / f"clip-{dir_time:%Y%m%d-%H%M%S}"
    sess.mkdir()

    monkeypatch.setattr(recover_mod, "_video_ctime_utc", lambda _session: mp4_ctime)

    actual = recover_mod._start_time_from_session(
        sess,
        {},
        {"start_time": recover_mod._iso_utc(systeminfo_start)},
        now=now,
    )

    assert actual == now


@requires_ffmpeg
def test_recover_lite_session_derives_prd_shape(tmp_path: Path) -> None:
    sess = tmp_path / "clip-20260528-100703"
    sess.mkdir()
    _write_mini_video(sess / "video.mp4")
    _write_action_camera(sess / "action_camera.json")
    (sess / "inputs.jsonl").write_text(
        "\n".join(
            json.dumps({"timestamp_ms": idx * 8, "event_type": "mouse_move", "mouseX": 80})
            for idx in range(60)
        )
        + "\n",
        encoding="utf-8",
    )
    (sess / "session_manifest.json").write_text(
        json.dumps(
            {
                "session_id": "a8ffc5a6-17ef-4279-a38e-8bc16467d2d2",
                "recorder_version": "lite-v0.21.0",
                "start_time": "2026-05-28T10:07:03.420606",
                "frame_count": 30,
                "fps": 30.0,
            }
        ),
        encoding="utf-8",
    )
    (sess / "systeminfo.json").write_text(
        json.dumps(
            {
                "gameProcessName": "javaw.exe",
                "width": 160,
                "height": 90,
                "recordedAt": "20260528-100703",
                "recorderVersion": "lite-v0.10.0",
            }
        ),
        encoding="utf-8",
    )
    (sess / "gameinfo.xlsx").write_bytes(b"fake xlsx placeholder")
    (sess / "depth_postprocess.json").write_text("{}", encoding="utf-8")
    (sess / "intrinsics.yaml").write_text("fx: 64\nfy: 64\n", encoding="utf-8")
    (sess / "depth_manifest.json").write_text("{}", encoding="utf-8")

    assert recover_mod.main(["recover_lite_session.py", str(sess)]) == 0

    for name in [
        "recording.mp4",
        "metadata.json",
        "game_state.jsonl",
        "audio.flac",
        "audio_check.json",
        "frames.jsonl",
        "fps_log.json",
        "input_latency.json",
        "MANIFEST.json",
    ]:
        assert (sess / name).exists(), name

    assert (sess / "depth").is_dir()
    assert (sess / "depth" / ".source").exists()
    assert len(list((sess / "depth").glob("*.exr"))) == 5

    metadata = json.loads((sess / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["recorder"] == "lite-recovered"
    assert metadata["device_id"]
    assert metadata["recorder_extra"]["window_capture"] is False

    action = json.loads((sess / "action_camera.json").read_text(encoding="utf-8"))
    assert isinstance(action[0]["time"], float)
    assert isinstance(action[0]["mouse_x"], float)
    assert action[0]["camera_rotation_oula"][1] == -170.0

    assert sum(1 for _ in (sess / "game_state.jsonl").open(encoding="utf-8")) == 30
    assert sum(1 for _ in (sess / "frames.jsonl").open(encoding="utf-8")) == 30
