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
    if "tkinter" in sys.modules and getattr(sys.modules["tkinter"], "_video_layers_stub", False):
        return

    tk = types.ModuleType("tkinter")
    tk._video_layers_stub = True  # type: ignore[attr-defined]

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


def _audio_report(m: Any) -> Any:
    return m.AudioProbeReport(process_name="javaw.exe", selected=None, probes=[])


def _app(m: Any) -> Any:
    app = object.__new__(m.RecorderApp)
    app._mc_window_rect = {
        "title": "Minecraft 1.21.4",
        "x": 10,
        "y": 20,
        "width": 1280,
        "height": 720,
        "recordDpi": 96,
    }
    return app


def test_video_capture_optional_imports_are_tolerated_on_macos(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    m = _import_recorder_module()
    monkeypatch.setattr(m.os, "name", "posix")

    assert "windows-capture" in m._VALID_CAPTURE_MODES
    assert "mss" in m._VALID_CAPTURE_MODES
    assert (
        m._start_layer(
            "windows-capture",
            tmp_path / "video.mp4",
            x=0,
            y=0,
            w=640,
            h=360,
            audio_inputs=[],
            audio_codec=[],
            creationflags=0,
        )
        is None
    )


def test_video_capture_layer_selection_tries_auto_layers_in_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    m = _import_recorder_module()
    app = _app(m)
    attempts: list[str] = []

    def _fake_start_layer(layer: str, out_path: Path, **_kwargs: Any) -> Any:
        attempts.append(layer)
        if layer != "ddagrab":
            raise RuntimeError(f"{layer} failed")
        return m.VideoCaptureHandle(
            layer="ddagrab",
            out_path=out_path,
            stdin_kind="control",
            proc=types.SimpleNamespace(poll=lambda: None),
        )

    monkeypatch.setattr(m, "_CAPTURE_MODE", "auto")
    monkeypatch.setattr(m, "_VIDEO_AUTO_LAYERS", ("windows-capture", "mss", "ddagrab", "gdigrab"))
    monkeypatch.setattr(m, "probe_audio_source_chain", lambda _process_name: _audio_report(m))
    monkeypatch.setattr(m, "_start_layer", _fake_start_layer)

    app._start_ffmpeg(tmp_path / "video.mp4")

    assert attempts == ["windows-capture", "mss", "ddagrab"]
    assert app._video_capture_mode == "ddagrab"
    assert app._video_capture_attempt_log[-1]["status"] == "selected"


def test_all_video_layers_fail_packages_data_without_video(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    m = _import_recorder_module()
    app = _app(m)

    monkeypatch.setattr(m, "_CAPTURE_MODE", "auto")
    monkeypatch.setattr(m, "probe_audio_source_chain", lambda _process_name: _audio_report(m))
    monkeypatch.setattr(
        m,
        "_start_layer",
        lambda layer, out_path, **_kwargs: (_ for _ in ()).throw(RuntimeError(f"{layer} failed")),
    )

    work_dir = tmp_path / "work"
    out_dir = tmp_path / "out"
    work_dir.mkdir()
    out_dir.mkdir()
    monkeypatch.setattr(m, "_output_dir", lambda: out_dir)
    monkeypatch.setattr(m, "_client_depth_inference_enabled", lambda: False)
    monkeypatch.setattr(m, "_depth_mode", lambda: "server")

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
    gs_text = '{"timestamp_ms":0,"x":1}\n'
    gs_source = work_dir / "source_game_state.jsonl"
    gs_source.write_text(gs_text, encoding="utf-8")
    fake_gso = types.ModuleType("game_state_overlay")
    fake_gso.load = lambda _path=None: [{"timestamp_ms": 0}]  # type: ignore[attr-defined]
    fake_gso.jsonl_path = lambda: str(gs_source)  # type: ignore[attr-defined]
    fake_gso.lookup_at_ms = lambda samples, _ms: samples[0]  # type: ignore[attr-defined]
    fake_gso.apply_to_record = lambda rec, _sample: rec.update(  # type: ignore[attr-defined]
        {"camera_position": [1.0, 2.0, 3.0]}
    )

    monkeypatch.setitem(sys.modules, "generate_systeminfo_json", fake_gsi)
    monkeypatch.setitem(sys.modules, "generate_gameinfo_xlsx", fake_ggx)
    monkeypatch.setitem(sys.modules, "game_state_overlay", fake_gso)

    def _fake_silent_audio(session_dir: Path, *, duration: float, reason: str) -> None:
        (session_dir / "audio.flac").write_bytes(b"silent")
        (session_dir / "audio_check.json").write_text(
            json.dumps(
                {"audio_source": "silent_fallback", "duration_sec": duration, "reason": reason}
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(m, "_generate_silent_audio_fallback_for_duration", _fake_silent_audio)

    app._tmp_dir = work_dir
    app._video_path = work_dir / "video.mp4"
    app._record_started_at = m.time.time() - 0.25
    app._captured_events = []
    app._session_id = "video-fallback-unit"
    app._allow_placeholder = False

    app._start_ffmpeg(work_dir / "video.mp4")
    assert app._video_capture_mode == "none"

    tar_path = app._package_tarball("20260528-010203")
    extract_dir = tmp_path / "extract"
    with tarfile.open(tar_path, "r:gz") as tf:
        tf.extractall(extract_dir)

    session_dir = extract_dir / "clip-20260528-010203"
    metadata = json.loads((session_dir / "metadata.json").read_text(encoding="utf-8"))

    assert not (session_dir / "video.mp4").exists()
    assert (session_dir / "game_state.jsonl").read_text(encoding="utf-8") == gs_text
    assert (session_dir / "inputs.jsonl").is_file()
    assert (session_dir / "audio.flac").is_file()
    assert metadata["video_capture"]["selected_layer"] == "none"
    assert metadata["video_capture"]["validation_passed"] is False
    assert metadata["video_capture"]["validation_reason"] == "video.mp4 does not exist"
    assert "video_missing_data_only_session" in metadata["video_capture"]["warnings"]
    assert "video_validation_failed" in metadata["video_capture"]["warnings"]
