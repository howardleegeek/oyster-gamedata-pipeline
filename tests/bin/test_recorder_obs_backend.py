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
    def __init__(
        self,
        output_file: Path,
        *,
        scenes: list[str] | None = None,
        inputs: dict[str, str] | None = None,
        scene_items: list[dict[str, Any]] | None = None,
    ) -> None:
        self.output_file = output_file
        self.connected = False
        self.closed = False
        self.requests: list[tuple[str, dict[str, Any] | None]] = []
        self.scenes = set(scenes or [])
        self.inputs = dict(inputs or {})
        self.input_settings: dict[str, dict[str, Any]] = {}
        self.scene_items: list[dict[str, Any]] = [dict(item) for item in scene_items or []]
        self._next_scene_item_id = 1 + max(
            [int(item.get("sceneItemId", 0)) for item in self.scene_items] or [0]
        )

    def connect(self) -> None:
        self.connected = True

    def request(
        self,
        request_type: str,
        request_data: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        self.requests.append((request_type, request_data))
        if request_type == "GetSceneList":
            return {
                "responseData": {
                    "scenes": [{"sceneName": scene_name} for scene_name in sorted(self.scenes)]
                }
            }
        if request_type == "CreateScene":
            assert request_data is not None
            self.scenes.add(str(request_data["sceneName"]))
            return {}
        if request_type == "GetInputList":
            return {
                "responseData": {
                    "inputs": [
                        {"inputName": input_name, "inputKind": input_kind}
                        for input_name, input_kind in sorted(self.inputs.items())
                    ]
                }
            }
        if request_type == "CreateInput":
            assert request_data is not None
            input_name = str(request_data["inputName"])
            self.inputs[input_name] = str(request_data["inputKind"])
            self.input_settings[input_name] = dict(request_data.get("inputSettings") or {})
            scene_item_id = self._add_scene_item(input_name)
            return {"responseData": {"sceneItemId": scene_item_id}}
        if request_type == "SetInputSettings":
            assert request_data is not None
            input_name = str(request_data["inputName"])
            current = dict(self.input_settings.get(input_name, {}))
            current.update(dict(request_data.get("inputSettings") or {}))
            self.input_settings[input_name] = current
            return {}
        if request_type == "GetSceneItemList":
            return {"responseData": {"sceneItems": [dict(item) for item in self.scene_items]}}
        if request_type == "CreateSceneItem":
            assert request_data is not None
            source_name = str(request_data["sourceName"])
            scene_item_id = self._add_scene_item(source_name)
            return {"responseData": {"sceneItemId": scene_item_id}}
        if request_type == "SetSceneItemIndex":
            assert request_data is not None
            scene_item_id = request_data["sceneItemId"]
            for item in self.scene_items:
                if item.get("sceneItemId") == scene_item_id:
                    item["sceneItemIndex"] = request_data["sceneItemIndex"]
                    break
            return {}
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

    def _add_scene_item(self, source_name: str) -> int:
        if any(item.get("sourceName") == source_name for item in self.scene_items):
            for item in self.scene_items:
                if item.get("sourceName") == source_name:
                    return int(item["sceneItemId"])
        scene_item_id = self._next_scene_item_id
        self._next_scene_item_id += 1
        self.scene_items.insert(
            0,
            {
                "sourceName": source_name,
                "sceneItemId": scene_item_id,
                "sceneItemIndex": 0,
            },
        )
        for index, item in enumerate(self.scene_items):
            item["sceneItemIndex"] = index
        return scene_item_id


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


def _request_types(client: _FakeObsClient) -> list[str]:
    return [request_type for request_type, _data in client.requests]


def _request_payloads(client: _FakeObsClient, request_type: str) -> list[dict[str, Any]]:
    return [
        data
        for seen_type, data in client.requests
        if seen_type == request_type and data is not None
    ]


def test_recorder_version_is_v0196() -> None:
    m = _import_recorder_module()

    assert m.RECORDER_VERSION == "lite-v0.19.6"


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


def test_obs_start_creates_capture_graph_before_record(tmp_path: Path) -> None:
    m = _import_recorder_module()
    obs_exe = tmp_path / "obs-studio" / "bin" / "64bit" / "obs64.exe"
    obs_exe.parent.mkdir(parents=True)
    obs_exe.write_text("fake", encoding="utf-8")
    obs_output = tmp_path / "obs-out" / "obs-file.mp4"
    obs_output.parent.mkdir()
    client = _FakeObsClient(obs_output)
    proc = _FakeProc()

    out_path = tmp_path / "session" / "video.mp4"
    m._start_obs_capture_layer(
        out_path,
        output_profile=m.VideoOutputProfile(width=1920, height=1080, fps=30.0),
        mc_window={"title": "Minecraft 1.21.4 - Singleplayer"},
        obs_exe=obs_exe,
        ws_client_factory=lambda: client,
        popen_factory=lambda args, *, cwd, **_kwargs: proc,
    )

    request_types = _request_types(client)
    start_index = request_types.index("StartRecord")
    assert request_types.index("SetVideoSettings") < start_index
    assert request_types.index("CreateScene") < start_index
    assert request_types.index("SetCurrentProgramScene") < start_index
    assert (
        max(
            index
            for index, request_type in enumerate(request_types)
            if request_type == "CreateInput"
        )
        < start_index
    )

    assert _request_payloads(client, "SetVideoSettings")[0] == {
        "baseWidth": 1920,
        "baseHeight": 1080,
        "outputWidth": 1920,
        "outputHeight": 1080,
        "fpsNumerator": 30,
        "fpsDenominator": 1,
    }
    assert _request_payloads(client, "CreateScene") == [{"sceneName": "MC"}]

    created_inputs = _request_payloads(client, "CreateInput")
    game_input = next(item for item in created_inputs if item["inputName"] == "oyster_game")
    display_input = next(item for item in created_inputs if item["inputName"] == "oyster_display")
    assert game_input["sceneName"] == "MC"
    assert game_input["inputKind"] == "game_capture"
    assert game_input["inputSettings"]["capture_mode"] == "window"
    assert game_input["inputSettings"]["window"] == (
        "Minecraft 1.21.4 - Singleplayer:GLFW30:javaw.exe"
    )
    assert game_input["inputSettings"]["priority"] == 2
    assert game_input["inputSettings"]["capture_audio"] is True
    assert display_input == {
        "sceneName": "MC",
        "inputName": "oyster_display",
        "inputKind": "monitor_capture",
        "inputSettings": {"capture_cursor": True},
        "sceneItemEnabled": True,
    }


def test_obs_start_updates_existing_game_capture_settings(tmp_path: Path) -> None:
    m = _import_recorder_module()
    obs_exe = tmp_path / "obs-studio" / "bin" / "64bit" / "obs64.exe"
    obs_exe.parent.mkdir(parents=True)
    obs_exe.write_text("fake", encoding="utf-8")
    obs_output = tmp_path / "obs-out" / "obs-file.mp4"
    obs_output.parent.mkdir()
    client = _FakeObsClient(
        obs_output,
        scenes=["MC"],
        inputs={
            "oyster_game": "game_capture",
            "oyster_display": "monitor_capture",
        },
        scene_items=[
            {"sourceName": "oyster_game", "sceneItemId": 10, "sceneItemIndex": 0},
            {"sourceName": "oyster_display", "sceneItemId": 11, "sceneItemIndex": 1},
        ],
    )
    proc = _FakeProc()

    out_path = tmp_path / "session" / "video.mp4"
    m._start_obs_capture_layer(
        out_path,
        output_profile=m.VideoOutputProfile(width=1920, height=1080, fps=30.0),
        mc_window={"title": "Minecraft #Modded"},
        obs_exe=obs_exe,
        ws_client_factory=lambda: client,
        popen_factory=lambda args, *, cwd, **_kwargs: proc,
    )

    request_types = _request_types(client)
    start_index = request_types.index("StartRecord")
    assert request_types.index("SetVideoSettings") < start_index
    assert request_types.index("SetCurrentProgramScene") < start_index
    assert "CreateScene" not in request_types
    assert "CreateInput" not in request_types
    assert "CreateSceneItem" not in request_types
    game_updates = [
        payload
        for payload in _request_payloads(client, "SetInputSettings")
        if payload["inputName"] == "oyster_game"
    ]
    assert game_updates
    assert game_updates[-1]["inputSettings"]["window"] == "Minecraft #23Modded:GLFW30:javaw.exe"


def test_obs_start_orders_display_capture_behind_game_capture(tmp_path: Path) -> None:
    m = _import_recorder_module()
    obs_exe = tmp_path / "obs-studio" / "bin" / "64bit" / "obs64.exe"
    obs_exe.parent.mkdir(parents=True)
    obs_exe.write_text("fake", encoding="utf-8")
    obs_output = tmp_path / "obs-out" / "obs-file.mp4"
    obs_output.parent.mkdir()
    client = _FakeObsClient(
        obs_output,
        scenes=["MC"],
        inputs={
            "oyster_game": "game_capture",
            "oyster_display": "monitor_capture",
        },
        scene_items=[
            {"sourceName": "oyster_game", "sceneItemId": 10, "sceneItemIndex": 0},
            {"sourceName": "oyster_display", "sceneItemId": 11, "sceneItemIndex": 1},
        ],
    )

    m._start_obs_capture_layer(
        tmp_path / "session" / "video.mp4",
        output_profile=m.VideoOutputProfile(width=1920, height=1080, fps=30.0),
        obs_exe=obs_exe,
        ws_client_factory=lambda: client,
        popen_factory=lambda args, *, cwd, **_kwargs: _FakeProc(),
    )

    index_by_source = {
        str(item["sourceName"]): int(item["sceneItemIndex"]) for item in client.scene_items
    }
    assert index_by_source["oyster_display"] == 0
    assert index_by_source["oyster_game"] > index_by_source["oyster_display"]
    scene_index_updates = _request_payloads(client, "SetSceneItemIndex")
    assert {
        "sceneName": "MC",
        "sceneItemId": 11,
        "sceneItemIndex": 0,
    } in scene_index_updates
    assert {
        "sceneName": "MC",
        "sceneItemId": 10,
        "sceneItemIndex": 1,
    } in scene_index_updates


def test_obs_display_capture_uses_monitor_under_minecraft_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    m = _import_recorder_module()
    monitors = [
        m.MonitorBounds(
            index=1,
            left=0,
            top=0,
            width=1920,
            height=1080,
            is_primary=True,
            device_name=r"\\.\DISPLAY1",
        ),
        m.MonitorBounds(
            index=2,
            left=1920,
            top=0,
            width=1920,
            height=1080,
            is_primary=False,
            device_name=r"\\.\DISPLAY2",
        ),
    ]
    monkeypatch.setattr(m, "_get_windows_monitor_bounds", lambda: monitors)

    settings = m._obs_display_capture_settings({"x": 2000, "y": 20, "width": 640, "height": 360})

    assert settings["monitor"] == 1
    assert settings["monitor_id"] == r"\\.\DISPLAY2"


def test_obs_record_directory_non_204_failure_propagates(tmp_path: Path) -> None:
    m = _import_recorder_module()
    obs_exe = tmp_path / "obs-studio" / "bin" / "64bit" / "obs64.exe"
    obs_exe.parent.mkdir(parents=True)
    obs_exe.write_text("fake", encoding="utf-8")
    obs_output = tmp_path / "obs-out" / "obs-file.mp4"
    obs_output.parent.mkdir()

    class _RecordDirFailClient(_FakeObsClient):
        def request(
            self,
            request_type: str,
            request_data: dict[str, Any] | None = None,
            **kwargs: Any,
        ) -> dict[str, Any]:
            if request_type == "SetRecordDirectory":
                self.requests.append((request_type, request_data))
                raise m.ObsWebSocketRequestError(
                    "SetRecordDirectory",
                    {"result": False, "code": 500, "comment": "denied"},
                )
            return super().request(request_type, request_data, **kwargs)

    client = _RecordDirFailClient(obs_output)
    proc = _FakeProc()

    with pytest.raises(m.ObsWebSocketError, match="SetRecordDirectory failed"):
        m._start_obs_capture_layer(
            tmp_path / "session" / "video.mp4",
            output_profile=m.VideoOutputProfile(width=1920, height=1080, fps=30.0),
            obs_exe=obs_exe,
            ws_client_factory=lambda: client,
            popen_factory=lambda args, *, cwd, **_kwargs: proc,
        )

    assert ("StartRecord", None) not in client.requests
    assert client.closed
    assert proc.terminated


def test_obs_start_websocket_wait_failure_terminates_without_nameerror(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    m = _import_recorder_module()
    obs_exe = tmp_path / "obs-studio" / "bin" / "64bit" / "obs64.exe"
    obs_exe.parent.mkdir(parents=True)
    obs_exe.write_text("fake", encoding="utf-8")
    proc = _FakeProc()

    def _raise_before_client(*_args: Any, **_kwargs: Any) -> None:
        raise m.ObsWebSocketError("OBS websocket unreachable: unit")

    monkeypatch.setattr(m, "_wait_for_obs_websocket", _raise_before_client)
    with pytest.raises(m.ObsWebSocketError, match="websocket unreachable"):
        m._start_obs_capture_layer(
            tmp_path / "session" / "video.mp4",
            output_profile=m.VideoOutputProfile(width=1920, height=1080, fps=30.0),
            obs_exe=obs_exe,
            ws_client_factory=lambda: _FakeObsClient(tmp_path / "obs.mp4"),
            popen_factory=lambda args, *, cwd, **_kwargs: proc,
        )

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
