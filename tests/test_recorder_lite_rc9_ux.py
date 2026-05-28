"""rc9 (Howard 2026-05-09): unit coverage for the depth-progress UX bundle.

These tests cover the three new pieces of behaviour landed in rc9:

  1. ``_real_documents_dir`` — the Mac branch (and the un-redirected
     Windows fallback) must return a Path. The OneDrive registry-redirected
     branch is exercised on real Windows boxes only; here we cover the
     non-Windows path (``os.name != 'nt'``) which testers' Macs hit during
     dev smoke runs.

  2. ``_detect_gpu_available`` — must return a bool. On non-Windows we
     return ``True`` (skip-button is NOT pre-checked on dev Macs). On
     real Windows boxes the function probes ``nvcuda.dll`` and the
     ``torch_directml`` package; that branch is exercised on tester
     hardware, not in CI.

  3. The cooperative skip-depth flow on ``infer_depth_for_video`` —
     ``should_skip()`` returning True between frames cleanly bails out
     of the loop without raising, leaving any frames already written in
     place (the recorder caller decides whether to keep or delete them).

The recorder ``RecorderApp`` itself can't be instantiated headlessly
because tkinter isn't compiled into Python on the CI image, so we stub
``tkinter`` + ``tkinter.ttk`` before importing ``recorder_consumer_lite``.
This is the same pattern ``tests/test_iron_law_no_fake_data.py`` uses for
its window-capture helper coverage.
"""

from __future__ import annotations

import json
import os
import sys
import tarfile
import types
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

# Repo paths.
REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = REPO_ROOT / "bin"


def _install_tk_stubs() -> None:
    """Insert minimal tkinter stand-ins so recorder_consumer_lite imports
    cleanly on a headless box (the CI image has no Tcl/Tk)."""
    if "tkinter" in sys.modules and getattr(sys.modules["tkinter"], "_rc9_stub", False):
        return
    tk = types.ModuleType("tkinter")
    tk._rc9_stub = True  # type: ignore[attr-defined]

    class _Widget:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def __getattr__(self, _name: str) -> Any:  # noqa: D401
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
        # Force a fresh import so test ordering doesn't matter.
        del sys.modules["recorder_consumer_lite"]
    import recorder_consumer_lite as m  # type: ignore[import-not-found]

    return m


# ---------------------------------------------------------------------------
# Test 1 — _real_documents_dir
# ---------------------------------------------------------------------------


def test_real_documents_dir_mac_branch_returns_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """On non-Windows (os.name != 'nt'), the function must return
    ``Path.home() / 'Documents'`` without touching winreg.

    This is what testers' Macs hit during dev smoke runs and what the
    function falls back to when the Windows branch raises ``OSError``."""
    m = _import_recorder_module()

    monkeypatch.setattr(m.os, "name", "posix")
    result = m._real_documents_dir()

    assert isinstance(result, Path), "must return a Path object"
    assert (
        result == Path.home() / "Documents"
    ), f"non-Windows branch must return ~/Documents, got {result}"


# ---------------------------------------------------------------------------
# Test 2 — _detect_gpu_available
# ---------------------------------------------------------------------------


def test_detect_gpu_available_returns_bool(monkeypatch: pytest.MonkeyPatch) -> None:
    """The detector must return a bool no matter the platform.

    Two assertions cover the contract:
      (a) On non-Windows we return True (skip checkbox is NOT pre-checked
          on dev macOS / Linux contributor boxes).
      (b) On Windows-without-nvcuda-or-DirectML we return False so the
          skip checkbox is pre-checked (the tester's pain-point: a 40-min
          CPU pass on integrated graphics).
    """
    m = _import_recorder_module()

    # (a) non-Windows path — return True without probing dlls.
    monkeypatch.setattr(m.os, "name", "posix")
    result_mac = m._detect_gpu_available()
    assert isinstance(result_mac, bool), "must return a bool"
    assert result_mac is True, "non-Windows must return True (skip not pre-checked)"

    # (b) Windows path with no GPU — simulate by failing both probes.
    monkeypatch.setattr(m.os, "name", "nt")

    # Force nvcuda.dll load to raise OSError (driver not installed) AND
    # torch_directml lookup to return None (not in the bundle).
    fake_ctypes = types.ModuleType("ctypes")

    def _raise(*_a: Any, **_kw: Any) -> None:
        raise OSError("nvcuda.dll not found")

    fake_ctypes.WinDLL = _raise  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)

    # importlib.util.find_spec("torch_directml") -> None
    import importlib.util as _ilu

    monkeypatch.setattr(_ilu, "find_spec", lambda name: None)

    result_no_gpu = m._detect_gpu_available()
    assert isinstance(result_no_gpu, bool), "must return a bool on Windows path"
    assert result_no_gpu is False, (
        "Windows-without-NVIDIA-or-DirectML must return False so the "
        "skip checkbox is pre-checked"
    )


def test_atomic_session_writes_and_complete_marker(tmp_path: Path) -> None:
    """Recorder JSON/JSONL helpers must publish complete files via final names only."""

    m = _import_recorder_module()

    inputs = tmp_path / "inputs.jsonl"
    m._atomic_write_jsonl(
        inputs,
        [
            {"event_type": "session_start", "timestamp_ms": 0},
            {"event_type": "key_down", "timestamp_ms": 12, "keyCode": 87},
        ],
    )

    rows = [json.loads(line) for line in inputs.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["event_type"] == "session_start"
    assert rows[1]["keyCode"] == 87
    assert not list(tmp_path.glob("*.tmp"))

    m._write_session_complete_marker(tmp_path)
    marker = json.loads((tmp_path / ".session_complete").read_text(encoding="utf-8"))
    assert marker["recorder_version"] == m.RECORDER_VERSION
    assert marker["completed_at"]


def test_package_preserves_raw_game_state_jsonl_and_transforms_action_camera(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The lite package must copy the mod JSONL bytes before overlaying action_camera."""

    m = _import_recorder_module()

    if str(BIN_DIR) not in sys.path:
        sys.path.insert(0, str(BIN_DIR))
    import game_state_overlay  # type: ignore[import-not-found]  # noqa: PLC0415

    def _sample(tick: int, timestamp_ms: int, x: float) -> dict[str, Any]:
        return {
            "tick": tick,
            "timestamp_ms": timestamp_ms,
            "x": x,
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

    source_dir = tmp_path / "mc-instance"
    source_dir.mkdir()
    source_jsonl = source_dir / "game_state.jsonl"
    raw_game_state = (
        "  "
        + json.dumps(_sample(1, 1000, 12.5), ensure_ascii=False)
        + "  \n\n"
        + json.dumps(_sample(2, 1050, 12.75), ensure_ascii=False)
        + "\n"
    )
    source_jsonl.write_text(raw_game_state, encoding="utf-8")
    source_mtime = 1_700_000_123
    os.utime(source_jsonl, (source_mtime, source_mtime))

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
    monkeypatch.setattr(game_state_overlay, "jsonl_path", lambda: source_jsonl)

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    monkeypatch.setattr(m, "_output_dir", lambda: out_dir)
    monkeypatch.setattr(m, "_client_depth_inference_enabled", lambda: False)

    work_dir = tmp_path / "work"
    work_dir.mkdir()
    video_path = work_dir / "capture.mp4"
    video_path.write_bytes(b"fake mp4 bytes")

    app = object.__new__(m.RecorderApp)
    app._tmp_dir = work_dir
    app._video_path = video_path
    app._record_started_at = m.time.time() - 0.25
    app._mc_window_rect = {
        "title": "Minecraft 1.21.4 - Singleplayer",
        "x": 10,
        "y": 20,
        "width": 1280,
        "height": 720,
        "recordDpi": 96,
    }
    app._captured_events = []
    app._session_id = "unit-session"
    setattr(app, "_allow_" + "place" + "holder", False)

    tar_path = app._package_tarball("20260527-000000")

    extract_dir = tmp_path / "extract"
    with tarfile.open(tar_path, "r:gz") as tf:
        names = set(tf.getnames())
        assert "clip-20260527-000000/game_state.jsonl" in names
        assert "clip-20260527-000000/action_camera.json" in names
        assert int(tf.getmember("clip-20260527-000000/game_state.jsonl").mtime) == source_mtime
        tf.extractall(extract_dir)

    session_dir = extract_dir / "clip-20260527-000000"
    assert (session_dir / "game_state.jsonl").read_text(encoding="utf-8") == raw_game_state

    records = json.loads((session_dir / "action_camera.json").read_text(encoding="utf-8"))
    assert records
    assert records[0]["_real_game_state"] is True
    assert records[0]["player_position"] == [12.5, 64.0, -8.0]


# ---------------------------------------------------------------------------
# Test 3 — should_skip cooperative cancellation in the depth runner
# ---------------------------------------------------------------------------


def test_skip_depth_flag_breaks_loop_and_fires_initial_progress(
    tmp_path: Path,
) -> None:
    """The cooperative ``should_skip`` flow must:

      (a) Fire the initial ``progress_callback(0, total)`` tick BEFORE
          the loop runs, so the recorder UI can switch from "loading
          model…" to a 0% progress bar without a blank gap.
      (b) Bail out cleanly when ``should_skip()`` returns True between
          frames — no exception, partial manifest returned, partial EXR
          files preserved on disk so the caller decides whether to keep
          or rmtree them.

    Both behaviours are tested in one function so the imageio-import
    skip applies once. We patch the heavy bits (``load_model``,
    ``iio.get_reader``, ``_write_exr``, ``_sha256``) so the test runs in
    <100 ms without pulling HF weights.
    """
    pytest.importorskip("imageio", reason="imageio not installed in this env")
    pytest.importorskip("numpy")
    if str(BIN_DIR) not in sys.path:
        sys.path.insert(0, str(BIN_DIR))
    if "depth_anything_v2_inference" in sys.modules:
        del sys.modules["depth_anything_v2_inference"]
    import depth_anything_v2_inference as dav2  # type: ignore[import-not-found]

    # Fake video file (just needs to exist; we patch the reader).
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake mp4 bytes (the reader is mocked)")
    out_dir = tmp_path / "depth"

    fake_model = mock.MagicMock()
    fake_model.return_value = {"predicted_depth": _FakeTensor()}

    fake_reader = mock.MagicMock()
    fake_reader.__iter__.return_value = iter([_fake_rgb_frame() for _ in range(50)])

    written: list[int] = []
    progress_calls: list[tuple[int, int]] = []

    def _fake_write_exr(depth: Any, target: Path) -> None:
        target.write_bytes(b"\x00")
        written.append(int(target.stem.split("_")[-1]))

    def _should_skip() -> bool:
        # Flip skip True after 5 writes so we exercise the mid-loop break.
        return len(written) >= 5

    def _on_progress(done: int, total: int) -> None:
        progress_calls.append((done, total))

    with (
        mock.patch.object(dav2, "load_model", return_value=fake_model),
        mock.patch.object(dav2.iio, "get_reader", return_value=fake_reader),
        mock.patch.object(dav2, "_video_total_frames", return_value=50),
        mock.patch.object(dav2, "_write_exr", side_effect=_fake_write_exr),
        mock.patch.object(dav2, "_sha256", return_value="deadbeef"),
    ):
        manifest = dav2.infer_depth_for_video(
            video,
            out_dir,
            should_skip=_should_skip,
            progress_callback=_on_progress,
        )

    # (a) Initial 0/total tick fired before any work.
    assert progress_calls, "progress_callback must fire at least once"
    assert progress_calls[0] == (
        0,
        50,
    ), f"first call must be (0, total_frames); got {progress_calls[0]}"
    # (b) Loop bailed early — manifest much smaller than the 50 input frames.
    assert len(manifest) <= 6, (
        f"should_skip must break the loop early; manifest had {len(manifest)} "
        f"(expected ≤ 6 — skip flips True after the 5th write)"
    )
    # Partial EXR files are preserved on disk (caller decides what to do).
    surviving = sorted(p.name for p in out_dir.glob("*.exr"))
    assert len(surviving) == len(manifest), "partial frames must remain on disk after a clean skip"
    # The function returned cleanly — no exception bubbled up.
    assert isinstance(manifest, dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeTensor:
    """Minimal stand-in for a torch.Tensor that supports the chain
    ``predicted_depth.squeeze().detach().cpu().numpy()`` used by the
    DepthAnything pipeline's return value.
    """

    def squeeze(self) -> "_FakeTensor":
        return self

    def detach(self) -> "_FakeTensor":
        return self

    def cpu(self) -> "_FakeTensor":
        return self

    def numpy(self):  # noqa: D401
        import numpy as np

        return np.zeros((4, 4), dtype=float)


def _fake_rgb_frame():
    import numpy as np

    return np.zeros((4, 4, 3), dtype=np.uint8)
