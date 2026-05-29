from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any
from unittest import mock

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


class _LiveProc:
    stdin = None

    def poll(self) -> None:
        return None


class _ExitedProc:
    stdin = None

    def __init__(self, returncode: int = 1) -> None:
        self.returncode = returncode

    def poll(self) -> int:
        return self.returncode


def _force_windows(m: Any) -> mock._patch:
    return mock.patch.object(m.os, "name", "nt")


def test_recorder_ffmpeg_cmd_pins_h265_bitrate(tmp_path: Path) -> None:
    m = _import_recorder_module()
    app = object.__new__(m.RecorderApp)
    app._mc_window_rect = {
        "title": "Minecraft 1.21.4",
        "x": 10,
        "y": 20,
        "width": 1280,
        "height": 720,
    }

    audio_report = m.AudioProbeReport(
        process_name="javaw.exe",
        selected=None,
        probes=[
            m.AudioSourceProbe(
                mode=m.AudioCaptureMode.NONE,
                label="test",
                available=False,
            )
        ],
    )

    with (
        _force_windows(m),
        mock.patch.object(m, "_CAPTURE_MODE", "ddagrab"),
        mock.patch.object(m, "_VIDEO_LAYER_INIT_TIMEOUT_SEC", 0.0),
        mock.patch.object(m, "_FFMPEG", tmp_path / "ffmpeg.exe"),
        mock.patch.object(m, "probe_audio_source_chain", return_value=audio_report),
        mock.patch.object(m.subprocess, "Popen", return_value=_LiveProc()) as popen,
    ):
        app._start_ffmpeg(tmp_path / "video.mp4")

    cmd = popen.call_args.args[0]
    assert cmd[cmd.index("-c:v") + 1] == "libx265"
    assert cmd[cmd.index("-b:v") + 1] == "10M"
    assert cmd[cmd.index("-maxrate") + 1] == "12M"
    assert cmd[cmd.index("-bufsize") + 1] == "20M"


def test_recorder_ffmpeg_cmd_supports_manual_ddagrab_mode(tmp_path: Path) -> None:
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

    with (
        _force_windows(m),
        mock.patch.object(m, "_CAPTURE_MODE", "ddagrab"),
        mock.patch.object(m, "_VIDEO_LAYER_INIT_TIMEOUT_SEC", 0.0),
        mock.patch.object(m, "_FFMPEG", tmp_path / "ffmpeg.exe"),
        mock.patch.object(m, "probe_audio_source_chain", return_value=audio_report),
        mock.patch.object(m.subprocess, "Popen", return_value=_LiveProc()) as popen,
    ):
        app._start_ffmpeg(tmp_path / "video.mp4")

    cmd = popen.call_args.args[0]
    assert cmd[cmd.index("-f") + 1] == "lavfi"
    assert cmd[cmd.index("-i") + 1] == "ddagrab=output_idx=0:framerate=30:draw_mouse=0"
    assert "-offset_x" not in cmd
    assert cmd[cmd.index("-vf") + 1].startswith("hwdownload,format=bgra,crop=1280:720:10:20,scale=")
    assert cmd[cmd.index("-c:v") + 1] == "libx265"
    assert app._video_capture_mode == "ddagrab"


def test_ddagrab_plan_captures_window_monitor_then_crops_to_mc_geometry() -> None:
    m = _import_recorder_module()
    monitors = [
        m.MonitorBounds(
            index=1,
            left=0,
            top=0,
            width=1920,
            height=1080,
            is_primary=True,
        ),
        m.MonitorBounds(
            index=2,
            left=1920,
            top=0,
            width=1920,
            height=1080,
            is_primary=False,
        ),
    ]

    with mock.patch.object(m, "_get_windows_monitor_bounds", return_value=monitors):
        plan = m._build_video_capture_plan("ddagrab", x=2000, y=30, w=1280, h=720)

    assert plan.input_args == (
        "-f",
        "lavfi",
        "-i",
        "ddagrab=output_idx=1:framerate=30:draw_mouse=0",
    )
    assert plan.pre_encode_filters == (
        "hwdownload",
        "format=bgra",
        "crop=1280:720:80:30",
    )
    assert plan.extra["output_idx"] == 1


def test_recorder_ffmpeg_cmd_falls_back_to_gdigrab_when_ddagrab_exits(
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

    with (
        _force_windows(m),
        mock.patch.object(m, "_CAPTURE_MODE", "auto"),
        mock.patch.object(m, "_VIDEO_AUTO_LAYERS", ("ddagrab", "gdigrab")),
        mock.patch.object(m, "_VIDEO_LAYER_INIT_TIMEOUT_SEC", 0.0),
        mock.patch.object(m, "_FFMPEG", tmp_path / "ffmpeg.exe"),
        mock.patch.object(m, "probe_audio_source_chain", return_value=audio_report),
        mock.patch.object(
            m.subprocess,
            "Popen",
            side_effect=[_ExitedProc(returncode=1), _LiveProc()],
        ) as popen,
    ):
        app._start_ffmpeg(tmp_path / "video.mp4")

    first_cmd = popen.call_args_list[0].args[0]
    second_cmd = popen.call_args_list[1].args[0]
    assert first_cmd[first_cmd.index("-f") + 1] == "lavfi"
    assert first_cmd[first_cmd.index("-i") + 1] == (
        "ddagrab=output_idx=0:framerate=30:draw_mouse=0"
    )
    assert second_cmd[second_cmd.index("-f") + 1] == "gdigrab"
    assert second_cmd[second_cmd.index("-offset_x") + 1] == "10"
    assert second_cmd[second_cmd.index("-offset_y") + 1] == "20"
    assert second_cmd[second_cmd.index("-video_size") + 1] == "1280x720"
    assert app._video_capture_mode == "gdigrab"


def test_ddagrab_failure_attempt_log_includes_rc_and_stderr(
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
    stderr = "Error initializing ddagrab: Invalid argument"

    with (
        _force_windows(m),
        mock.patch.object(m, "_CAPTURE_MODE", "auto"),
        mock.patch.object(m, "_VIDEO_AUTO_LAYERS", ("ddagrab", "gdigrab")),
        mock.patch.object(m, "_VIDEO_LAYER_INIT_TIMEOUT_SEC", 0.0),
        mock.patch.object(m, "_FFMPEG", tmp_path / "ffmpeg.exe"),
        mock.patch.object(m, "probe_audio_source_chain", return_value=audio_report),
        mock.patch.object(m, "_video_capture_stderr_text", return_value=stderr),
        mock.patch.object(
            m.subprocess,
            "Popen",
            side_effect=[_ExitedProc(returncode=-22), _LiveProc()],
        ),
    ):
        app._start_ffmpeg(tmp_path / "video.mp4")

    failed = app._video_capture_attempt_log[0]
    assert failed["layer"] == "ddagrab"
    assert failed["status"] == "failed"
    assert failed["rc"] == -22
    assert failed["stderr"] == stderr
    assert failed["stderr_log"].endswith("video.ddagrab.stderr.log")


def test_stop_ffmpeg_waits_60s_for_clean_mp4_finalization(tmp_path: Path) -> None:
    m = _import_recorder_module()
    app = object.__new__(m.RecorderApp)
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(_mp4_box(b"ftyp", b"isom0000") + _mp4_box(b"moov"))

    class _Stdin:
        def __init__(self) -> None:
            self.writes: list[bytes] = []
            self.flushed = False

        def write(self, data: bytes) -> None:
            self.writes.append(data)

        def flush(self) -> None:
            self.flushed = True

    class _Proc:
        def __init__(self) -> None:
            self.stdin = _Stdin()
            self.wait_timeouts: list[float] = []
            self.terminated = False

        def wait(self, timeout: float) -> int:
            self.wait_timeouts.append(timeout)
            return 0

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            raise AssertionError("clean ffmpeg shutdown must not be killed")

    proc = _Proc()
    app._ffmpeg_proc = proc
    app._video_path = video_path

    with (
        mock.patch.object(m, "_fsync_file") as fsync_file,
        mock.patch.object(m, "_validate_recorded_video", return_value=(True, "unit")),
    ):
        app._stop_ffmpeg()

    assert proc.stdin.writes == [b"q\n"]
    assert proc.stdin.flushed
    assert proc.wait_timeouts == [60.0]
    assert not proc.terminated
    assert app._ffmpeg_proc is None
    fsync_file.assert_called_once_with(video_path)


def test_stop_ffmpeg_repairs_missing_moov_after_forced_stop(tmp_path: Path) -> None:
    m = _import_recorder_module()
    app = object.__new__(m.RecorderApp)
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(_mp4_box(b"ftyp", b"isom0000") + _mp4_box(b"mdat", b"payload"))

    class _Stdin:
        def write(self, _data: bytes) -> None:
            pass

        def flush(self) -> None:
            pass

    class _Proc:
        def __init__(self) -> None:
            self.stdin = _Stdin()
            self.wait_timeouts: list[float] = []
            self.terminated = False
            self.killed = False

        def wait(self, timeout: float) -> int:
            self.wait_timeouts.append(timeout)
            if len(self.wait_timeouts) == 1:
                raise m.subprocess.TimeoutExpired("ffmpeg", timeout)
            return 0

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True

    proc = _Proc()
    app._ffmpeg_proc = proc
    app._video_path = video_path
    remux_calls: list[list[str]] = []

    def _fake_run(cmd: list[str], **_kwargs: Any) -> Any:
        remux_calls.append(cmd)
        fixed_path = Path(cmd[-1])
        fixed_path.write_bytes(
            _mp4_box(b"ftyp", b"isom0000") + _mp4_box(b"moov") + _mp4_box(b"mdat", b"payload")
        )
        return type("Completed", (), {"returncode": 0, "stderr": b""})()

    with (
        mock.patch.object(m, "_FFMPEG", tmp_path / "ffmpeg.exe"),
        mock.patch.object(m.subprocess, "run", side_effect=_fake_run),
        mock.patch.object(m, "_validate_recorded_video", return_value=(True, "unit")),
    ):
        app._stop_ffmpeg()

    assert proc.wait_timeouts == [60.0, 3.0]
    assert proc.terminated
    assert not proc.killed
    assert remux_calls == [
        [
            str(tmp_path / "ffmpeg.exe"),
            "-y",
            "-i",
            str(video_path),
            "-c",
            "copy",
            str(tmp_path / "video.fixed.mp4"),
        ]
    ]
    assert m._mp4_has_moov_atom(video_path)
    assert not (tmp_path / "video.fixed.mp4").exists()


def test_recorder_source_has_no_six_min_recording_cap() -> None:
    src = (BIN_DIR / "recorder_consumer_lite.py").read_text(encoding="utf-8")

    assert "MAX_RECORD_SECONDS" not in src
    assert '"-t",' not in src
