from __future__ import annotations

import shutil
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest

BIN_DIR = Path(__file__).resolve().parents[2] / "bin"


def _install_tk_stubs() -> None:
    if "tkinter" in sys.modules and getattr(
        sys.modules["tkinter"], "_video_validation_stub", False
    ):
        return

    tk = types.ModuleType("tkinter")
    tk._video_validation_stub = True  # type: ignore[attr-defined]

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


def _require_ffmpeg_tools(monkeypatch: pytest.MonkeyPatch, m: Any) -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.skip("ffmpeg/ffprobe not installed")
    monkeypatch.setattr(m, "_FFMPEG", Path(ffmpeg))
    monkeypatch.setattr(m, "_FFPROBE", Path(ffprobe))


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


def test_validate_recorded_video_rejects_static_frame_mp4(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    m = _import_recorder_module()
    _require_ffmpeg_tools(monkeypatch, m)
    video = tmp_path / "static.mp4"
    _make_fixture_video(
        video,
        "color=c=black:s=320x180:r=10:d=6",
        ffmpeg=m._FFMPEG,
    )

    valid, reason = m._validate_recorded_video(video)

    assert valid is False
    assert "identical" in reason or "low entropy" in reason


def test_validate_recorded_video_accepts_varied_mp4(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    m = _import_recorder_module()
    _require_ffmpeg_tools(monkeypatch, m)
    video = tmp_path / "varied.mp4"
    _make_fixture_video(
        video,
        "testsrc2=duration=6:size=320x180:rate=10",
        ffmpeg=m._FFMPEG,
    )

    valid, reason = m._validate_recorded_video(video)

    assert valid is True
    assert "unique frames" in reason


def test_validate_recorded_video_rejects_zero_byte_mp4(tmp_path: Path) -> None:
    m = _import_recorder_module()
    video = tmp_path / "zero.mp4"
    video.write_bytes(b"")

    valid, reason = m._validate_recorded_video(video)

    assert valid is False
    assert reason
