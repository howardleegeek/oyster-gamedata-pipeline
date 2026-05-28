from __future__ import annotations

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
    assert len(list((sess / "depth").glob("*.exr"))) == 1800

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
