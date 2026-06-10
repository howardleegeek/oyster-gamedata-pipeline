from __future__ import annotations

import json
import shutil
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest

BIN_DIR = Path(__file__).resolve().parents[2] / "bin"


def _install_tk_stubs() -> None:
    if "tkinter" in sys.modules and getattr(
        sys.modules["tkinter"], "_video_validation_stub", False
    ):
        return

    tk = types.ModuleType("tkinter")
    tk._video_validation_stub = True  # type: ignore[attr-defined]

    class _Widget:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def __getattr__(self, _name: str) -> Any:
            return lambda *a, **kw: None

    tk.Tk = type("Tk", (_Widget,), {"__init__": lambda self, *a, **kw: None})
    tk.Frame = tk.Label = tk.Button = tk.Checkbutton = _Widget
    tk.BooleanVar = type(
        "BooleanVar",
        (),
        {"__init__": lambda self, value=False: None, "get": lambda self: False},
    )
    tk.messagebox = types.SimpleNamespace(showerror=lambda *a, **kw: None)

    ttk = types.ModuleType("tkinter.ttk")
    ttk.Progressbar = _Widget
    tk.ttk = ttk

    sys.modules["tkinter"] = tk
    sys.modules["tkinter.ttk"] = ttk
    sys.modules["tkinter.messagebox"] = types.SimpleNamespace(showerror=lambda *a, **kw: None)


def _import_recorder_module() -> Any:
    _install_tk_stubs()
    if str(BIN_DIR) not in sys.path:
        sys.path.insert(0, str(BIN_DIR))
    sys.modules.pop("recorder_consumer_lite", None)

    import recorder_consumer_lite as m  # type: ignore[import-not-found]

    return m


def _require_ffmpeg_tools(monkeypatch: pytest.MonkeyPatch, m: Any) -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.skip("ffmpeg/ffprobe not installed")
    monkeypatch.setattr(m, "_FFMPEG", Path(ffmpeg))
    monkeypatch.setattr(m, "_FFPROBE", Path(ffprobe))


def _make_fixture_video(path: Path, lavfi: str, *, ffmpeg: Path) -> None:
    result = subprocess.run(
        [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            lavfi,
            "-c:v",
            "mpeg4",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(result.stderr or f"ffmpeg exited with code {result.returncode}")


def _mp4_box(name: bytes, payload: bytes = b"") -> bytes:
    return (8 + len(payload)).to_bytes(4, "big") + name + payload


def test_probe_duration_sec_uses_ffmpeg_stderr_when_ffprobe_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    m = _import_recorder_module()
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake mp4")
    monkeypatch.setattr(m, "_FFPROBE", tmp_path / "ffprobe.exe")
    monkeypatch.setattr(m, "_FFMPEG", tmp_path / "ffmpeg.exe")

    def _fake_run(cmd: list[str], **_kwargs: Any) -> Any:
        if cmd[0] == str(m._FFPROBE):
            raise FileNotFoundError("missing ffprobe")
        return types.SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="Input #0, mov\n  Duration: 00:01:02.50, start: 0.000000",
        )

    monkeypatch.setattr(m.subprocess, "run", _fake_run)

    assert m._probe_duration_sec(video) == pytest.approx(62.5)


def test_probe_duration_sec_reads_mp4_mvhd_when_tools_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    m = _import_recorder_module()
    video = tmp_path / "video.mp4"
    mvhd = (
        b"\x00\x00\x00\x00" + b"\x00" * 8 + (1000).to_bytes(4, "big") + (12500).to_bytes(4, "big")
    )
    video.write_bytes(_mp4_box(b"ftyp", b"isom") + _mp4_box(b"moov", _mp4_box(b"mvhd", mvhd)))

    def _missing_tool(*_args: Any, **_kwargs: Any) -> Any:
        raise FileNotFoundError("missing media tool")

    monkeypatch.setattr(m.subprocess, "run", _missing_tool)

    assert m._probe_duration_sec(video) == pytest.approx(12.5)


def test_validate_recorded_video_uses_ffmpeg_duration_when_ffprobe_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    m = _import_recorder_module()
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake mp4")
    monkeypatch.setattr(m, "_FFPROBE", tmp_path / "ffprobe.exe")
    monkeypatch.setattr(m, "_FFMPEG", tmp_path / "ffmpeg.exe")
    frame_calls: list[int] = []

    def _fake_run(cmd: list[str], **_kwargs: Any) -> Any:
        if cmd[0] == str(m._FFPROBE):
            raise FileNotFoundError("missing ffprobe")
        if "-vframes" in cmd:
            frame_calls.append(len(frame_calls))
            frame = bytes([len(frame_calls) * 40]) * (160 * 90)
            return types.SimpleNamespace(returncode=0, stdout=frame, stderr="")
        return types.SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="Input #0, mov\n  Duration: 00:00:06.00, start: 0.000000",
        )

    monkeypatch.setattr(m.subprocess, "run", _fake_run)

    valid, reason = m._validate_recorded_video(video)

    assert valid is True
    assert "unique frames" in reason


def test_silent_audio_fallback_missing_ffprobe_is_nonfatal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    m = _import_recorder_module()
    session_dir = tmp_path / "clip"
    session_dir.mkdir()
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake mp4")
    monkeypatch.setattr(m, "_FFPROBE", tmp_path / "ffprobe.exe")
    monkeypatch.setattr(m, "_FFMPEG", tmp_path / "ffmpeg.exe")

    def _fake_run(cmd: list[str], **_kwargs: Any) -> Any:
        if cmd[0] == str(m._FFPROBE):
            raise FileNotFoundError("missing ffprobe")
        if "-f" in cmd and "lavfi" in cmd:
            Path(cmd[-1]).write_bytes(b"fake flac")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        return types.SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="Input #0, mov\n  Duration: 00:00:07.25, start: 0.000000",
        )

    monkeypatch.setattr(m.subprocess, "run", _fake_run)

    m._generate_silent_audio_fallback(session_dir, video)

    payload = json.loads((session_dir / "audio_check.json").read_text(encoding="utf-8"))
    assert payload["audio_source"] == "silent_fallback"
    assert payload["duration_sec"] == pytest.approx(7.25)
    assert (session_dir / "audio.flac").is_file()


def test_silent_audio_fallback_failure_records_audio_check_without_raising(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    m = _import_recorder_module()
    session_dir = tmp_path / "clip"
    session_dir.mkdir()
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake mp4")

    def _missing_tool(*_args: Any, **_kwargs: Any) -> Any:
        raise FileNotFoundError("missing media tool")

    monkeypatch.setattr(m.subprocess, "run", _missing_tool)

    m._generate_silent_audio_fallback(session_dir, video)

    payload = json.loads((session_dir / "audio_check.json").read_text(encoding="utf-8"))
    assert payload["audio_source"] == "failed"
    assert payload["error"] == "duration probe unavailable"


def test_validate_recorded_video_rejects_static_frame_mp4(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    m = _import_recorder_module()
    _require_ffmpeg_tools(monkeypatch, m)
    video = tmp_path / "static.mp4"
    _make_fixture_video(
        video,
        "color=c=black:s=320x180:r=10:d=6",
        ffmpeg=m._FFMPEG,
    )

    valid, reason = m._validate_recorded_video(video)

    assert valid is False
    assert "identical" in reason or "low entropy" in reason


def test_validate_recorded_video_accepts_varied_mp4(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    m = _import_recorder_module()
    _require_ffmpeg_tools(monkeypatch, m)
    video = tmp_path / "varied.mp4"
    _make_fixture_video(
        video,
        "testsrc2=duration=6:size=320x180:rate=10",
        ffmpeg=m._FFMPEG,
    )

    valid, reason = m._validate_recorded_video(video)

    assert valid is True
    assert "unique frames" in reason


def test_validate_recorded_video_rejects_zero_byte_mp4(tmp_path: Path) -> None:
    m = _import_recorder_module()
    video = tmp_path / "zero.mp4"
    video.write_bytes(b"")

    valid, reason = m._validate_recorded_video(video)

    assert valid is False
    assert reason
