"""Windows Raw Input mouse delta capture for the lite recorder.

This module is intentionally a no-op off Windows. On Windows it creates a
hidden window, registers for mouse WM_INPUT events, and forwards device-level
relative deltas without relying on the OS cursor position.
"""

from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
from ctypes import POINTER, Structure, byref, c_int, c_uint, c_void_p, sizeof, wintypes
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

windll = getattr(ctypes, "windll", None)

_WIN_HANDLE = getattr(wintypes, "HANDLE", c_void_p)
_WIN_HWND = getattr(wintypes, "HWND", _WIN_HANDLE)
_WIN_HMENU = getattr(wintypes, "HMENU", _WIN_HANDLE)
_WIN_HBRUSH = getattr(wintypes, "HBRUSH", _WIN_HANDLE)
_WIN_HCURSOR = getattr(wintypes, "HCURSOR", _WIN_HANDLE)
_WIN_HICON = getattr(wintypes, "HICON", _WIN_HANDLE)
_WIN_HINSTANCE = getattr(wintypes, "HINSTANCE", _WIN_HANDLE)
_WIN_HMODULE = getattr(wintypes, "HMODULE", _WIN_HANDLE)
_WIN_ATOM = getattr(wintypes, "ATOM", getattr(wintypes, "WORD", c_uint))
_WIN_BOOL = getattr(wintypes, "BOOL", c_int)
_WIN_LRESULT = getattr(wintypes, "LRESULT", wintypes.LPARAM)
_WIN_LPVOID = getattr(wintypes, "LPVOID", c_void_p)

RIDEV_REMOVE = 0x00000001
RIDEV_INPUTSINK = 0x00000100
RID_INPUT = 0x10000003
RIM_TYPEMOUSE = 0
WM_DESTROY = 0x0002
WM_QUIT = 0x0012
WM_INPUT = 0x00FF
PM_REMOVE = 0x0001
MOUSE_MOVE_RELATIVE = 0
MOUSE_MOVE_ABSOLUTE = 1

_UINT_FAILURE = 0xFFFFFFFF
_WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
WNDPROC = _WINFUNCTYPE(
    _WIN_LRESULT,
    _WIN_HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


class RAWINPUTDEVICE(Structure):
    _fields_ = [
        ("usUsagePage", wintypes.USHORT),
        ("usUsage", wintypes.USHORT),
        ("dwFlags", wintypes.DWORD),
        ("hwndTarget", _WIN_HWND),
    ]


class _RAWMOUSE_BUTTONS_STRUCT(Structure):
    _fields_ = [
        ("usButtonFlags", wintypes.USHORT),
        ("usButtonData", wintypes.USHORT),
    ]


class _RAWMOUSE_BUTTONS(ctypes.Union):
    _fields_ = [
        ("ulButtons", wintypes.ULONG),
        ("buttons", _RAWMOUSE_BUTTONS_STRUCT),
    ]


class RAWMOUSE(Structure):
    _fields_ = [
        ("usFlags", wintypes.USHORT),
        ("_buttons", _RAWMOUSE_BUTTONS),
        ("ulRawButtons", wintypes.ULONG),
        ("lLastX", wintypes.LONG),
        ("lLastY", wintypes.LONG),
        ("ulExtraInformation", wintypes.ULONG),
    ]

    @property
    def ulButtons(self) -> int:
        return int(self._buttons.ulButtons)

    @property
    def usButtonFlags(self) -> int:
        return int(self._buttons.buttons.usButtonFlags)

    @property
    def usButtonData(self) -> int:
        return int(self._buttons.buttons.usButtonData)


class RAWINPUTHEADER(Structure):
    _fields_ = [
        ("dwType", wintypes.DWORD),
        ("dwSize", wintypes.DWORD),
        ("hDevice", _WIN_HANDLE),
        ("wParam", wintypes.WPARAM),
    ]


class RAWINPUT(Structure):
    class _DATA(ctypes.Union):
        _fields_ = [
            ("mouse", RAWMOUSE),
            ("keyboard", ctypes.c_ubyte * 24),
        ]

    _fields_ = [
        ("header", RAWINPUTHEADER),
        ("data", _DATA),
    ]


class WNDCLASS(Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", c_int),
        ("cbWndExtra", c_int),
        ("hInstance", _WIN_HINSTANCE),
        ("hIcon", _WIN_HICON),
        ("hCursor", _WIN_HCURSOR),
        ("hbrBackground", _WIN_HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class RawInputCapture:
    """Capture WM_INPUT relative mouse deltas on Windows.

    ``start()`` returns ``False`` and leaves ``tier == "none"`` on non-Windows
    or when registration fails. Callers should keep their existing absolute
    mouse listeners running and treat this as an additive signal.
    """

    def __init__(
        self,
        on_mouse_delta: Callable[[int, int, int], None],
        *,
        user32: Optional[Any] = None,
        kernel32: Optional[Any] = None,
        clock: Callable[[], float] = time.time,
        platform_name: Optional[str] = None,
    ) -> None:
        self.on_mouse_delta = on_mouse_delta
        self.thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.wm_input_total = 0
        self.failures = 0
        self.tier = "none"

        self._user32_override = user32
        self._kernel32_override = kernel32
        self._clock = clock
        self._platform_name = platform_name or os.name
        self._start_time = 0.0
        self._started_event = threading.Event()
        self._registration_ok = False
        self._thread_id: Optional[int] = None
        self._hwnd: Optional[int] = None
        self._class_name = f"OysterRawInputCapture_{id(self):x}"
        self._wndproc: Optional[Any] = None
        self.last_error = ""

    def start(self, timeout: float = 2.0) -> bool:
        if self._platform_name != "nt":
            self.last_error = "Raw Input is Windows-only"
            return False
        if self._user32() is None or self._kernel32() is None:
            self.last_error = "Win32 user32/kernel32 unavailable"
            return False
        if self.thread is not None and self.thread.is_alive():
            return self._registration_ok

        self.stop_event.clear()
        self._started_event.clear()
        self._registration_ok = False
        self._start_time = self._clock()
        self.thread = threading.Thread(target=self._run, daemon=True, name="RawInputCapture")
        self.thread.start()
        self._started_event.wait(timeout=timeout)
        return self._registration_ok

    def stop(self, timeout: float = 1.0) -> None:
        self.stop_event.set()
        thread_id = self._thread_id
        if thread_id is not None:
            try:
                user32 = self._user32()
                if user32 is not None:
                    user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
            except Exception as e:
                logger.debug("PostThreadMessageW(thread_id=%s) failed: %s", thread_id, e)
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=timeout)

    def _user32(self) -> Optional[Any]:
        if self._user32_override is not None:
            return self._user32_override
        if windll is None:
            return None
        return windll.user32

    def _kernel32(self) -> Optional[Any]:
        if self._kernel32_override is not None:
            return self._kernel32_override
        if windll is None:
            return None
        return windll.kernel32

    def _run(self) -> None:
        user32 = self._user32()
        kernel32 = self._kernel32()
        if user32 is None or kernel32 is None:
            self.last_error = "Win32 user32/kernel32 unavailable"
            self._started_event.set()
            return

        self._configure_prototypes(user32, kernel32)
        hinstance = kernel32.GetModuleHandleW(None)
        try:
            self._thread_id = int(kernel32.GetCurrentThreadId())
        except Exception as e:
            logger.debug("GetCurrentThreadId failed: %s", e)
            self._thread_id = None

        @WNDPROC
        def wndproc(hwnd: int, msg: int, wparam: int, lparam: int) -> int:
            if msg == WM_INPUT:
                self._handle_wm_input(lparam)
                try:
                    return int(user32.DefWindowProcW(hwnd, msg, wparam, lparam))
                except Exception as e:
                    logger.debug("DefWindowProcW failed in wndproc: %s", e)
                    return 0
            if msg == WM_DESTROY:
                try:
                    user32.PostQuitMessage(0)
                except Exception as e:
                    logger.debug("PostQuitMessage failed: %s", e)
                return 0
            try:
                return int(user32.DefWindowProcW(hwnd, msg, wparam, lparam))
            except Exception as e:
                logger.debug("DefWindowProcW failed in outer handler: %s", e)
                return 0

        self._wndproc = wndproc
        wndclass = WNDCLASS()
        wndclass.lpfnWndProc = wndproc
        wndclass.hInstance = hinstance
        wndclass.lpszClassName = self._class_name

        try:
            atom = user32.RegisterClassW(byref(wndclass))
            if not atom:
                self.last_error = f"RegisterClassW failed: {self._last_error(kernel32)}"
                self._started_event.set()
                return

            hwnd = user32.CreateWindowExW(
                0,
                self._class_name,
                self._class_name,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                hinstance,
                None,
            )
            if not hwnd:
                self.last_error = f"CreateWindowExW failed: {self._last_error(kernel32)}"
                self._started_event.set()
                return
            self._hwnd = int(hwnd)

            if not self._register_raw_input(self._hwnd):
                self.last_error = f"RegisterRawInputDevices failed: {self._last_error(kernel32)}"
                self._started_event.set()
                return

            self._registration_ok = True
            self._started_event.set()
            msg = wintypes.MSG()
            while not self.stop_event.is_set():
                while user32.PeekMessageW(byref(msg), 0, 0, 0, PM_REMOVE):
                    if int(msg.message) == WM_QUIT:
                        self.stop_event.set()
                        break
                    user32.TranslateMessage(byref(msg))
                    user32.DispatchMessageW(byref(msg))
                if self.stop_event.is_set():
                    break
                time.sleep(0.005)
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._started_event.set()
        finally:
            try:
                self._unregister_raw_input()
            except Exception as e:
                logger.debug("Unregister raw input devices failed: %s", e)
            try:
                if self._hwnd:
                    user32.DestroyWindow(self._hwnd)
            except Exception as e:
                logger.debug("DestroyWindow(hwnd=%s) failed: %s", self._hwnd, e)
            try:
                user32.UnregisterClassW(self._class_name, hinstance)
            except Exception as e:
                logger.debug("UnregisterClassW(%s) failed: %s", self._class_name, e)
            self._thread_id = None
            self._hwnd = None

    def _register_raw_input(self, hwnd: int) -> bool:
        user32 = self._user32()
        if user32 is None:
            return False
        device = RAWINPUTDEVICE(0x01, 0x02, RIDEV_INPUTSINK, hwnd)
        return bool(user32.RegisterRawInputDevices(byref(device), 1, sizeof(RAWINPUTDEVICE)))

    def _unregister_raw_input(self) -> bool:
        user32 = self._user32()
        if user32 is None:
            return False
        device = RAWINPUTDEVICE(0x01, 0x02, RIDEV_REMOVE, None)
        return bool(user32.RegisterRawInputDevices(byref(device), 1, sizeof(RAWINPUTDEVICE)))

    def _handle_wm_input(self, lparam: int) -> None:
        user32 = self._user32()
        if user32 is None:
            return

        size = c_uint(0)
        result = user32.GetRawInputData(
            lparam,
            RID_INPUT,
            None,
            byref(size),
            sizeof(RAWINPUTHEADER),
        )
        if int(result) == _UINT_FAILURE or size.value <= 0:
            self.failures += 1
            return

        buffer = ctypes.create_string_buffer(size.value)
        result = user32.GetRawInputData(
            lparam,
            RID_INPUT,
            buffer,
            byref(size),
            sizeof(RAWINPUTHEADER),
        )
        if int(result) == _UINT_FAILURE or int(result) != int(size.value):
            self.failures += 1
            return

        raw_input = ctypes.cast(buffer, POINTER(RAWINPUT)).contents
        if int(raw_input.header.dwType) != RIM_TYPEMOUSE:
            return
        mouse = raw_input.data.mouse
        if int(mouse.usFlags) & MOUSE_MOVE_ABSOLUTE:
            return

        timestamp_ms = int((self._clock() - self._start_time) * 1000)
        try:
            self.on_mouse_delta(int(mouse.lLastX), int(mouse.lLastY), timestamp_ms)
        except Exception as e:
            logger.debug("on_mouse_delta failed: %s", e)
            self.failures += 1
            return

        self.wm_input_total += 1
        self.tier = "rawinput"

    @staticmethod
    def _configure_prototypes(user32: Any, kernel32: Any) -> None:
        def _set(func: Any, attr: str, value: Any) -> None:
            try:
                setattr(func, attr, value)
            except Exception as e:
                logger.debug(
                    "Setattr(%s, %s=%r) failed: %s", func, attr, value, e
                )

        _set(kernel32.GetModuleHandleW, "argtypes", [wintypes.LPCWSTR])
        _set(kernel32.GetModuleHandleW, "restype", _WIN_HMODULE)
        _set(kernel32.GetCurrentThreadId, "argtypes", [])
        _set(kernel32.GetCurrentThreadId, "restype", wintypes.DWORD)
        _set(user32.RegisterClassW, "argtypes", [POINTER(WNDCLASS)])
        _set(user32.RegisterClassW, "restype", _WIN_ATOM)
        _set(
            user32.CreateWindowExW,
            "argtypes",
            [
                wintypes.DWORD,
                wintypes.LPCWSTR,
                wintypes.LPCWSTR,
                wintypes.DWORD,
                c_int,
                c_int,
                c_int,
                c_int,
                _WIN_HWND,
                _WIN_HMENU,
                _WIN_HINSTANCE,
                _WIN_LPVOID,
            ],
        )
        _set(user32.CreateWindowExW, "restype", _WIN_HWND)
        _set(user32.RegisterRawInputDevices, "argtypes", [POINTER(RAWINPUTDEVICE), c_uint, c_uint])
        _set(user32.RegisterRawInputDevices, "restype", _WIN_BOOL)
        _set(
            user32.GetRawInputData,
            "argtypes",
            [_WIN_HANDLE, wintypes.UINT, _WIN_LPVOID, POINTER(c_uint), c_uint],
        )
        _set(user32.GetRawInputData, "restype", wintypes.UINT)
        _set(
            user32.DefWindowProcW,
            "argtypes",
            [_WIN_HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM],
        )
        _set(user32.DefWindowProcW, "restype", _WIN_LRESULT)
        _set(user32.DestroyWindow, "argtypes", [_WIN_HWND])
        _set(user32.DestroyWindow, "restype", _WIN_BOOL)
        _set(
            user32.UnregisterClassW,
            "argtypes",
            [wintypes.LPCWSTR, _WIN_HINSTANCE],
        )
        _set(user32.UnregisterClassW, "restype", _WIN_BOOL)
        _set(
            user32.PostThreadMessageW,
            "argtypes",
            [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM],
        )
        _set(user32.PostThreadMessageW, "restype", _WIN_BOOL)
        _set(user32.PostQuitMessage, "argtypes", [c_int])
        _set(user32.PostQuitMessage, "restype", None)
        _set(
            user32.PeekMessageW,
            "argtypes",
            [POINTER(wintypes.MSG), _WIN_HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT],
        )
        _set(user32.PeekMessageW, "restype", _WIN_BOOL)
        _set(user32.TranslateMessage, "argtypes", [POINTER(wintypes.MSG)])
        _set(user32.TranslateMessage, "restype", _WIN_BOOL)
        _set(user32.DispatchMessageW, "argtypes", [POINTER(wintypes.MSG)])
        _set(user32.DispatchMessageW, "restype", _WIN_LRESULT)
