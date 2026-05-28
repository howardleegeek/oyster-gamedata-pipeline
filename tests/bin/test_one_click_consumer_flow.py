from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

BIN_DIR = Path(__file__).resolve().parents[2] / "bin"


def _install_tk_stubs() -> None:
    if "tkinter" in sys.modules and getattr(sys.modules["tkinter"], "_one_click_stub", False):
        return

    tk = types.ModuleType("tkinter")
    tk._one_click_stub = True  # type: ignore[attr-defined]

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


def _import_oyster_play() -> Any:
    if str(BIN_DIR) not in sys.path:
        sys.path.insert(0, str(BIN_DIR))
    sys.modules.pop("oyster_play", None)
    import oyster_play as m  # type: ignore[import-not-found]

    return m


def test_recorder_rejects_launcher_process_and_window_titles(monkeypatch) -> None:
    m = _import_recorder_module()

    assert "MinecraftLauncher.exe" not in m.MC_PROCESS_NAMES
    assert m._is_supported_minecraft_process_name("javaw.exe") is True
    assert m._is_supported_minecraft_process_name("java.exe") is True
    assert m._is_supported_minecraft_process_name("Minecraft.exe") is True
    assert m._is_supported_minecraft_process_name("MinecraftLauncher.exe") is False
    monkeypatch.setattr(m, "_list_windows_processes", lambda: {"MinecraftLauncher.exe"})
    assert m._minecraft_running() is False
    monkeypatch.setattr(m, "_list_windows_processes", lambda: {"javaw.exe"})
    assert m._minecraft_running() is True

    assert m._is_real_minecraft_window_title("Minecraft 1.21.4") is True
    assert m._is_real_minecraft_window_title("Minecraft 1.21.4 - Singleplayer") is True
    assert m._is_real_minecraft_window_title("Minecraft Launcher") is False
    assert m._is_real_minecraft_window_title("Minecraft 启动器") is False
    assert m._is_real_minecraft_window_title("Microsoft Store") is False


def test_recorder_requires_game_sized_window_before_recording() -> None:
    m = _import_recorder_module()

    assert m._is_real_minecraft_window_geometry(1280, 720) is True
    assert m._is_real_minecraft_window_geometry(640, 360) is True
    assert m._is_real_minecraft_window_geometry(320, 240) is False


def test_recorder_does_not_iconify_after_ffmpeg_start() -> None:
    src = (BIN_DIR / "recorder_consumer_lite.py").read_text(encoding="utf-8")

    assert "window " "iconified to taskbar" not in src
    assert "self.after(0, self." "iconify)" not in src
    assert "post-ffmpeg iconify skipped" in src


def test_recorder_depth_mode_defaults_to_server_postprocess(monkeypatch) -> None:
    monkeypatch.delenv("OYSTER_DEPTH_MODE", raising=False)
    monkeypatch.delenv("OYSTER_ALLOW_CLIENT_DEPTH", raising=False)
    m = _import_recorder_module()

    assert m._depth_mode() == "server"
    assert m._client_depth_inference_enabled() is False

    monkeypatch.setenv("OYSTER_DEPTH_MODE", "local")
    assert m._client_depth_inference_enabled() is True

    monkeypatch.setenv("OYSTER_DEPTH_MODE", "server")
    monkeypatch.setenv("OYSTER_ALLOW_CLIENT_DEPTH", "1")
    assert m._client_depth_inference_enabled() is True


def test_recorder_package_exposes_recording_mp4_alias(tmp_path: Path) -> None:
    m = _import_recorder_module()
    clip_dir = tmp_path / "clip"
    clip_dir.mkdir()
    video = clip_dir / "video.mp4"
    video.write_bytes(b"fake-video")

    alias = m._ensure_recording_mp4_alias(clip_dir)

    assert alias == clip_dir / "recording.mp4"
    assert video.is_file()
    assert alias.exists()
    assert alias.read_bytes() == b"fake-video"


def test_oysterplay_auto_arms_recorder_even_when_log_ready_marker_is_missing(
    monkeypatch, tmp_path: Path
) -> None:
    m = _import_oyster_play()
    fake_win = m.RecorderWindow(hwnd=100, title="Oyster 录制器", class_name="TkTopLevel", pid=42)
    clicked: list[str] = []

    class _FakeJava:
        pid = 1234

        def wait(self) -> int:
            return 0

    monkeypatch.setattr(
        m.launcher,
        "verify_install",
        lambda *_args, **_kwargs: SimpleNamespace(ok=True, missing=[]),
    )
    monkeypatch.setattr(
        m.launcher,
        "build_launch_plan",
        lambda **_kwargs: SimpleNamespace(cmd=["javaw"], main_class="KnotClient"),
    )
    monkeypatch.setattr(m, "find_recorder_exe", lambda _root: tmp_path / "recorder.exe")
    monkeypatch.setattr(m, "spawn_recorder", lambda _recorder: None)
    monkeypatch.setattr(m.launcher, "launch_javaw", lambda *_args, **_kwargs: _FakeJava())
    monkeypatch.setattr(m.launcher, "latest_log_path", lambda _root: tmp_path / "latest.log")
    monkeypatch.setattr(m.launcher, "wait_for_mc_ready", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(m, "wait_for_recorder_window", lambda **_kwargs: fake_win)

    def _click(_win: Any, *, button_label: str = m.RECORDER_BUTTON_LABEL) -> bool:
        clicked.append(button_label)
        return True

    monkeypatch.setattr(m, "click_recorder_button", _click)

    sess = m.run_session(install_root_path=tmp_path)

    assert sess.failure_reason == ""
    assert sess.armed is True
    assert clicked[0] == m.RECORDER_BUTTON_LABEL
    assert clicked[-1] == m.RECORDER_DISARM_LABEL
