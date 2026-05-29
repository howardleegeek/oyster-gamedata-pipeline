from __future__ import annotations

import json
import sys
import tarfile
import types
from pathlib import Path
from typing import Any

import pytest

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


def _mp4_box(name: bytes, payload: bytes = b"") -> bytes:
    return (8 + len(payload)).to_bytes(4, "big") + name + payload


class _FakeProc:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode or 0


class _FakeObsClient:
    def __init__(self, output_file: Path) -> None:
        self.output_file = output_file
        self.connected = False
        self.closed = False
        self.requests: list[tuple[str, dict[str, Any] | None]] = []

    def connect(self) -> None:
        self.connected = True

    def request(
        self,
        request_type: str,
        request_data: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        self.requests.append((request_type, request_data))
        if request_type == "GetProfileParameter":
            if request_data and request_data.get("parameterName") == "RecEncoder":
                return {"responseData": {"parameterValue": "nvenc"}}
            return {"responseData": {"parameterValue": ""}}
        if request_type == "StopRecord":
            self.output_file.write_bytes(_mp4_box(b"ftyp", b"isom0000") + _mp4_box(b"moov"))
            return {"responseData": {"outputPath": str(self.output_file)}}
        return {}

    def close(self) -> None:
        self.closed = True


def _install_packaging_fakes(monkeypatch: pytest.MonkeyPatch, m: Any, out_dir: Path) -> None:
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
    fake_gso = types.ModuleType("game_state_overlay")
    fake_gso.load = lambda _path=None: [{"timestamp_ms": 0}]  # type: ignore[attr-defined]
    fake_gso.lookup_at_ms = lambda samples, _ms: samples[0]  # type: ignore[attr-defined]
    fake_gso.apply_to_record = lambda rec, _sample: rec  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "generate_systeminfo_json", fake_gsi)
    monkeypatch.setitem(sys.modules, "generate_gameinfo_xlsx", fake_ggx)
    monkeypatch.setitem(sys.modules, "game_state_overlay", fake_gso)
    monkeypatch.setattr(m, "_output_dir", lambda: out_dir)
    monkeypatch.setattr(m, "_client_depth_inference_enabled", lambda: False)


def test_recorder_version_is_v0190() -> None:
    m = _import_recorder_module()

    assert m.RECORDER_VERSION == "lite-v0.19.0"


def test_obs_launch_arg_builder_uses_portable_minimized_profile(tmp_path: Path) -> None:
    m = _import_recorder_module()
    obs_exe = tmp_path / "obs-studio" / "bin" / "64bit" / "obs64.exe"

    spec = m._build_obs_launch_spec(obs_exe)

    assert spec.cwd == obs_exe.parent
    assert spec.args == (
        str(obs_exe),
        "--portable",
        "--minimize-to-tray",
        "--disable-shutdown-check",
        "--collection",
        "oyster",
        "--profile",
        "oyster",
        "--scene",
        "MC",
    )


def test_obs_start_stop_control_uses_websocket_and_moves_mp4(tmp_path: Path) -> None:
    m = _import_recorder_module()
    obs_exe = tmp_path / "obs-studio" / "bin" / "64bit" / "obs64.exe"
    obs_exe.parent.mkdir(parents=True)
    obs_exe.write_text("fake", encoding="utf-8")
    obs_output = tmp_path / "obs-out" / "obs-file.mp4"
    obs_output.parent.mkdir()
    client = _FakeObsClient(obs_output)
    proc = _FakeProc()
    launched: list[tuple[list[str], str]] = []

    def _popen(args: list[str], *, cwd: str, **_kwargs: Any) -> _FakeProc:
        launched.append((args, cwd))
        return proc

    out_path = tmp_path / "session" / "video.mp4"
    handle = m._start_obs_capture_layer(
        out_path,
        output_profile=m.VideoOutputProfile(width=1920, height=1080, fps=30.0),
        obs_exe=obs_exe,
        ws_client_factory=lambda: client,
        popen_factory=_popen,
    )

    assert launched[0][0][0] == str(obs_exe)
    assert launched[0][1] == str(obs_exe.parent)
    assert ("SetRecordDirectory", {"recordDirectory": str(out_path.parent)}) in client.requests
    assert ("StartRecord", None) in client.requests
    assert handle.video_encoder == "nvenc"

    m._stop_obs_capture_handle(handle)

    assert ("StopRecord", None) in client.requests
    assert out_path.is_file()
    assert not obs_output.exists()
    assert client.closed
    assert proc.terminated


def test_obs_missing_falls_back_to_existing_ffmpeg_layer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    m = _import_recorder_module()
    app = object.__new__(m.RecorderApp)
    app._mc_window_rect = {
        "title": "Minecraft 1.21.4",
        "x": 10,
        "y": 20,
        "width": 1280,
        "height": 720,
    }
    audio_report = m.AudioProbeReport(process_name="javaw.exe", selected=None, probes=[])

    def _fake_ffmpeg_layer(layer: str, out_path: Path, **kwargs: Any) -> Any:
        return m.VideoCaptureHandle(
            layer=layer,
            out_path=out_path,
            stdin_kind="control",
            proc=_FakeProc(),
            video_encoder="libx264",
            output_profile=kwargs.get("output_profile"),
        )

    monkeypatch.setattr(m, "_CAPTURE_MODE", "auto")
    monkeypatch.setattr(m, "_VIDEO_AUTO_LAYERS", ("obs", "ddagrab"))
    monkeypatch.setattr(m, "_find_bundled_obs_exe", lambda: None)
    monkeypatch.setattr(m, "probe_audio_source_chain", lambda _process: audio_report)
    monkeypatch.setattr(m, "_start_ffmpeg_capture_layer", _fake_ffmpeg_layer)

    app._start_video_capture(tmp_path / "video.mp4")

    assert app._video_capture_mode == "ddagrab"
    assert app._video_encoder == "libx264"
    assert app._video_capture_attempt_log[0]["layer"] == "obs"
    assert app._video_capture_attempt_log[0]["status"] == "failed"
    assert app._video_capture_attempt_log[-1]["status"] == "selected"


def test_obs_unreachable_falls_back_to_existing_ffmpeg_layer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    m = _import_recorder_module()
    app = object.__new__(m.RecorderApp)
    app._mc_window_rect = {
        "title": "Minecraft 1.21.4",
        "x": 10,
        "y": 20,
        "width": 1280,
        "height": 720,
    }
    audio_report = m.AudioProbeReport(process_name="javaw.exe", selected=None, probes=[])

    def _raise_unreachable(*_args: Any, **_kwargs: Any) -> None:
        raise m.ObsWebSocketError("OBS websocket unreachable")

    def _fake_ffmpeg_layer(layer: str, out_path: Path, **kwargs: Any) -> Any:
        return m.VideoCaptureHandle(
            layer=layer,
            out_path=out_path,
            stdin_kind="control",
            proc=_FakeProc(),
            video_encoder="libx264",
            output_profile=kwargs.get("output_profile"),
        )

    monkeypatch.setattr(m, "_CAPTURE_MODE", "auto")
    monkeypatch.setattr(m, "_VIDEO_AUTO_LAYERS", ("obs", "ddagrab"))
    monkeypatch.setattr(m, "probe_audio_source_chain", lambda _process: audio_report)
    monkeypatch.setattr(m, "_start_obs_capture_layer", _raise_unreachable)
    monkeypatch.setattr(m, "_start_ffmpeg_capture_layer", _fake_ffmpeg_layer)

    app._start_video_capture(tmp_path / "video.mp4")

    assert app._video_capture_mode == "ddagrab"
    assert app._video_capture_attempt_log[0]["layer"] == "obs"
    assert app._video_capture_attempt_log[0]["status"] == "failed"
    assert "unreachable" in app._video_capture_attempt_log[0]["error"]
    assert app._video_capture_attempt_log[-1]["layer"] == "ddagrab"
    assert app._video_capture_attempt_log[-1]["status"] == "selected"


def test_obs_backend_metadata_records_backend_and_chosen_encoder(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    m = _import_recorder_module()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    _install_packaging_fakes(monkeypatch, m, out_dir)

    active_dir = out_dir / "active_session"
    active_dir.mkdir()
    game_state = active_dir / "game_state.jsonl"
    game_state.write_text('{"timestamp_ms":0,"x":1}\n', encoding="utf-8")
    sys.modules["game_state_overlay"].jsonl_path = lambda: str(game_state)  # type: ignore[attr-defined]

    app = object.__new__(m.RecorderApp)
    app._tmp_dir = tmp_path / "work"
    app._tmp_dir.mkdir()
    app._video_path = app._tmp_dir / "video.mp4"
    app._video_path.write_bytes(_mp4_box(b"ftyp", b"isom0000") + _mp4_box(b"moov"))
    app._record_started_at = m.time.time() - 6.0
    app._mc_window_rect = {
        "title": "Minecraft 1.21.4 - Singleplayer",
        "x": 10,
        "y": 20,
        "width": 1280,
        "height": 720,
        "recordDpi": 96,
    }
    app._captured_events = []
    app._input_capture_diagnostics = {
        "registration_tier": "rawinput",
        "wm_input_total": 0,
        "get_raw_input_data_failures": 0,
    }
    app._session_id = "obs-metadata-unit"
    app._active_session_dir = active_dir
    app._audio_probe_failed = False
    app._video_capture_attempt_log = [{"layer": "obs", "status": "selected"}]
    app._video_capture_mode = "obs"
    app._video_capture_requested_mode = "auto"
    app._video_encoder = "nvenc"
    app._video_output_profile = m.VideoOutputProfile(width=1920, height=1080, fps=30.0)
    app._video_validation_passed = True
    app._video_validation_reason = "unit"
    app._video_frozen = False
    app._video_frozen_reason = None
    app._video_frames_written = 180
    app._video_expected_frames = 180
    app._video_frames_under_expected = False
    app._video_load_reduction_recommended = False
    app._allow_placeholder = False

    tar_path = app._package_tarball("20260529-000000")
    extract_dir = tmp_path / "extract"
    with tarfile.open(tar_path, "r:gz") as tf:
        tf.extractall(extract_dir)

    metadata = json.loads(
        (extract_dir / "clip-20260529-000000" / "metadata.json").read_text(encoding="utf-8")
    )
    assert metadata["video_capture"]["backend"] == "obs"
    assert metadata["video_capture"]["selected_layer"] == "obs"
    assert metadata["video_capture"]["chosen_encoder"] == "nvenc"
    assert metadata["video_capture"]["video_encoder"] == "nvenc"
