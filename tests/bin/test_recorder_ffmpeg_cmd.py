from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any
from unittest import mock

BIN_DIR = Path(__file__).resolve().parents[2] / "bin"


def _install_tk_stubs() -> None:
    tk = types.ModuleType("tkinter")

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

    ttk = types.ModuleType("tkinter.ttk")
    ttk.Progressbar = _Widget

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


def test_recorder_ffmpeg_cmd_pins_h265_bitrate(tmp_path: Path) -> None:
    m = _import_recorder_module()
    app = object.__new__(m.RecorderApp)
    app._mc_window_rect = {
        "title": "Minecraft 1.21.4",
        "x": 10,
        "y": 20,
        "width": 1280,
        "height": 720,
    }

    audio_report = m.AudioProbeReport(
        process_name="javaw.exe",
        selected=None,
        probes=[
            m.AudioSourceProbe(
                mode=m.AudioCaptureMode.NONE,
                label="test",
                available=False,
            )
        ],
    )

    with (
        mock.patch.object(m, "_FFMPEG", tmp_path / "ffmpeg.exe"),
        mock.patch.object(m, "probe_audio_source_chain", return_value=audio_report),
        mock.patch.object(m.subprocess, "Popen") as popen,
    ):
        app._start_ffmpeg(tmp_path / "video.mp4")

    cmd = popen.call_args.args[0]
    assert cmd[cmd.index("-c:v") + 1] == "libx265"
    assert cmd[cmd.index("-b:v") + 1] == "10M"
    assert cmd[cmd.index("-maxrate") + 1] == "12M"
    assert cmd[cmd.index("-bufsize") + 1] == "20M"
    assert "-t" not in cmd


def test_recorder_source_has_no_six_min_recording_cap() -> None:
    src = (BIN_DIR / "recorder_consumer_lite.py").read_text(encoding="utf-8")

    assert "MAX_RECORD_SECONDS" not in src
    assert '"-t",' not in src
