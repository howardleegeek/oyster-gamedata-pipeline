from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

BIN_DIR = Path(__file__).resolve().parents[1] / "bin"


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


def test_recorder_foregrounds_minecraft_hwnd_not_its_own_ui(monkeypatch) -> None:
    m = _import_recorder_module()
    minecraft_hwnd = 1001
    recorder_ui_hwnd = 2002
    calls: list[tuple[str, int]] = []

    class _User32:
        def ShowWindow(self, hwnd: int, _cmd: int) -> bool:  # noqa: N802
            calls.append(("ShowWindow", hwnd))
            return True

        def BringWindowToTop(self, hwnd: int) -> bool:  # noqa: N802
            calls.append(("BringWindowToTop", hwnd))
            return True

        def SetForegroundWindow(self, hwnd: int) -> bool:  # noqa: N802
            calls.append(("SetForegroundWindow", hwnd))
            return True

    fake_ctypes = types.SimpleNamespace(windll=types.SimpleNamespace(user32=_User32()))
    fake_ui_window = types.SimpleNamespace(hwnd=recorder_ui_hwnd)

    monkeypatch.setattr(m.os, "name", "nt")
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)

    m._restore_minecraft_window_for_capture({"hwnd": minecraft_hwnd, "ui": fake_ui_window})

    assert ("SetForegroundWindow", minecraft_hwnd) in calls
    assert ("SetForegroundWindow", recorder_ui_hwnd) not in calls


def test_post_ffmpeg_path_does_not_focus_recorder_ui() -> None:
    src = (BIN_DIR / "recorder_consumer_lite.py").read_text(encoding="utf-8")
    start = src.index("self._start_ffmpeg(self._video_path)")
    end = src.index("# Phase 3:", start)
    post_ffmpeg_start = src[start:end]

    assert "SetForegroundWindow" not in post_ffmpeg_start
    assert ".focus_force(" not in post_ffmpeg_start
    assert ".lift(" not in post_ffmpeg_start
    assert ".deiconify(" not in post_ffmpeg_start


def test_input_capture_does_not_register_suppressing_keyboard_hook(monkeypatch) -> None:
    m = _import_recorder_module()
    keyboard_calls: list[dict[str, Any]] = []
    mouse_calls: list[dict[str, Any]] = []

    class _KeyboardListener:
        def __init__(self, *_args: Any, **kwargs: Any) -> None:
            keyboard_calls.append(kwargs)

        def start(self) -> None:
            pass

    class _MouseListener:
        def __init__(self, *_args: Any, **kwargs: Any) -> None:
            mouse_calls.append(kwargs)

        def start(self) -> None:
            pass

    keyboard = types.SimpleNamespace(Listener=_KeyboardListener)
    mouse = types.SimpleNamespace(Listener=_MouseListener)
    pynput = types.SimpleNamespace(keyboard=keyboard, mouse=mouse)
    monkeypatch.setitem(sys.modules, "pynput", pynput)
    monkeypatch.setitem(sys.modules, "pynput.keyboard", keyboard)
    monkeypatch.setitem(sys.modules, "pynput.mouse", mouse)

    capture = m.InputCapture()

    assert capture.start() is True
    assert keyboard_calls
    assert all(call.get("suppress") in (None, False) for call in keyboard_calls)
    assert all(call.get("suppress") in (None, False) for call in mouse_calls)
