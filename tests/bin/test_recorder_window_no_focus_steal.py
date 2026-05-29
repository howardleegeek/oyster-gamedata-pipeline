from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

BIN_DIR = Path(__file__).resolve().parents[2] / "bin"


def _install_tk_stubs() -> None:
    if "tkinter" in sys.modules and getattr(sys.modules["tkinter"], "_focus_stub", False):
        return

    tk = types.ModuleType("tkinter")
    tk._focus_stub = True  # type: ignore[attr-defined]

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


class _FakeUser32:
    def __init__(self, *, ex_style: int = 0x00040000, fail_get: bool = False) -> None:
        self.ex_style = ex_style
        self.fail_get = fail_get
        self.get_calls: list[tuple[int, int]] = []
        self.set_calls: list[tuple[int, int, int]] = []

    def GetWindowLongW(self, hwnd: int, index: int) -> int:
        self.get_calls.append((hwnd, index))
        if self.fail_get:
            raise OSError("GetWindowLongW failed")
        return self.ex_style

    def SetWindowLongW(self, hwnd: int, index: int, value: int) -> int:
        self.set_calls.append((hwnd, index, value))
        return self.ex_style


def _install_fake_ctypes(monkeypatch: pytest.MonkeyPatch, user32: _FakeUser32) -> None:
    fake_ctypes = types.ModuleType("ctypes")
    fake_ctypes.windll = types.SimpleNamespace(user32=user32)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)


def _make_app(m: Any, *, frame: Any = "0x1234") -> Any:
    app = object.__new__(m.RecorderApp)
    app._window_no_activate_hwnd = None
    app._window_original_ex_style = None
    app._window_no_activate_applied = False
    app._window_disabled_for_recording = False
    app._disabled_stop_hotkey_thread = None
    app._disabled_stop_hotkey_thread_id = None
    app._disabled_stop_hotkey_id = 0x534F
    app.winfo_id = lambda: 0x2222
    app.wm_frame = lambda: frame
    app.attributes = lambda *_args, **_kwargs: None
    return app


def test_ws_ex_noactivate_applied_during_recording_and_restored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    m = _import_recorder_module()
    monkeypatch.setattr(m.os, "name", "nt")
    user32 = _FakeUser32(ex_style=0x00040000)
    _install_fake_ctypes(monkeypatch, user32)
    app = _make_app(m)

    app._make_window_non_focus_stealing()

    assert m.WS_EX_NOACTIVATE == 0x08000000
    assert m.GWL_EXSTYLE == -20
    assert user32.get_calls == [(0x1234, -20)]
    assert user32.set_calls == [(0x1234, -20, 0x08040000)]
    assert app._window_no_activate_hwnd == 0x1234
    assert app._window_original_ex_style == 0x00040000
    assert app._window_no_activate_applied is True
    assert app._window_disabled_for_recording is False

    app._restore_window_activatable()

    assert user32.set_calls[-1] == (0x1234, -20, 0x00040000)
    assert app._window_no_activate_hwnd is None
    assert app._window_original_ex_style is None


def test_ws_ex_noactivate_failure_disables_window_and_restore_reenables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    m = _import_recorder_module()
    monkeypatch.setattr(m.os, "name", "nt")
    user32 = _FakeUser32(fail_get=True)
    _install_fake_ctypes(monkeypatch, user32)
    app = _make_app(m)
    attribute_calls: list[tuple[str, bool]] = []
    hotkey_started: list[bool] = []
    hotkey_stopped: list[bool] = []
    app.attributes = lambda name, value: attribute_calls.append((name, value))
    app._start_disabled_stop_hotkey = lambda: hotkey_started.append(True)
    app._stop_disabled_stop_hotkey = lambda: hotkey_stopped.append(True)

    app._make_window_non_focus_stealing()

    assert attribute_calls == [("-disabled", True)]
    assert hotkey_started == [True]
    assert app._window_no_activate_applied is False
    assert app._window_disabled_for_recording is True

    app._restore_window_activatable()

    assert attribute_calls[-1] == ("-disabled", False)
    assert hotkey_stopped == [True]
    assert app._window_disabled_for_recording is False


def test_recording_path_wraps_ffmpeg_with_focus_mitigation() -> None:
    src = (BIN_DIR / "recorder_consumer_lite.py").read_text(encoding="utf-8")

    assert 'self.attributes("-toolwindow", True)' in src
    assert (
        "self._make_window_non_focus_stealing()\n"
        "        try:\n"
        "            self._start_ffmpeg(self._video_path)"
    ) in src
    assert (
        "except Exception as exc:  # noqa: BLE001\n"
        '            _trace(f"video capture failed at all 4 layers: {exc}")\n'
        "            self._video_started = False"
    ) in src
    assert ('self._set("⚠️ 仅数据采集（视频不可用）", ORANGE, str(exc)[:80])') in src


def test_status_window_never_uses_topmost_or_lift_for_status() -> None:
    src = (BIN_DIR / "recorder_consumer_lite.py").read_text(encoding="utf-8")

    assert 'attributes("-topmost"' not in src
    assert ".lift(" not in src
    assert ".focus_force(" not in src
    assert ".grab_set(" not in src
