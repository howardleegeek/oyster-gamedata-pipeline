from __future__ import annotations

import ctypes
import subprocess
import sys
from ctypes import POINTER, byref, c_uint, sizeof
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = REPO_ROOT / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

import raw_input_capture as ric  # noqa: E402


def test_import_survives_missing_optional_wintype_aliases() -> None:
    code = f"""
from ctypes import wintypes
for name in ("HCURSOR", "HBRUSH", "HICON", "HINSTANCE", "HMODULE", "ATOM", "BOOL", "LPVOID"):
    if hasattr(wintypes, name):
        delattr(wintypes, name)
import sys
sys.path.insert(0, {str(BIN_DIR)!r})
import raw_input_capture as ric
capture = ric.RawInputCapture(lambda _dx, _dy, _ts: None, platform_name="posix")
assert capture.start() is False
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


class _FakeUser32:
    def __init__(self) -> None:
        self.register_calls: list[ric.RAWINPUTDEVICE] = []
        self.raw_input = ric.RAWINPUT()
        self.raw_input.header.dwType = ric.RIM_TYPEMOUSE
        self.raw_input.data.mouse.usFlags = ric.MOUSE_MOVE_RELATIVE
        self.raw_input.data.mouse.lLastX = 13
        self.raw_input.data.mouse.lLastY = -7

    def RegisterRawInputDevices(self, devices: Any, count: int, size: int) -> int:
        assert count == 1
        assert size == sizeof(ric.RAWINPUTDEVICE)
        device = ctypes.cast(devices, POINTER(ric.RAWINPUTDEVICE)).contents
        self.register_calls.append(
            ric.RAWINPUTDEVICE(
                usUsagePage=device.usUsagePage,
                usUsage=device.usUsage,
                dwFlags=device.dwFlags,
                hwndTarget=device.hwndTarget,
            )
        )
        return 1

    def GetRawInputData(
        self,
        _hrawinput: int,
        command: int,
        data: Any,
        size_ptr: Any,
        header_size: int,
    ) -> int:
        assert command == ric.RID_INPUT
        assert header_size == sizeof(ric.RAWINPUTHEADER)
        size_ref = ctypes.cast(size_ptr, POINTER(c_uint)).contents
        size_ref.value = sizeof(ric.RAWINPUT)
        if data is None:
            return 0
        ctypes.memmove(data, byref(self.raw_input), sizeof(ric.RAWINPUT))
        return sizeof(ric.RAWINPUT)


class _FakeWinFunc:
    def __init__(self, name: str, result: int | None = 1) -> None:
        self.name = name
        self.result = result
        self.calls: list[tuple[Any, ...]] = []

    def __call__(self, *args: Any) -> int | None:
        self.calls.append(args)
        if (
            self.name == "CreateWindowExW"
            and getattr(self, "argtypes", None) is None
            and int(args[10]) > 0xFFFFFFFF
        ):
            raise OverflowError("int too long to convert")
        return self.result


class _FakeWinUser32:
    def __init__(self) -> None:
        self.RegisterClassW = _FakeWinFunc("RegisterClassW", 1)
        self.CreateWindowExW = _FakeWinFunc("CreateWindowExW", 0xABCDEF)
        self.RegisterRawInputDevices = _FakeWinFunc("RegisterRawInputDevices", 1)
        self.GetRawInputData = _FakeWinFunc("GetRawInputData", 0)
        self.DefWindowProcW = _FakeWinFunc("DefWindowProcW", 0)
        self.DestroyWindow = _FakeWinFunc("DestroyWindow", 1)
        self.UnregisterClassW = _FakeWinFunc("UnregisterClassW", 1)
        self.PostThreadMessageW = _FakeWinFunc("PostThreadMessageW", 1)
        self.PostQuitMessage = _FakeWinFunc("PostQuitMessage", None)
        self.PeekMessageW = _FakeWinFunc("PeekMessageW", 0)
        self.TranslateMessage = _FakeWinFunc("TranslateMessage", 1)
        self.DispatchMessageW = _FakeWinFunc("DispatchMessageW", 0)


class _FakeWinKernel32:
    def __init__(self, hinstance: int) -> None:
        self.GetModuleHandleW = _FakeWinFunc("GetModuleHandleW", hinstance)
        self.GetCurrentThreadId = _FakeWinFunc("GetCurrentThreadId", 1234)


def test_non_windows_start_is_noop() -> None:
    calls: list[tuple[int, int, int]] = []
    capture = ric.RawInputCapture(
        lambda dx, dy, ts: calls.append((dx, dy, ts)),
        platform_name="posix",
    )

    assert capture.start() is False
    assert capture.thread is None
    assert capture.tier == "none"
    assert capture.wm_input_total == 0
    assert calls == []


def test_register_and_unregister_raw_input_devices() -> None:
    user32 = _FakeUser32()
    capture = ric.RawInputCapture(lambda _dx, _dy, _ts: None, user32=user32)

    assert capture._register_raw_input(hwnd=12345) is True
    assert capture._unregister_raw_input() is True

    register, unregister = user32.register_calls
    assert register.usUsagePage == 0x01
    assert register.usUsage == 0x02
    assert register.dwFlags == ric.RIDEV_INPUTSINK
    assert int(register.hwndTarget) == 12345

    assert unregister.usUsagePage == 0x01
    assert unregister.usUsage == 0x02
    assert unregister.dwFlags == ric.RIDEV_REMOVE
    assert unregister.hwndTarget is None


def test_win32_prototypes_are_64_bit_handle_safe() -> None:
    user32 = _FakeWinUser32()
    kernel32 = _FakeWinKernel32(hinstance=0x1234567887654321)

    ric.RawInputCapture._configure_prototypes(user32, kernel32)

    assert user32.CreateWindowExW.argtypes == [
        ric.wintypes.DWORD,
        ric.wintypes.LPCWSTR,
        ric.wintypes.LPCWSTR,
        ric.wintypes.DWORD,
        ric.c_int,
        ric.c_int,
        ric.c_int,
        ric.c_int,
        ric._WIN_HWND,
        ric._WIN_HMENU,
        ric._WIN_HINSTANCE,
        ric._WIN_LPVOID,
    ]
    assert user32.CreateWindowExW.restype is ric._WIN_HWND
    assert user32.RegisterRawInputDevices.argtypes == [
        POINTER(ric.RAWINPUTDEVICE),
        c_uint,
        c_uint,
    ]
    assert user32.GetRawInputData.argtypes == [
        ric._WIN_HANDLE,
        ric.wintypes.UINT,
        ric._WIN_LPVOID,
        POINTER(c_uint),
        c_uint,
    ]
    assert user32.DefWindowProcW.argtypes == [
        ric._WIN_HWND,
        ric.wintypes.UINT,
        ric.wintypes.WPARAM,
        ric.wintypes.LPARAM,
    ]
    assert user32.DestroyWindow.argtypes == [ric._WIN_HWND]


def test_registration_path_accepts_64_bit_hinstance() -> None:
    user32 = _FakeWinUser32()
    kernel32 = _FakeWinKernel32(hinstance=0x1234567887654321)
    capture = ric.RawInputCapture(
        lambda _dx, _dy, _ts: None,
        user32=user32,
        kernel32=kernel32,
        platform_name="nt",
    )

    try:
        assert capture.start(timeout=1.0) is True
    finally:
        capture.stop()

    assert capture.last_error == ""
    assert user32.CreateWindowExW.calls[0][10] == 0x1234567887654321
    assert user32.RegisterRawInputDevices.calls


def test_wm_input_relative_mouse_delta_invokes_callback() -> None:
    user32 = _FakeUser32()
    now = [100.0]
    events: list[tuple[int, int, int]] = []
    capture = ric.RawInputCapture(
        lambda dx, dy, ts: events.append((dx, dy, ts)),
        user32=user32,
        clock=lambda: now[0],
    )
    capture._start_time = 99.75

    capture._handle_wm_input(lparam=555)

    assert events == [(13, -7, 250)]
    assert capture.wm_input_total == 1
    assert capture.failures == 0
    assert capture.tier == "rawinput"


def test_wm_input_absolute_mouse_move_is_ignored() -> None:
    user32 = _FakeUser32()
    user32.raw_input.data.mouse.usFlags = ric.MOUSE_MOVE_ABSOLUTE
    events: list[tuple[int, int, int]] = []
    capture = ric.RawInputCapture(lambda dx, dy, ts: events.append((dx, dy, ts)), user32=user32)

    capture._handle_wm_input(lparam=555)

    assert events == []
    assert capture.wm_input_total == 0
    assert capture.tier == "none"


def test_get_raw_input_data_failure_increments_diagnostics() -> None:
    class _FailingUser32(_FakeUser32):
        def GetRawInputData(self, *_args: Any) -> int:
            return 0xFFFFFFFF

    ric.RawInputCapture(lambda _dx, _dy, _ts: None, user32=_FailingUser32())
