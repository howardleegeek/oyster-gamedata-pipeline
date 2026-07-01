from __future__ import annotations

import json
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest

BIN_DIR = Path(__file__).resolve().parents[2] / "bin"


def _install_tk_stubs() -> None:
    if "tkinter" in sys.modules and getattr(sys.modules["tkinter"], "_audio_chain_stub", False):
        return

    tk = types.ModuleType("tkinter")
    tk._audio_chain_stub = True  # type: ignore[attr-defined]

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
    # Use ModuleType instead of SimpleNamespace so that mock.patch() works correctly
    # SimpleNamespace doesn't support attribute assignment which mock.patch needs
    messagebox = types.ModuleType("tkinter.messagebox")
    messagebox.showerror = lambda *a, **kw: None
    messagebox.askyesno = lambda title, message, parent=None: False
    tk.messagebox = messagebox

    ttk = types.ModuleType("tkinter.ttk")
    ttk.Progressbar = _Widget
    tk.ttk = ttk

    sys.modules["tkinter"] = tk
    sys.modules["tkinter.ttk"] = ttk
    sys.modules["tkinter.messagebox"] = messagebox


def _import_recorder_module() -> Any:
    _install_tk_stubs()
    if str(BIN_DIR) not in sys.path:
        sys.path.insert(0, str(BIN_DIR))
    sys.modules.pop("recorder_consumer_lite", None)
    import recorder_consumer_lite as m  # type: ignore[import-not-found]

    return m


def _force_windows(monkeypatch: pytest.MonkeyPatch, m: Any, *, build: int = 19045) -> None:
    monkeypatch.setattr(m.os, "name", "nt")
    monkeypatch.setattr(
        m.sys,
        "getwindowsversion",
        lambda: types.SimpleNamespace(build=build),
        raising=False,
    )


def test_audio_chain_prefers_application_audio_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    m = _import_recorder_module()
    _force_windows(monkeypatch, m)

    dshow = """
    [dshow @ 000001] DirectShow audio devices
    [dshow @ 000001]  "Application Audio Capture (javaw.exe)" (audio)
    [dshow @ 000001]  "Microphone (Realtek Audio)" (audio)
    """

    monkeypatch.setattr(
        m, "_list_ffmpeg_devices", lambda demuxer: dshow if demuxer == "dshow" else ""
    )
    monkeypatch.setattr(m, "_ffmpeg_supports_device", lambda _demuxer: False)

    report = m.probe_audio_source_chain("javaw.exe")

    assert report.selected is not None
    assert report.selected.mode == m.AudioCaptureMode.APPLICATION
    assert report.selected.ffmpeg_args == (
        "-f",
        "dshow",
        "-i",
        "audio=Application Audio Capture (javaw.exe)",
    )


def test_audio_chain_uses_desktop_before_microphone(monkeypatch: pytest.MonkeyPatch) -> None:
    m = _import_recorder_module()
    _force_windows(monkeypatch, m)

    dshow = """
    [dshow @ 000001] DirectShow audio devices
    [dshow @ 000001]  "Microphone (Realtek Audio)" (audio)
    """
    wasapi = """
    [wasapi @ 000001] WASAPI output devices
    [wasapi @ 000001]  "Speakers (Realtek Audio)"
    """

    def _devices(demuxer: str) -> str:
        return dshow if demuxer == "dshow" else wasapi

    monkeypatch.setattr(m, "_list_ffmpeg_devices", _devices)
    monkeypatch.setattr(m, "_ffmpeg_supports_device", lambda demuxer: demuxer == "wasapi")

    report = m.probe_audio_source_chain("javaw.exe")

    assert report.selected is not None
    assert report.selected.mode == m.AudioCaptureMode.DESKTOP
    assert report.selected.ffmpeg_args == ("-f", "wasapi", "-i", "loopback")
    assert report.probes[2].mode == m.AudioCaptureMode.INPUT
    assert report.probes[2].available is True


def test_audio_chain_falls_back_to_any_input_device(monkeypatch: pytest.MonkeyPatch) -> None:
    m = _import_recorder_module()
    _force_windows(monkeypatch, m)

    dshow = """
    [dshow @ 000001] DirectShow audio devices
    [dshow @ 000001]  "Microphone Array (USB Audio)" (audio)
    """

    monkeypatch.setattr(
        m, "_list_ffmpeg_devices", lambda demuxer: dshow if demuxer == "dshow" else ""
    )
    monkeypatch.setattr(m, "_ffmpeg_supports_device", lambda _demuxer: False)

    report = m.probe_audio_source_chain("javaw.exe")

    assert report.selected is not None
    assert report.selected.mode == m.AudioCaptureMode.INPUT
    assert report.selected.ffmpeg_args == (
        "-f",
        "dshow",
        "-i",
        "audio=Microphone Array (USB Audio)",
    )


def test_ffmpeg_probe_default_timeout_is_30_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    m = _import_recorder_module()
    seen: dict[str, Any] = {}

    def _run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen["cmd"] = cmd
        seen["timeout"] = kwargs["timeout"]
        return subprocess.CompletedProcess(cmd, 0, stdout="out", stderr="err")

    monkeypatch.setattr(m.subprocess, "run", _run)

    rc, output = m._run_ffmpeg_probe(["-hide_banner", "-devices"])

    assert rc == 0
    assert output == "outerr"
    assert seen["timeout"] == 30.0


def test_ffmpeg_probe_logs_slow_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    m = _import_recorder_module()
    traces: list[str] = []
    ticks = iter([100.0, 106.25])

    def _run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(m.subprocess, "run", _run)
    monkeypatch.setattr(m.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(m, "_trace", traces.append)

    rc, _output = m._run_ffmpeg_probe(["-hide_banner", "-list_devices", "true"])

    assert rc == 0
    assert traces == [
        "WARNING: ffmpeg_probe slow completion "
        "elapsed=6.2s timeout=30.0s args=-hide_banner -list_devices true"
    ]


def test_probe_audio_chain_cli_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    m = _import_recorder_module()
    selected = m.AudioSourceProbe(
        mode=m.AudioCaptureMode.DESKTOP,
        label="Desktop Audio Output (WASAPI loopback)",
        available=True,
        ffmpeg_args=("-f", "wasapi", "-i", "loopback"),
        device="loopback",
        reason="test probe",
        fallback_used=True,
    )
    report = m.AudioProbeReport(process_name="javaw.exe", selected=selected, probes=[selected])
    monkeypatch.setattr(m, "probe_audio_source_chain", lambda _process_name: report)

    rc = m.main(["--probe-audio-chain-json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["selected"]["mode"] == m.AudioCaptureMode.DESKTOP
    assert payload["selected"]["ffmpeg_args"] == ["-f", "wasapi", "-i", "loopback"]
