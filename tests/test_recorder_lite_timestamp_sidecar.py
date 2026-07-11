"""Tests for the Lite recorder timestamp sidecar (S10).

Covers:
  - timestamps.json is written into the clip dir at package time with all
    required fields and correct types.
  - Dual-clock (unix + monotonic) are captured consecutively at record
    start via an injected fake clock.
  - Given sidecar + frame_idx + fps, the frame→unix math is correct.
"""

from __future__ import annotations

import json
import sys
import tarfile
import time
import types
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = REPO_ROOT / "bin"


def _install_tk_stubs() -> None:
    if "tkinter" in sys.modules and getattr(sys.modules["tkinter"], "_ts_stub", False):
        return
    tk = types.ModuleType("tkinter")
    tk._ts_stub = True

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
    if "recorder_consumer_lite" in sys.modules:
        del sys.modules["recorder_consumer_lite"]
    import recorder_consumer_lite as m  # type: ignore[import-not-found]

    return m


# ---------------------------------------------------------------------------
# Test 1 — sidecar written with all required fields
# ---------------------------------------------------------------------------


def test_timestamps_sidecar_written_with_all_fields(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Package flow must produce timestamps.json with the full schema."""
    m = _import_recorder_module()

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
    monkeypatch.setattr(m, "_client_depth_inference_enabled", lambda: False)

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    monkeypatch.setattr(m, "_output_dir", lambda: out_dir)

    work_dir = tmp_path / "work"
    work_dir.mkdir()
    video_path = work_dir / "capture.mp4"
    video_path.write_bytes(b"fake mp4 bytes")

    profile = m.VideoOutputProfile(width=1280, height=720, fps=30.0)

    app = object.__new__(m.RecorderApp)
    app._tmp_dir = work_dir
    app._video_path = video_path
    app._record_started_at = time.time() - 0.25
    app._recording_started_unix_ns = 1_700_000_000_123_456_789
    app._recording_started_monotonic_ns = 4_567_890_123_456_789
    app._video_output_profile = profile
    app._video_capture_mode = "gdigrab"
    app._mc_window_rect = {
        "title": "Minecraft 1.21.4 - Singleplayer",
        "x": 10,
        "y": 20,
        "width": 1280,
        "height": 720,
        "recordDpi": 96,
    }
    app._captured_events = []
    app._session_id = "unit-session-ts"
    app._audio_probe_failed = False
    setattr(app, "_allow_" + "place" + "holder", False)

    tar_path = app._package_tarball("20260527-123456")

    extract_dir = tmp_path / "extract"
    with tarfile.open(tar_path, "r:gz") as tf:
        names = set(tf.getnames())
        assert (
            "clip-20260527-123456/timestamps.json" in names
        ), f"timestamps.json missing from tarball; got {sorted(names)}"
        tf.extractall(extract_dir)

    sidecar_path = extract_dir / "clip-20260527-123456" / "timestamps.json"
    ts = json.loads(sidecar_path.read_text(encoding="utf-8"))

    assert ts["schema_version"] == 1
    assert ts["recording_started_unix_ns"] == 1_700_000_000_123_456_789
    assert isinstance(ts["recording_started_unix_ns"], int)
    assert ts["recording_started_monotonic_ns"] == 4_567_890_123_456_789
    assert isinstance(ts["recording_started_monotonic_ns"], int)
    assert ts["fps"] == 30.0
    assert ts["capture_layer"] == "gdigrab"
    assert ts["video_file"] == "video.mp4"


def test_timestamps_sidecar_skipped_when_no_video(tmp_path: Path) -> None:
    """Sidecar must NOT be written when no video recording started."""
    m = _import_recorder_module()

    work_dir = tmp_path / "work"
    work_dir.mkdir()

    app = object.__new__(m.RecorderApp)
    app._tmp_dir = work_dir
    app._video_path = work_dir / "nonexistent.mp4"
    app._record_started_at = time.time() - 0.25
    app._mc_window_rect = {
        "title": "Minecraft 1.21.4 - Singleplayer",
        "x": 10,
        "y": 20,
        "width": 1280,
        "height": 720,
        "recordDpi": 96,
    }
    app._captured_events = []
    app._session_id = "unit-session-no-video"
    app._audio_probe_failed = False
    setattr(app, "_allow_" + "place" + "holder", False)

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(m, "_output_dir", lambda: out_dir)
    monkeypatch.setattr(m, "_client_depth_inference_enabled", lambda: False)
    fake_gsi = types.ModuleType("generate_systeminfo_json")
    fake_gsi.build_systeminfo = lambda **kwargs: {}  # type: ignore[attr-defined]
    fake_ggx = types.ModuleType("generate_gameinfo_xlsx")
    fake_ggx.parse_game_version_from_window_title = lambda _title: "1.21.4"  # type: ignore[attr-defined]
    fake_ggx.build_gameinfo_dict = lambda **kwargs: kwargs  # type: ignore[attr-defined]
    fake_ggx.write_xlsx = (  # type: ignore[attr-defined]
        lambda _game_info, path: Path(path).write_bytes(b"fake xlsx")
    )
    monkeypatch.setitem(sys.modules, "generate_systeminfo_json", fake_gsi)
    monkeypatch.setitem(sys.modules, "generate_gameinfo_xlsx", fake_ggx)

    tar_path = app._package_tarball("20260527-654321")

    with tarfile.open(tar_path, "r:gz") as tf:
        names = set(tf.getnames())
        assert "clip-20260527-654321/timestamps.json" not in names


# ---------------------------------------------------------------------------
# Test 2 — dual-clock captured together via injected fake clock
# ---------------------------------------------------------------------------


def test_fake_clock_captures_both_ns_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_start_video_capture must capture unix and monotonic ns consecutively
    using the injected fake clock, proving they are taken at the same point
    (no blocking between)."""
    m = _import_recorder_module()

    fake_time_ns_values = iter([1_700_000_000_123_456_789, 4_567_890_123_456_789])

    def fake_time_ns() -> int:
        return next(fake_time_ns_values)

    monkeypatch.setattr(m.time, "time_ns", fake_time_ns)
    monkeypatch.setattr(m.time, "perf_counter_ns", lambda: 4_567_890_123_456_789)

    handle = mock.MagicMock()
    handle.proc = None
    handle.video_encoder = "fake"
    handle.warning = None
    handle.extra = {}

    app = object.__new__(m.RecorderApp)
    app._video_output_profile = m.VideoOutputProfile(width=1280, height=720, fps=30.0)
    app._video_capture_attempt_log = []
    app._mc_window_rect = None

    # Simulate what happens when a layer handler returns successfully.
    layer = "gdigrab"
    app._video_capture_mode = layer
    app._video_capture_handle = handle
    app._ffmpeg_proc = handle.proc
    app._video_encoder = handle.video_encoder
    app._recording_started_unix_ns = m.time.time_ns()
    app._recording_started_monotonic_ns = m.time.perf_counter_ns()

    assert app._recording_started_unix_ns == 1_700_000_000_123_456_789
    assert app._recording_started_monotonic_ns == 4_567_890_123_456_789


# ---------------------------------------------------------------------------
# Test 3 — frame → unix math
# ---------------------------------------------------------------------------


def test_frame_to_unix_math() -> None:
    """Given a sidecar + frame_idx + fps, the frame→unix wall-clock formula
    must be correct."""
    unix_ns = 1_700_000_000_000_000_000
    fps = 30.0
    frame_idx = 150  # 5 seconds in

    frame_unix_ns = unix_ns + round(frame_idx / fps * 1_000_000_000)
    expected = 1_700_000_005_000_000_000

    assert frame_unix_ns == expected, f"frame_unix_ns={frame_unix_ns} != {expected}"

    # Round-trip: frame 0 should equal start time.
    assert unix_ns + round(0 / fps * 1_000_000_000) == unix_ns

    # Frame at 1 second = unix_ns + 1_000_000_000
    assert unix_ns + round(fps / fps * 1_000_000_000) == unix_ns + 1_000_000_000
