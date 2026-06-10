from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from typing import Any

import pytest

BIN_DIR = Path(__file__).resolve().parents[2] / "bin"


class _OsProxy:
    def __init__(self, name: str) -> None:
        self.name = name

    def __getattr__(self, attr: str) -> Any:
        return getattr(os, attr)


def _install_tk_stubs() -> None:
    if "tkinter" in sys.modules and getattr(sys.modules["tkinter"], "_mc_focus_stub", False):
        return

    tk = types.ModuleType("tkinter")
    tk._mc_focus_stub = True  # type: ignore[attr-defined]

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


def _options_map(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition(":")
        if sep:
            out[key] = value
    return out


@pytest.mark.parametrize(
    "initial",
    [
        "pauseOnLostFocus:true\nfullscreen:true\nrenderDistance:12\n",
        "renderDistance:12\n",
        "pauseOnLostFocus:false\nfullscreen:false\n",
    ],
)
def test_ensure_mc_focus_loss_safe_patches_options_txt(
    tmp_path: Path,
    initial: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    m = _import_recorder_module()
    traces: list[str] = []
    monkeypatch.setattr(m, "_trace", traces.append)
    monkeypatch.setattr(m, "os", _OsProxy("nt"))

    instance = tmp_path / "mc-instance"
    instance.mkdir()
    options = instance / "options.txt"
    options.write_text(initial, encoding="utf-8")

    assert m._ensure_mc_focus_loss_safe(instance) is True
    assert m._ensure_mc_focus_loss_safe(instance) is True

    parsed = _options_map(options)
    assert parsed["pauseOnLostFocus"] == "false"
    assert parsed["fullscreen"] == "false"
    assert options.read_text(encoding="utf-8").count("pauseOnLostFocus:false") == 1
    assert options.read_text(encoding="utf-8").count("fullscreen:false") == 1
    assert any("options.txt patched: pauseOnLostFocus=false" in line for line in traces)


def test_ensure_mc_focus_loss_safe_skips_missing_options_txt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    m = _import_recorder_module()
    traces: list[str] = []
    monkeypatch.setattr(m, "_trace", traces.append)
    monkeypatch.setattr(m, "os", _OsProxy("nt"))

    instance = tmp_path / "mc-instance"
    instance.mkdir()

    assert m._ensure_mc_focus_loss_safe(instance) is False

    assert not (instance / "options.txt").exists()
    assert traces == [f"options.txt not found at {instance / 'options.txt'}, skipping"]


def test_ensure_mc_focus_loss_safe_noops_on_non_windows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    m = _import_recorder_module()
    traces: list[str] = []
    monkeypatch.setattr(m, "_trace", traces.append)
    monkeypatch.setattr(m, "os", _OsProxy("posix"))

    def _fail_write(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("non-Windows path must not write options.txt")

    monkeypatch.setattr(m, "_atomic_write_text", _fail_write)

    instance = tmp_path / "mc-instance"
    instance.mkdir()
    options = instance / "options.txt"
    original = "pauseOnLostFocus:true\nfullscreen:true\nrenderDistance:12\n"
    options.write_text(original, encoding="utf-8")

    assert m._ensure_mc_focus_loss_safe(instance) is False

    assert options.read_text(encoding="utf-8") == original
    assert traces == ["non-Windows, skipping options.txt patch"]


def test_watch_mc_focus_alive_is_disabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    m = _import_recorder_module()
    traces: list[str] = []
    monkeypatch.setattr(m, "_trace", traces.append)
    monkeypatch.setattr(m.os, "name", "nt")

    calls: list[tuple[str, int]] = []

    class _FakeUser32:
        def GetForegroundWindow(self) -> int:
            return 222

        def ShowWindow(self, hwnd: int, _cmd: int) -> None:
            calls.append(("ShowWindow", hwnd))

        def BringWindowToTop(self, hwnd: int) -> None:
            calls.append(("BringWindowToTop", hwnd))

        def SetForegroundWindow(self, hwnd: int) -> None:
            calls.append(("SetForegroundWindow", hwnd))

    fake_ctypes = types.ModuleType("ctypes")
    fake_ctypes.windll = types.SimpleNamespace(user32=_FakeUser32())  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)

    now = [100.0]
    monkeypatch.setattr(m.time, "time", lambda: now[0])

    app = object.__new__(m.RecorderApp)
    app._mc_window_rect = {"hwnd": 111}
    app._last_mc_focus_check_at = 0.0
    app._mc_focus_restore_loop_enabled = False
    app._mc_focus_restore_ran = False

    app._watch_mc_focus_alive()
    now[0] = 102.0
    app._watch_mc_focus_alive()

    assert calls == []
    assert traces == []
    assert app._mc_focus_restore_ran is False


def test_focus_restore_warning_loop_removed_from_source() -> None:
    src = (BIN_DIR / "recorder_consumer_lite.py").read_text(encoding="utf-8")

    assert "WARN: MC mod data frozen" not in src
    assert "possible focus loss" not in src
    watch_start = src.index("def _watch_mc_focus_alive")
    watch_end = src.index("def _make_window_non_focus_stealing", watch_start)
    watch_body = src[watch_start:watch_end]
    assert "SetForegroundWindow" not in watch_body
    assert "BringWindowToTop" not in watch_body
