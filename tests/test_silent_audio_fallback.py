from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tarfile
import types
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = REPO_ROOT / "bin"


def _install_tk_stubs() -> None:
    if "tkinter" in sys.modules and getattr(sys.modules["tkinter"], "_silent_audio_stub", False):
        return

    tk = types.ModuleType("tkinter")
    tk._silent_audio_stub = True  # type: ignore[attr-defined]

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


def _make_tiny_video(ffmpeg: str, video_path: Path) -> None:
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=16x16:r=10",
            "-t",
            "0.4",
            "-c:v",
            "mpeg4",
            str(video_path),
        ],
        check=True,
        capture_output=True,
    )


def test_package_writes_silent_flac_when_all_audio_probes_fail(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("ffmpeg/ffprobe not available")

    m = _import_recorder_module()
    monkeypatch.setattr(m, "_FFMPEG", Path(ffmpeg))
    monkeypatch.setattr(m, "_FFPROBE", Path(ffprobe))

    fake_gsi = types.ModuleType("generate_systeminfo_json")
    fake_gsi.build_systeminfo = lambda **kwargs: {  # type: ignore[attr-defined]
        "gameProcessName": kwargs["game_process_name"],
        "x": kwargs["x"],
        "y": kwargs["y"],
        "width": kwargs["width"],
        "height": kwargs["height"],
        "recordDpi": kwargs["record_dpi"],
    }
    fake_ggx = types.ModuleType("generate_gameinfo_xlsx")
    fake_ggx.parse_game_version_from_window_title = lambda _title: "1.21.4"  # type: ignore[attr-defined]
    fake_ggx.build_gameinfo_dict = lambda **kwargs: kwargs  # type: ignore[attr-defined]
    fake_ggx.write_xlsx = (  # type: ignore[attr-defined]
        lambda _game_info, path: Path(path).write_bytes(b"fake xlsx")
    )
    monkeypatch.setitem(sys.modules, "generate_systeminfo_json", fake_gsi)
    monkeypatch.setitem(sys.modules, "generate_gameinfo_xlsx", fake_ggx)
    monkeypatch.setattr(m, "_output_dir", lambda: tmp_path / "out")
    monkeypatch.setattr(m, "_client_depth_inference_enabled", lambda: False)
    monkeypatch.setattr(m, "_depth_mode", lambda: "server")
    (tmp_path / "out").mkdir()

    failed_probes = [
        m.AudioSourceProbe(
            mode=m.AudioCaptureMode.APPLICATION,
            label="Application Audio Capture",
            available=False,
            reason="missing",
        ),
        m.AudioSourceProbe(
            mode=m.AudioCaptureMode.DESKTOP,
            label="Desktop Audio Output",
            available=False,
            reason="missing",
        ),
        m.AudioSourceProbe(
            mode=m.AudioCaptureMode.INPUT,
            label="Any audio input device",
            available=False,
            reason="missing",
        ),
    ]
    monkeypatch.setattr(
        m,
        "probe_audio_source_chain",
        lambda _process_name: m.AudioProbeReport(
            process_name="javaw.exe",
            selected=None,
            probes=failed_probes,
        ),
    )

    class _DummyPopen:
        stdin = None

    work_dir = tmp_path / "work"
    work_dir.mkdir()
    video_path = work_dir / "capture.mp4"
    _make_tiny_video(ffmpeg, video_path)

    app = object.__new__(m.RecorderApp)
    app._mc_window_rect = {
        "title": "Minecraft 1.21.4 - Singleplayer",
        "x": 0,
        "y": 0,
        "width": 1280,
        "height": 720,
        "recordDpi": 96,
    }
    with mock.patch.object(m.subprocess, "Popen", lambda *_args, **_kwargs: _DummyPopen()):
        app._start_ffmpeg(work_dir / "unused.mp4")
    assert app._audio_probe_failed is True

    app._tmp_dir = work_dir
    app._video_path = video_path
    app._record_started_at = m.time.time() - 0.25
    app._captured_events = []
    app._session_id = "silent-audio-unit"
    app._allow_placeholder = True

    tar_path = app._package_tarball("20260527-010203")

    extract_dir = tmp_path / "extract"
    with tarfile.open(tar_path, "r:gz") as tf:
        tf.extractall(extract_dir)

    session_dir = extract_dir / "clip-20260527-010203"
    audio_path = session_dir / "audio.flac"
    check = json.loads((session_dir / "audio_check.json").read_text(encoding="utf-8"))

    assert audio_path.is_file()
    assert audio_path.stat().st_size > 0
    assert check["audio_source"] == "silent_fallback"
    assert check["audio_file"] == "audio.flac"
    assert check["is_silent"] is True
    assert check["continuous"] is True
    assert check["duration_sec"] > 0
    assert check["size_bytes"] == audio_path.stat().st_size
