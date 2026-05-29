from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from bin import audit_lift_post_patches, canonical_pipeline
from bin.data_precision_audit import p2_mouse_camera_coherence


def test_audit_lift_treats_session_dir_timestamp_as_local_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    if not hasattr(time, "tzset"):
        pytest.skip("time.tzset is required to verify local timezone handling")

    old_tz = os.environ.get("TZ")

    def restore_tz() -> None:
        if old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old_tz
        time.tzset()

    request.addfinalizer(restore_tz)
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    time.tzset()

    session = tmp_path / "session_20240102_030405_fixture"
    session.mkdir()
    (session / "metadata.json").write_text(
        json.dumps({"recording_started_utc": "2999-01-01T00:00:00+00:00"}),
        encoding="utf-8",
    )

    changes = audit_lift_post_patches.patch_metadata(session)

    expected = dt.datetime(2024, 1, 1, 19, 4, 5, tzinfo=dt.timezone.utc)
    actual = dt.datetime.fromisoformat(changes["recording_started_utc"])
    assert actual == expected
    assert actual <= dt.datetime.now(dt.timezone.utc)


def _require_ffmpeg_tools() -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe not installed")


def _make_audio_mp4(path: Path, lavfi_source: str) -> None:
    _require_ffmpeg_tools()
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            lavfi_source,
            "-t",
            "0.5",
            "-c:a",
            "aac",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(result.stderr or f"ffmpeg exited with {result.returncode}")


def test_canonical_step3_writes_audio_check_for_audible_audio(tmp_path: Path) -> None:
    session = tmp_path / "session"
    session.mkdir()
    _make_audio_mp4(session / "recording.mp4", "sine=frequency=1000:sample_rate=48000")

    canonical_pipeline.step3_extract_audio(session)

    check = json.loads((session / "audio_check.json").read_text(encoding="utf-8"))
    assert isinstance(check["rms_db"], (int, float))
    assert isinstance(check["snr_db"], (int, float))
    assert -80.0 < check["rms_db"] < 0.0
    assert check["snr_db"] >= 0.0
    assert check["is_silent"] is False


def test_canonical_step3_writes_audio_check_for_silent_audio(tmp_path: Path) -> None:
    session = tmp_path / "session"
    session.mkdir()
    _make_audio_mp4(session / "recording.mp4", "anullsrc=r=48000:cl=mono")

    canonical_pipeline.step3_extract_audio(session)

    check = json.loads((session / "audio_check.json").read_text(encoding="utf-8"))
    assert isinstance(check["rms_db"], (int, float))
    assert isinstance(check["snr_db"], (int, float))
    assert check["rms_db"] <= -100.0
    assert check["snr_db"] >= 0.0
    assert check["is_silent"] is True


def test_precision_audit_p2_reads_action_camera_mouse_dx() -> None:
    rows = []
    yaw = 0.0
    for frame in range(120):
        active = frame // 30 in {0, 2}
        if active:
            yaw += 1.0
        rows.append(
            {
                "time": frame / 30.0,
                "mouse_dx": 5.0 if active else 0.0,
                "camera_rotation_oula": [0.0, yaw, 0.0],
            }
        )

    result = p2_mouse_camera_coherence([], rows)

    assert result["ok"] is True
    assert result["windowed_mouse_yaw_correlation"] > 0.9
    assert result["windows_both_active"] == 2
