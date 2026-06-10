from __future__ import annotations

import json
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

    def _gpu_probe_must_not_run() -> bool:
        raise AssertionError("default server mode must not probe GPU")

    monkeypatch.setattr(m, "_detect_gpu_available", _gpu_probe_must_not_run)
    assert m._client_depth_default_skip() is False

    monkeypatch.setenv("OYSTER_DEPTH_MODE", "client")
    assert m._client_depth_inference_enabled() is True

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


def test_oysterplay_prefers_bundled_rust_recorder(tmp_path: Path) -> None:
    m = _import_oyster_play()
    rust = tmp_path / "gamedata-recorder" / "gamedata-recorder.exe"
    python_fallback = tmp_path / "OysterRecorder-onedir" / "OysterRecorder-onedir.exe"
    rust.parent.mkdir()
    python_fallback.parent.mkdir()
    rust.write_text("rust", encoding="utf-8")
    python_fallback.write_text("python", encoding="utf-8")

    assert m.find_recorder_exe(tmp_path) == rust


def test_oysterplay_spawns_rust_recorder_with_shippable_config(monkeypatch, tmp_path: Path) -> None:
    m = _import_oyster_play()
    rust = tmp_path / "install" / "gamedata-recorder" / "gamedata-recorder.exe"
    rust.parent.mkdir(parents=True)
    rust.write_text("rust", encoding="utf-8")
    recordings_dir = tmp_path / "User" / "AppData" / "Local" / "GameData Recorder" / "recordings"
    config_path = tmp_path / "User" / "AppData" / "Roaming" / "GameData Recorder" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_bytes(
        b"\xef\xbb\xbf" + json.dumps({"preferences": {"honk": True}}).encode("utf-8")
    )
    popen_calls: list[dict[str, Any]] = []

    class _FakePopen:
        def __init__(self, args: list[str], **kwargs: Any) -> None:
            popen_calls.append({"args": args, **kwargs})

    monkeypatch.setattr(m.os, "name", "nt", raising=False)
    monkeypatch.setattr(m, "is_recorder_running", lambda: False)
    monkeypatch.setattr(m, "rust_recorder_config_path", lambda: config_path)
    monkeypatch.setattr(m, "rust_recorder_recordings_dir", lambda: recordings_dir)
    monkeypatch.setattr(m.subprocess, "Popen", _FakePopen)
    monkeypatch.setenv("GAMEDATA_CI_MODE", "1")
    monkeypatch.setenv("GAMEDATA_SKIP_API_KEY", "1")
    monkeypatch.setenv("GAMEDATA_OUTPUT_DIR", str(tmp_path / "ci-output"))

    proc = m.spawn_recorder(rust)

    assert isinstance(proc, _FakePopen)
    assert popen_calls[0]["args"] == [str(rust)]
    assert popen_calls[0]["cwd"] == str(rust.parent)
    env = popen_calls[0]["env"]
    assert "GAMEDATA_CI_MODE" not in env
    assert "GAMEDATA_SKIP_API_KEY" not in env
    assert "GAMEDATA_OUTPUT_DIR" not in env
    assert env["OYSTER_CAPTURE_MODE"] == "game"

    raw_config = config_path.read_bytes()
    assert not raw_config.startswith(b"\xef\xbb\xbf")
    config = json.loads(raw_config.decode("utf-8"))
    assert config["credentials"]["hasConsented"] is True
    assert config["credentials"]["consentGivenAtVersion"] == "2.6.0"
    assert config["preferences"]["autoUploadOnCompletion"] is False
    assert config["preferences"]["recordMicrophone"] is False
    assert config["preferences"]["recordingLocation"] == str(recordings_dir)
    assert config["preferences"]["honk"] is True
    assert config["preferences"]["games"]["javaw"] == {
        "capture_mode": "game_hook",
        "use_window_capture": False,
    }
    assert config["preferences"]["games"]["minecraft"] == {
        "capture_mode": "game_hook",
        "use_window_capture": False,
    }


def test_oysterplay_rust_recorder_skips_legacy_uia_clicks(monkeypatch, tmp_path: Path) -> None:
    m = _import_oyster_play()
    rust = tmp_path / "gamedata-recorder" / "gamedata-recorder.exe"
    rust.parent.mkdir()
    rust.write_text("rust", encoding="utf-8")

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
    monkeypatch.setattr(m, "find_recorder_exe", lambda _root: rust)
    monkeypatch.setattr(m, "spawn_recorder", lambda _recorder: None)
    monkeypatch.setattr(m.launcher, "launch_javaw", lambda *_args, **_kwargs: _FakeJava())
    monkeypatch.setattr(m.launcher, "latest_log_path", lambda _root: tmp_path / "latest.log")
    monkeypatch.setattr(m.launcher, "wait_for_mc_ready", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        m,
        "wait_for_recorder_window",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("UIA wait not expected")),
    )
    monkeypatch.setattr(
        m,
        "click_recorder_button",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("UIA click not expected")),
    )

    sess = m.run_session(install_root_path=tmp_path)

    assert sess.failure_reason == ""
    assert sess.armed is True
    assert sess.recorder_window is None
