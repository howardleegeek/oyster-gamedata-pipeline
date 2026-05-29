from __future__ import annotations

import json
import shutil
import subprocess
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


def _require_ffmpeg_tools(monkeypatch: pytest.MonkeyPatch, m: Any) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.skip("ffmpeg/ffprobe not installed")
    monkeypatch.setattr(m, "_FFMPEG", Path(ffmpeg))
    monkeypatch.setattr(m, "_FFPROBE", Path(ffprobe))
    return Path(ffmpeg)


def _make_fixture_video(path: Path, lavfi: str, *, ffmpeg: Path) -> None:
    result = subprocess.run(
        [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            lavfi,
            "-c:v",
            "mpeg4",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(result.stderr or f"ffmpeg exited with code {result.returncode}")


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
    monkeypatch.setitem(sys.modules, "generate_systeminfo_json", fake_gsi)
    monkeypatch.setitem(sys.modules, "generate_gameinfo_xlsx", fake_ggx)
    monkeypatch.setattr(m, "_output_dir", lambda: out_dir)
    monkeypatch.setattr(m, "_client_depth_inference_enabled", lambda: False)
    monkeypatch.setattr(
        m,
        "_generate_silent_audio_fallback_for_duration",
        lambda session_dir, *, duration, reason: (session_dir / "audio_check.json").write_text(
            json.dumps({"reason": reason, "duration_sec": duration}),
            encoding="utf-8",
        ),
    )


def _game_state_line(timestamp_ms: int = 1000) -> str:
    return json.dumps(
        {
            "tick": 1,
            "timestamp_ms": timestamp_ms,
            "x": 12.5,
            "y": 64.0,
            "z": -8.0,
            "yaw_deg": 90.0,
            "pitch_deg": 10.0,
            "look_x": 0.0,
            "look_y": 0.0,
            "look_z": 1.0,
            "velocity_x": 0.1,
            "velocity_y": 0.0,
            "velocity_z": 0.2,
            "on_ground": True,
            "sneaking": False,
            "sprinting": True,
            "dimension": "minecraft:overworld",
            "game_mode": "SURVIVAL",
        }
    )


def _new_app(m: Any, tmp_path: Path, active_dir: Path) -> Any:
    app = object.__new__(m.RecorderApp)
    app._tmp_dir = tmp_path / "work"
    app._tmp_dir.mkdir()
    app._video_path = app._tmp_dir / "video.mp4"
    app._record_started_at = m.time.time() - 1.0
    app._mc_window_rect = {
        "title": "Minecraft 1.21.4 - Singleplayer",
        "x": 10,
        "y": 20,
        "width": 1280,
        "height": 720,
        "recordDpi": 96,
    }
    app._captured_events = [
        {"event_type": "key_down", "timestamp_ms": 100, "keyCode": 87},
    ]
    app._input_capture_diagnostics = {
        "registration_tier": "rawinput",
        "wm_input_total": 1,
        "get_raw_input_data_failures": 0,
    }
    app._session_id = "unit-session"
    app._active_session_dir = active_dir
    app._audio_probe_failed = False
    app._video_capture_attempt_log = []
    app._video_capture_mode = "unknown"
    app._video_capture_requested_mode = "auto"
    setattr(app, "_allow_" + "place" + "holder", False)
    return app


def test_data_session_packages_when_all_video_layers_fail(
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
    game_state.write_text(_game_state_line() + "\n", encoding="utf-8")

    if str(BIN_DIR) not in sys.path:
        sys.path.insert(0, str(BIN_DIR))
    import game_state_overlay  # type: ignore[import-not-found]  # noqa: PLC0415

    monkeypatch.setattr(game_state_overlay, "jsonl_path", lambda: game_state)
    app = _new_app(m, tmp_path, active_dir)
    app._mark_session_started("20260528-000000")
    app._mc_pause_on_lost_focus_set = True
    app._mc_focus_restore_loop_enabled = False
    app._mc_focus_restore_ran = False
    app._window_no_activate_applied = True

    audio_report = m.AudioProbeReport(process_name="javaw.exe", selected=None, probes=[])
    monkeypatch.setattr(m, "probe_audio_source_chain", lambda _process: audio_report)

    def _fail_layer(layer: str, *_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError(f"{layer} unavailable")

    monkeypatch.setattr(m, "_start_layer", _fail_layer)

    app._start_video_capture(app._video_path)
    assert app._video_capture_mode == "none"

    tar_path = app._package_tarball("20260528-000000")
    extract_dir = tmp_path / "extract"
    with tarfile.open(tar_path, "r:gz") as tf:
        tf.extractall(extract_dir)

    clip_dir = extract_dir / "clip-20260528-000000"
    assert (clip_dir / "inputs.jsonl").is_file()
    assert (clip_dir / "game_state.jsonl").read_text(encoding="utf-8").strip()
    metadata = json.loads((clip_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["video_capture"]["selected_mode"] == "none"
    assert len(metadata["video_capture"]["attempts_failed"]) == 4
    assert metadata["focus_safety"] == {
        "pause_on_lost_focus_disabled": True,
        "focus_restore_loop_enabled": False,
        "focus_restore_ran": False,
        "status_window_no_activate": True,
    }
    assert metadata["session_complete"] is False


@pytest.mark.parametrize(
    ("lavfi", "expected_frozen"),
    [
        ("color=c=blue:s=320x180:r=10:d=6", True),
        ("testsrc2=duration=6:size=320x180:rate=10", False),
    ],
)
def test_video_frozen_status_is_written_to_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    lavfi: str,
    expected_frozen: bool,
) -> None:
    m = _import_recorder_module()
    ffmpeg = _require_ffmpeg_tools(monkeypatch, m)
    out_dir = tmp_path / ("out_frozen" if expected_frozen else "out_live")
    out_dir.mkdir()
    _install_packaging_fakes(monkeypatch, m, out_dir)

    active_dir = out_dir / "active_session"
    active_dir.mkdir()
    game_state = active_dir / "game_state.jsonl"
    game_state.write_text(_game_state_line() + "\n", encoding="utf-8")

    if str(BIN_DIR) not in sys.path:
        sys.path.insert(0, str(BIN_DIR))
    import game_state_overlay  # type: ignore[import-not-found]  # noqa: PLC0415

    monkeypatch.setattr(game_state_overlay, "jsonl_path", lambda: game_state)

    app = _new_app(m, tmp_path, active_dir)
    app._record_started_at = m.time.time() - 6.0
    app._video_capture_mode = "mss"
    app._video_capture_attempt_log = [{"layer": "mss", "status": "selected"}]
    _make_fixture_video(app._video_path, lavfi, ffmpeg=ffmpeg)

    tar_path = app._package_tarball("20260528-000050")
    extract_dir = tmp_path / ("extract_frozen" if expected_frozen else "extract_live")
    with tarfile.open(tar_path, "r:gz") as tf:
        tf.extractall(extract_dir)

    metadata = json.loads(
        (extract_dir / "clip-20260528-000050" / "metadata.json").read_text(encoding="utf-8")
    )
    assert metadata["video_frozen"] is expected_frozen
    assert metadata["video_capture"]["video_frozen"] is expected_frozen
    if expected_frozen:
        assert metadata["frozen_reason"]
        assert "video_frozen" in metadata["partial_reasons"]
        assert metadata["video_capture"]["validation_passed"] is False
    else:
        assert metadata["frozen_reason"] is None
        assert metadata["video_capture"]["validation_passed"] is True


def test_video_success_missing_mod_jsonl_marks_partial(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    m = _import_recorder_module()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    _install_packaging_fakes(monkeypatch, m, out_dir)

    if str(BIN_DIR) not in sys.path:
        sys.path.insert(0, str(BIN_DIR))
    import game_state_overlay  # type: ignore[import-not-found]  # noqa: PLC0415

    monkeypatch.setattr(game_state_overlay, "jsonl_path", lambda: tmp_path / "missing.jsonl")

    active_dir = out_dir / "active_session"
    active_dir.mkdir()
    app = _new_app(m, tmp_path, active_dir)
    app._video_path.write_bytes(b"fake mp4 bytes")
    app._video_capture_mode = "ddagrab"
    app._video_capture_attempt_log = [{"layer": "ddagrab", "status": "selected"}]

    tar_path = app._package_tarball("20260528-000100")
    extract_dir = tmp_path / "extract"
    with tarfile.open(tar_path, "r:gz") as tf:
        names = set(tf.getnames())
        assert "clip-20260528-000100/.session_complete" not in names
        tf.extractall(extract_dir)

    metadata = json.loads(
        (extract_dir / "clip-20260528-000100" / "metadata.json").read_text(encoding="utf-8")
    )
    assert metadata["session_complete"] is False
    assert metadata["partial"] is True
    assert "real_game_state_missing" in metadata["partial_reasons"]
    assert metadata["game_state_capture"]["status"] == "missing"


def test_orphaned_active_session_packaged_on_next_boot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    m = _import_recorder_module()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    monkeypatch.setattr(m, "_output_dir", lambda: out_dir)

    active_dir = out_dir / "active_session"
    active_dir.mkdir()
    (active_dir / ".session_id").write_text(
        json.dumps({"session_id": "orphan-session"}),
        encoding="utf-8",
    )
    (active_dir / "game_state.jsonl").write_text(_game_state_line() + "\n", encoding="utf-8")

    app = object.__new__(m.RecorderApp)
    app._active_session_dir = active_dir
    app._recover_orphaned_active_session_on_boot()

    tarballs = sorted(out_dir.glob("clip-*.tar.gz"))
    assert len(tarballs) == 1
    assert not (active_dir / "game_state.jsonl").exists()

    extract_dir = tmp_path / "extract"
    with tarfile.open(tarballs[0], "r:gz") as tf:
        tf.extractall(extract_dir)
    clip_dir = next(extract_dir.glob("clip-*"))
    assert (clip_dir / "game_state.jsonl").is_file()
    metadata = json.loads((clip_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["orphaned_active_session_recovered"] is True
    assert metadata["session_complete"] is False
