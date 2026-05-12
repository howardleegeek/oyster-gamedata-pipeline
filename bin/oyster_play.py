#!/usr/bin/env python3
"""R05C · bin/oyster_play.py — single-button consumer launcher.

Replaces today's temporary ``OysterPlay.py`` orchestrator. This module
is what gets compiled to ``OysterPlay.exe`` via the bundled PyInstaller
build script (``bin/build_bundled_installer/build_oysterplay_exe.py``).

User flow (cf. spec R05C):
    1.  Verify install integrity (JRE + Fabric profile + client jar).
    2.  Spawn the recorder (``OysterRecorder.exe``) if not already running.
    3.  Build javaw cmd from LEAF Fabric JSON only (bug-fix #1).
    4.  ``subprocess.Popen`` javaw with stderr → file + tail (bug-fix #3).
    5.  Wait up to 30 s for MC ready signal in ``latest.log`` (bug-fix #5).
    6.  UIA-click the recorder's "▶ 开始录制" button (bug-fix #4).
    7.  Wait for javaw exit, then disarm recorder + show summary.

Cross-platform: this module imports cleanly on macOS for testing. The
Windows-only paths (registry Desktop, MessageBox, UIA, Popen of
``OysterRecorder.exe``) are gated by ``os.name == 'nt'`` checks.
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------
# Imports from the sibling launcher module. We support two import paths:
#   - normal:    `import oyster_launch_mc as launcher`
#   - PyInstaller / package layout: `from . import oyster_launch_mc`
# --------------------------------------------------------------------------

try:
    # When PyInstaller bundles us, both modules end up siblings under the
    # frozen archive's `bin/`. The relative import works because we add
    # bin/ to sys.path in main().
    import oyster_launch_mc as launcher  # type: ignore[import-not-found]
except ImportError:
    # When run as a script from the repo, bin/ may not be on sys.path
    # yet — patch it ourselves.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import oyster_launch_mc as launcher  # type: ignore[no-redef]

try:
    from _i18n import _t  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover — defensive: dialog text never crashes import
    def _t(en: str, zh: str) -> str:  # type: ignore[no-redef]
        return en


# --------------------------------------------------------------------------
# rc17.4-form — operator metadata form (first-launch + per-session)
# --------------------------------------------------------------------------

try:
    from oyster_launcher_form import (  # type: ignore[import-not-found]
        apply_config_to_env,
        config_exists,
        load_config,
        prompt_route_type,
        save_config,
        show_first_launch_form,
    )
except ImportError:
    # When run from repo, bin/ may not be on sys.path yet.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from oyster_launcher_form import (  # type: ignore[no-redef]
        apply_config_to_env,
        config_exists,
        load_config,
        prompt_route_type,
        save_config,
        show_first_launch_form,
    )

logger = logging.getLogger("oyster_play")


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


RECORDER_EXE_NAME = "OysterRecorder.exe"
# Bug 5 (R05E): when PyInstaller --onedir is invoked with
# `--name OysterRecorder-onedir`, the produced binary is named
# `OysterRecorder-onedir.exe` and lives in a sibling subdir of the same
# name. installer.iss ships that subdir verbatim under {app}, so the
# launcher must know to look for both names + both layouts.
RECORDER_ONEDIR_DIR = "OysterRecorder-onedir"
RECORDER_ONEDIR_EXE = "OysterRecorder-onedir.exe"
RECORDER_BUTTON_LABEL = "▶ 开始录制"
RECORDER_DISARM_LABEL = "⏹ 停止录制"
RECORDER_WINDOW_CLASS = "TkTopLevel"  # Tkinter top-level windows
DEFAULT_READY_TIMEOUT_SEC = 30.0


# --------------------------------------------------------------------------
# Recorder process management
# --------------------------------------------------------------------------


def find_recorder_exe(install_root_path: Path) -> Path | None:
    """Locate the recorder executable next to OysterPlay.exe.

    rc16 (Howard 2026-05-11): Rust+OBS recorder is now the PRIMARY path.
    Python recorder (OysterRecorder-onedir/) is the FALLBACK, opt-in via
    ``OYSTER_PY_RECORDER=1``.

    Looks in standard install layouts, in priority order:

        1. <INSTALL_ROOT>/recorder/OysterRecorder.exe
           — rc16 PRIMARY: Rust+OBS recorder, shipped by
           build-recorder-rust.yml + staged by installer.iss section (3b).
        2. <INSTALL_ROOT>/OysterRecorder-onedir/OysterRecorder-onedir.exe
           — FALLBACK: PyInstaller --onedir Python recorder (R05E layout).
        3. <INSTALL_ROOT>/OysterRecorder.exe
           — legacy / hand-installed layout.
        4. sibling of the running executable (when run from a
           PyInstaller bundle — OysterPlay.exe is dropped at {app}),
           tried under each of the layouts above.

    When ``OYSTER_PY_RECORDER=1`` is set, the Rust paths (1 + sibling-of-1)
    are skipped entirely and the Python recorder paths are tried directly.
    This lets users fall back without reinstalling when the Rust path
    misbehaves on their rig.
    """
    # rc16.1: env-gate — empty/unset (default) means Rust-first.
    # Accepts standard truthy values ("1", "true", "yes", "on") case-
    # insensitively so users following typical env-var convention aren't
    # silently ignored. Anything else is treated as default (Rust-first).
    _py_only_raw = os.environ.get("OYSTER_PY_RECORDER", "").strip().lower()
    py_only = _py_only_raw in {"1", "true", "yes", "on"}

    candidates: list[Path] = []
    if not py_only:
        # rc16 PRIMARY: Rust+OBS recorder lives under {app}/recorder/.
        candidates.append(install_root_path / "recorder" / RECORDER_EXE_NAME)
    # FALLBACK chain: Python onedir, then legacy single-file.
    candidates.append(install_root_path / RECORDER_ONEDIR_DIR / RECORDER_ONEDIR_EXE)
    candidates.append(install_root_path / RECORDER_EXE_NAME)

    # When bundled, sys.executable is OysterPlay.exe — recorder may be
    # in the same dir under either layout. Same priority order applies.
    exe = Path(sys.executable).resolve()
    if not py_only:
        candidates.append(exe.parent / "recorder" / RECORDER_EXE_NAME)
    candidates.append(exe.parent / RECORDER_ONEDIR_DIR / RECORDER_ONEDIR_EXE)
    candidates.append(exe.parent / RECORDER_EXE_NAME)

    for c in candidates:
        if c.is_file():
            return c
    return None


def is_recorder_running() -> bool:
    """Return True if a recorder process is alive (either exe naming).

    Bug 5 (R05E): the actual shipped binary is
    ``OysterRecorder-onedir.exe``, but legacy installs may still have
    ``OysterRecorder.exe``. Match both.

    Windows-only via ``tasklist``. On non-Windows we always return False
    (testing path).
    """
    if os.name != "nt":
        return False

    for image_name in (RECORDER_ONEDIR_EXE, RECORDER_EXE_NAME):
        try:
            out = subprocess.check_output(
                ["tasklist", "/FI", f"IMAGENAME eq {image_name}",
                 "/FO", "CSV", "/NH"],
                encoding="utf-8", errors="replace", timeout=10,
            )
        except (subprocess.SubprocessError, OSError) as e:
            logger.warning(
                "tasklist failed for %s: %s — continuing", image_name, e,
            )
            continue
        if image_name.lower() in out.lower():
            return True
    return False


def spawn_recorder(recorder_exe: Path) -> subprocess.Popen | None:
    """Start ``OysterRecorder.exe`` if not already running. Returns the
    Popen handle (or None when no spawn was needed / non-Windows)."""
    if os.name != "nt":
        logger.info("non-Windows — skipping recorder spawn (test mode)")
        return None
    if is_recorder_running():
        logger.info("recorder already running — skipping spawn")
        return None
    logger.info("spawning recorder: %s", recorder_exe)
    # rc15.27 C-#1 (round 17): on a PyInstaller --noconsole parent, the
    # recorder's stdout/stderr inherit a detached console handle which
    # Windows may keep alive as a zombie object. Add CREATE_NO_WINDOW
    # (0x08000000) and route stdio to DEVNULL.
    # rc17.0.5: scope GameHook forcing to known OpenGL games. Stream O added
    # OPENGL_PROCESSES detector in env_sanity.py — reuse same list.
    # rc17.0.4 (superseded): forced game-mode unconditionally, which broke
    # DirectX titles where DXGI Monitor is faster than GameHook and where
    # injection-detection can trigger anti-cheat bans. We now require the
    # caller's OYSTER_TARGET_PROCESS (defaults to javaw.exe for MC) to be in
    # a known-good OpenGL set before forcing game mode.
    OPENGL_PROCESSES_FOR_GAMEHOOK = frozenset({
        "javaw.exe",      # Minecraft Java
        "java.exe",       # Minecraft Java (older)
        "factorio.exe",
        "kerbal.exe",
        "ksp.exe",
        "ksp_x64.exe",
        "minetest.exe",
        "0ad.exe",
        "supertuxkart.exe",
    })

    env = os.environ.copy()
    target_process = os.environ.get("OYSTER_TARGET_PROCESS", "javaw.exe").lower()
    if (
        "OYSTER_CAPTURE_MODE" not in env
        and target_process in OPENGL_PROCESSES_FOR_GAMEHOOK
    ):
        env["OYSTER_CAPTURE_MODE"] = "game"
        logger.info(
            "rc17.0.5: OYSTER_CAPTURE_MODE=game forced for OpenGL target '%s' "
            "(skips DXGI 1Hz throttle on AMD/NVIDIA iGPU rigs)",
            target_process,
        )
    elif "OYSTER_CAPTURE_MODE" not in env:
        logger.debug(
            "rc17.0.5: target '%s' not in OpenGL list, leaving capture mode default",
            target_process,
        )
    return subprocess.Popen(
        [str(recorder_exe)],
        cwd=str(recorder_exe.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
        creationflags=0x00000200 | 0x08000000,  # CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    )


# rc16.1c (grill-me revert): the runtime fallback functions
# `_is_rust_recorder` + `spawn_recorder_with_fallback` were removed after
# the second grill found 4 unfixable problems for tonight's ship:
#   1. 60s blocking proc.wait() froze the launcher main thread on the
#      HAPPY PATH — UX regression on every session, even when Rust worked
#   2. "process alive == working" is the wrong signal — Rust can be
#      alive-but-hung in libobs init waiting on an unavailable D3D11
#      device, and a watchdog timer can't detect that
#   3. 95 lines of new code with zero Windows runtime execution
#   4. The parent pivot (Rust+OBS works on AMD 780M) is itself
#      unvalidated — adding fallback code without empirical evidence
#      from the tester is premature optimization
#
# Path forward: ship rc16.0-shape (file-presence-only fallback). For the
# scenario where Rust ships but crashes at startup, the manual recovery
# is `OYSTER_PY_RECORDER=1` documented in TESTER_NOTES — one line of
# instruction beats 95 lines of untested watchdog code for a single
# tester rig. When we have data showing Rust crashes on real users,
# rc17 can revisit with a properly threaded + log-driven watchdog.


# --------------------------------------------------------------------------
# Recorder window detection (Bug-fix #4)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RecorderWindow:
    """Captured info about a recorder Tk window we can drive via UIA."""
    hwnd: int
    title: str
    class_name: str
    pid: int


def _enum_windows() -> list[tuple[int, str, str, int]]:
    """Return ``[(hwnd, title, class_name, pid), ...]`` for ALL top-level
    windows. Returns empty on non-Windows."""
    if os.name != "nt":
        return []

    import ctypes
    import ctypes.wintypes as wt

    user32 = ctypes.windll.user32

    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    GetWindowText = user32.GetWindowTextW
    GetWindowTextLength = user32.GetWindowTextLengthW
    GetClassName = user32.GetClassNameW
    IsWindowVisible = user32.IsWindowVisible
    GetWindowThreadProcessId = user32.GetWindowThreadProcessId

    results: list[tuple[int, str, str, int]] = []

    def _cb(hwnd: int, _lparam: int) -> bool:
        # NOTE: we do NOT skip iconified — bug-fix #4 says
        # MainWindowHandle is zero when iconified, so we must walk all
        # top-level windows including hidden Tk windows.
        if not IsWindowVisible(hwnd):
            return True
        ln = GetWindowTextLength(hwnd)
        title_buf = ctypes.create_unicode_buffer(ln + 1) if ln else ctypes.create_unicode_buffer(1)
        if ln:
            GetWindowText(hwnd, title_buf, ln + 1)
        title = title_buf.value
        cls_buf = ctypes.create_unicode_buffer(256)
        GetClassName(hwnd, cls_buf, 256)
        class_name = cls_buf.value
        pid = wt.DWORD()
        GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        results.append((int(hwnd), title, class_name, int(pid.value)))
        return True

    user32.EnumWindows(EnumWindowsProc(_cb), 0)
    return results


def find_recorder_window(
    *,
    button_label: str = RECORDER_BUTTON_LABEL,
    class_filter: str = RECORDER_WINDOW_CLASS,
) -> RecorderWindow | None:
    """Return the recorder window or None.

    Bug-fix #4 strategy:
        1.  EnumWindows → find candidates with class name "TkTopLevel".
        2.  For each candidate, drive UIA to check whether it has a
            descendant Button with name == "▶ 开始录制".
        3.  Return the first match.

    Class-name filter alone is enough most of the time (Tk apps with
    that exact class name are rare). The UIA button check is only used
    when there are multiple Tk windows.
    """
    if os.name != "nt":
        return None

    candidates = [w for w in _enum_windows() if w[2] == class_filter]
    if not candidates:
        # Fallback: any window whose title contains "Oyster" — handles
        # case where Tk class name differs across Python versions.
        candidates = [w for w in _enum_windows() if "oyster" in w[1].lower()]

    if not candidates:
        return None

    if len(candidates) == 1:
        hwnd, title, cls, pid = candidates[0]
        return RecorderWindow(hwnd=hwnd, title=title, class_name=cls, pid=pid)

    # Multiple candidates — disambiguate via UIA button label.
    for hwnd, title, cls, pid in candidates:
        if _window_has_button(hwnd, button_label):
            return RecorderWindow(hwnd=hwnd, title=title, class_name=cls, pid=pid)

    # No UIA match either — return first as best-effort.
    hwnd, title, cls, pid = candidates[0]
    return RecorderWindow(hwnd=hwnd, title=title, class_name=cls, pid=pid)


def _window_has_button(hwnd: int, button_label: str) -> bool:
    """Use UIAutomation to check if the HWND has a button with this name."""
    if os.name != "nt":
        return False
    try:
        # comtypes is the standard Python UIA binding. We prefer it to
        # be optional — when absent we fall back to True (assume hit).
        from comtypes.client import CreateObject  # type: ignore[import-not-found]
        from comtypes.gen import UIAutomationClient as UIA  # type: ignore[import-not-found]
    except ImportError:
        logger.debug("comtypes not available; skipping UIA button check")
        return True

    try:
        uia = CreateObject(UIA.CUIAutomation)
        root = uia.ElementFromHandle(hwnd)
        cond = uia.CreatePropertyCondition(UIA.UIA_NamePropertyId, button_label)
        elt = root.FindFirst(UIA.TreeScope_Descendants, cond)
        return elt is not None
    except Exception as e:  # noqa: BLE001 — UIA is finicky, fail-soft
        logger.debug("UIA probe failed: %s", e)
        return False


def click_recorder_button(
    win: RecorderWindow,
    *,
    button_label: str = RECORDER_BUTTON_LABEL,
) -> bool:
    """Click the named button via UIA InvokePattern. Returns True on success."""
    if os.name != "nt":
        return False
    try:
        from comtypes.client import CreateObject  # type: ignore[import-not-found]
        from comtypes.gen import UIAutomationClient as UIA  # type: ignore[import-not-found]
    except ImportError:
        logger.warning("comtypes missing — cannot UIA-click recorder button")
        return False

    try:
        uia = CreateObject(UIA.CUIAutomation)
        root = uia.ElementFromHandle(win.hwnd)
        cond = uia.CreatePropertyCondition(UIA.UIA_NamePropertyId, button_label)
        elt = root.FindFirst(UIA.TreeScope_Descendants, cond)
        if elt is None:
            logger.warning("UIA: button '%s' not found in hwnd %s",
                           button_label, win.hwnd)
            return False
        invoke = elt.GetCurrentPattern(UIA.UIA_InvokePatternId)
        if invoke is None:
            logger.warning("UIA: button '%s' has no InvokePattern",
                           button_label)
            return False
        invoke.QueryInterface(UIA.IUIAutomationInvokePattern).Invoke()
        return True
    except Exception as e:  # noqa: BLE001
        logger.error("UIA click failed: %s", e)
        return False


def wait_for_recorder_window(
    *,
    timeout_sec: float = 20.0,
    poll_interval_sec: float = 0.5,
) -> RecorderWindow | None:
    """Spin until the recorder Tk window shows up. Returns None on timeout."""
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        win = find_recorder_window()
        if win is not None:
            return win
        time.sleep(poll_interval_sec)
    return None


# --------------------------------------------------------------------------
# Desktop shortcut (registry-resolved Desktop path)
# --------------------------------------------------------------------------


def write_desktop_shortcut(
    *,
    target_exe: Path,
    shortcut_name: str = "Oyster Recording",
) -> Path | None:
    """Create a Windows .lnk on the user's REAL Desktop (registry-resolved).

    Bug-fix #2: we use ``launcher.get_desktop_path()`` which goes through
    the registry / SHGetKnownFolderPath instead of trusting
    ``%USERPROFILE%\\Desktop``.
    """
    if os.name != "nt":
        logger.info("non-Windows — skipping desktop shortcut creation")
        return None

    desktop = launcher.get_desktop_path()
    desktop.mkdir(parents=True, exist_ok=True)
    lnk_path = desktop / f"{shortcut_name}.lnk"

    try:
        # Use Windows Script Host COM (no extra deps).
        import pythoncom  # type: ignore[import-not-found]
        from win32com.client import Dispatch  # type: ignore[import-not-found]
    except ImportError:
        logger.warning(
            "pywin32 not available — falling back to PowerShell shortcut creation"
        )
        # rc15.27 C-#3 (round 17): escape PowerShell single-quotes ('').
        # Was: paths with apostrophes (`O'Brian` desktop, OneDrive
        # `User's Files`) broke PS quoting and produced a parse error
        # rather than a working shortcut. PS escapes ' as ''.
        _safe_lnk = str(lnk_path).replace("'", "''")
        _safe_exe = str(target_exe).replace("'", "''")
        _safe_dir = str(target_exe.parent).replace("'", "''")
        ps = (
            f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{_safe_lnk}'); "
            f"$s.TargetPath='{_safe_exe}'; "
            f"$s.WorkingDirectory='{_safe_dir}'; "
            f"$s.Save()"
        )
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                check=True, capture_output=True, timeout=15,
            )
            return lnk_path
        except (subprocess.SubprocessError, OSError) as e:
            logger.error("PowerShell shortcut fallback failed: %s", e)
            return None

    pythoncom.CoInitialize()
    try:
        shell = Dispatch("WScript.Shell")
        shortcut = shell.CreateShortCut(str(lnk_path))
        shortcut.Targetpath = str(target_exe)
        shortcut.WorkingDirectory = str(target_exe.parent)
        shortcut.IconLocation = str(target_exe)
        shortcut.save()
    finally:
        pythoncom.CoUninitialize()
    return lnk_path


# --------------------------------------------------------------------------
# Main flow
# --------------------------------------------------------------------------


@dataclass
class PlaySession:
    """Tracks the running session state. Fields populated step-by-step."""
    install_root: Path
    plan: launcher.LaunchPlan | None = None
    recorder_proc: subprocess.Popen | None = None
    javaw_proc: subprocess.Popen | None = None
    recorder_window: RecorderWindow | None = None
    armed: bool = False
    failure_reason: str = ""


_MEM_HOGS = {
    "MuMuPlayer.exe", "MuMuPlayer-12.0-base.exe", "MuMuVMMHeadless.exe",
    "Bluestacks.exe", "HD-Player.exe", "BlueStacks.exe",
    "LDPlayer.exe", "ldplayer.exe",
    "NoxVM.exe", "Nox.exe",
    "MEmu.exe", "MEmuConsole.exe",
}


def _detect_memory_hogs_and_resize_heap(default_xmx: str = "4G") -> tuple[str, list[str]]:
    """rc15.12 (Howard 2026-05-10 'bingd 不会关 MuMu'): auto-detect Android
    emulators + dynamic-resize JVM heap based on actual available RAM.

    Returns: (xmx_string, list_of_detected_hog_process_names)
    """
    if os.name != "nt":
        return default_xmx, []
    detected_hogs: list[str] = []
    avail_gb = 999.0
    try:
        import ctypes  # noqa: PLC0415
        import subprocess as _sp  # noqa: PLC0415

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullExtended", ctypes.c_ulonglong),
            ]

        m = MEMORYSTATUSEX()
        m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        # rc15.15 H6: GlobalMemoryStatusEx returns BOOL — 0 on failure.
        # Without this check, m.ullAvailPhys is uninitialized → avail_gb=0
        # → target_gb=1G → MC OOMs at startup with a useless heap.
        ok = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        if not ok:
            # Treat unknown RAM as "fine, don't downsize"; emulator detection
            # still runs below to surface hogs in detected_hogs list.
            avail_gb = 999.0
        else:
            avail_gb = m.ullAvailPhys / (1024 ** 3)

        # tasklist for hog detection (utf-8 + errors=replace per rc15.3)
        try:
            out = _sp.check_output(
                ["tasklist", "/FO", "CSV", "/NH"],
                stderr=_sp.DEVNULL, text=True,
                encoding="utf-8", errors="replace",
                timeout=5, creationflags=0x08000000,
            )
            import csv as _csv  # noqa: PLC0415
            for line in out.splitlines():
                if not line.strip():
                    continue
                try:
                    parts = next(_csv.reader([line]))
                    if len(parts) >= 1:
                        image = parts[0]
                        if image in _MEM_HOGS:
                            detected_hogs.append(image)
                except Exception:
                    continue
        except Exception:
            pass
    except Exception:
        return default_xmx, []

    # Dynamic resize: target heap = min(default, avail * 0.6) clamped to [1G, default]
    try:
        default_gb = int(default_xmx.upper().rstrip("G"))
    except Exception:
        default_gb = 4
    target_gb = max(1, min(default_gb, int(avail_gb * 0.6)))
    if target_gb < default_gb:
        return f"{target_gb}G", detected_hogs
    return default_xmx, detected_hogs


# rc16.8 input-watchdog: real event_type strings emitted by the Rust
# recorder (see vendor/recorder/src/output_types/mod.rs::InputEventType).
# Lifecycle events (START/END/VIDEO_*/HOOK_START/UNFOCUS/FOCUS) excluded —
# they fire regardless of whether input capture works.
_REAL_INPUT_EVENT_TAGS: tuple[str, ...] = (
    '"event_type":"KEYBOARD"', '"event_type":"MOUSE_MOVE"',
    '"event_type":"MOUSE_BUTTON"', '"event_type":"SCROLL"',
    '"event_type":"GAMEPAD_BUTTON"', '"event_type":"GAMEPAD_BUTTON_VALUE"',
    '"event_type":"GAMEPAD_AXIS"',
)


def _input_watchdog(deadline_seconds: float = 60.0) -> None:
    """rc16.8: 60s after recording starts, scan the most recent inputs.jsonl
    under %LOCALAPPDATA%/GameData Recorder/recordings/<session>/ for real
    KEYBOARD/MOUSE/SCROLL/GAMEPAD events. <3 → loud log + Tk popup pointing
    at OYSTER_PY_RECORDER=1 fallback. Best-effort; never raises."""
    time.sleep(deadline_seconds)
    try:
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        root = Path(local) / "GameData Recorder" / "recordings"
        inputs_path: Path | None = None
        if root.is_dir():
            cands = sorted(root.glob("*/inputs.jsonl"),
                           key=lambda p: p.stat().st_mtime, reverse=True)
            inputs_path = cands[0] if cands else None
        if inputs_path is None or not inputs_path.is_file():
            logger.warning("rc16.8 input-watchdog: no inputs.jsonl found "
                           "%.0fs after start", deadline_seconds)
            return
        with inputs_path.open("r", encoding="utf-8", errors="replace") as f:
            real_events = sum(
                1 for line in f
                if any(tag in line for tag in _REAL_INPUT_EVENT_TAGS)
            )
        if real_events >= 3:
            logger.info("rc16.8 input-watchdog: %d events in %.0fs — healthy",
                        real_events, deadline_seconds)
            return
        logger.error(
            "rc16.8 INPUT WATCHDOG: only %d input events in %ss — input "
            "capture may be broken. Set OYSTER_PY_RECORDER=1 to switch to "
            "Python recorder fallback.", real_events, deadline_seconds,
        )
        try:
            import tkinter as tk  # noqa: PLC0415
            from tkinter import messagebox  # noqa: PLC0415
            r = tk.Tk(); r.withdraw()
            messagebox.showwarning(
                _t(
                    "Oyster Recorder - Input Capture Issue",
                    "Oyster Recorder - 输入捕获异常",
                ),
                _t(
                    f"Only {real_events} keyboard/mouse events were captured in "
                    f"the first {int(deadline_seconds)}s. The recording may be "
                    f"missing your inputs.\n\nTo fix: close this app, then run "
                    f"`set OYSTER_PY_RECORDER=1` before relaunching.",
                    f"前 {int(deadline_seconds)} 秒内只捕获到 {real_events} 个键盘/"
                    f"鼠标事件。录制可能缺失您的输入操作。\n\n"
                    f"修复方法: 关闭本应用,然后运行 "
                    f"`set OYSTER_PY_RECORDER=1` 后重新启动。",
                ),
            )
            r.destroy()
        except Exception:
            logger.debug("rc16.8 input-watchdog: Tk popup failed", exc_info=True)
    except Exception as e:  # noqa: BLE001 — watchdog must never crash launcher
        logger.warning("rc16.8 input-watchdog: check failed (%s)", e)


def _find_latest_session_dir() -> Path | None:
    """rc16.11: locate the most-recently-modified recordings/<session>/
    under %LOCALAPPDATA%/GameData Recorder/. Mirrors the discovery
    pattern in _input_watchdog so the diag uploader operates on the
    same dir the lint pipeline produced. Best-effort; returns None on
    any error (caller skips upload offer)."""
    try:
        local = os.environ.get("LOCALAPPDATA") or str(
            Path.home() / "AppData" / "Local"
        )
        root = Path(local) / "GameData Recorder" / "recordings"
        if not root.is_dir():
            return None
        # Pick the most-recently-modified child dir. Freshly aborted
        # recordings may not have inputs.jsonl yet, so we don't filter
        # on file presence here — the diag bundler tolerates sparse
        # session dirs.
        candidates: list[Path] = [c for c in root.iterdir() if c.is_dir()]
        if not candidates:
            return None
        return max(candidates, key=lambda p: p.stat().st_mtime)
    except Exception as e:  # noqa: BLE001 — discovery must never raise
        logger.warning("rc16.11 diag-uploader: session-dir lookup failed (%s)", e)
        return None


def _spawn_diag_uploader_thread() -> None:
    """rc16.11: launch run_layer4 in a daemon thread so the launcher
    main thread can return without blocking on a Tk dialog. Privacy
    invariant: auto_confirm=False is passed unconditionally so the
    user MUST click Send. If the launcher exits before the user
    responds, the daemon thread dies with the process — and no
    upload happens. This is the intended, privacy-safe failure mode.

    Importable diag_uploader is best-effort: if the module is missing
    (e.g. dev checkout without the banked file, or PyInstaller bundle
    that forgot --hidden-import), we log warning and continue. Never
    raises into the launcher main thread."""
    session_dir = _find_latest_session_dir()
    if session_dir is None:
        logger.info("rc16.11 diag-uploader: no session dir found, skipping")
        return

    # Read lint report if it exists; otherwise pass None and let
    # run_layer4 decide (treats None as "lint didn't run, err on
    # the side of offering upload"). Read on the launcher thread
    # because it's small + synchronous; the heavy work (ZIP + Tk
    # + HTTP) happens in the daemon thread below.
    lint_report: dict | None = None
    lint_json_path = session_dir / "lint_v3_report.json"
    if lint_json_path.is_file():
        try:
            import json  # noqa: PLC0415
            lint_report = json.loads(lint_json_path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001 — bad JSON => treat as missing
            logger.warning("rc16.11 diag-uploader: lint JSON parse failed (%s)", e)
            lint_report = None

    def _diag_worker() -> None:
        try:
            from diag_uploader import run_layer4  # noqa: PLC0415
            # auto_confirm=False is REQUIRED by the privacy contract:
            # no silent upload, user must click Send. Do NOT change.
            bundle = run_layer4(
                session_dir, lint_report=lint_report, auto_confirm=False,
            )
            if bundle is not None and bundle.upload_url:
                logger.info(
                    "rc16.11 diag-uploader: uploaded to %s", bundle.upload_url,
                )
            elif bundle is not None:
                logger.info(
                    "rc16.11 diag-uploader: bundle saved locally at %s",
                    bundle.zip_path,
                )
            else:
                logger.info(
                    "rc16.11 diag-uploader: nothing to upload (clean session)",
                )
        except ImportError as e:
            # diag_uploader.py missing from this build — log + continue.
            # Production CI must --add-data + --hidden-import it (see
            # build_oysterplay_exe.py rc16.11 block).
            logger.warning("rc16.11 diag-uploader: module not importable (%s)", e)
        except Exception as e:  # noqa: BLE001 — must never crash launcher
            logger.warning("rc16.11 diag-uploader: worker failed (%s)", e)

    # daemon=True so the launcher process exit kills this thread cleanly
    # without leaking a Tk root window. NEVER join this thread on the
    # main path — the launcher must be free to return immediately. The
    # privacy invariant ("no upload unless user clicks Send") is
    # preserved by run_layer4's dialog being modal: if the launcher
    # exits before the user responds, the dialog dies, no upload.
    threading.Thread(
        target=_diag_worker, daemon=True, name="oyster-diag-uploader",
    ).start()


def run_session(
    *,
    install_root_path: Path,
    username: str = "Player",
    java_xmx: str = "4G",
    profile_name: str = launcher.FABRIC_PROFILE_NAME,
    ready_timeout_sec: float = DEFAULT_READY_TIMEOUT_SEC,
    dry_run: bool = False,
) -> PlaySession:
    """Drive the full single-button flow. Returns the session object."""
    sess = PlaySession(install_root=install_root_path)

    # rc15.12: detect memory hogs + auto-resize heap so JVM doesn't OOM at
    # startup if user has MuMu / Bluestacks / etc running. Tester bingd
    # was on 4GB phys system with MuMu eating 1GB → MC crash.
    resized_xmx, hogs = _detect_memory_hogs_and_resize_heap(java_xmx)
    if hogs:
        # Best-effort offer to kill hogs (auto-yes for now since tester
        # 'doesn't know how to'). Future rc15.13 should add user prompt.
        for hog_image in hogs:
            try:
                import subprocess as _sp  # noqa: PLC0415
                _sp.run(
                    ["taskkill", "/F", "/IM", hog_image],
                    creationflags=0x08000000,
                    capture_output=True, timeout=5,
                )
            except Exception as exc:
                # rc15.27 C-#2 (round 17): was silent. If taskkill fails
                # (Access Denied for elevated MuMu, process gone, etc.)
                # we silently downsized heap but left hog running → MC
                # OOMs anyway and tester sees no clue.
                logger.warning("taskkill %s failed: %s", hog_image, exc)
    if resized_xmx != java_xmx:
        # Heap was downsized due to low avail RAM.
        java_xmx = resized_xmx

    # Step 1: install integrity check
    status = launcher.verify_install(install_root_path, profile_name)
    if not status.ok:
        sess.failure_reason = _t(
            "Install incomplete. Please reinstall.\nMissing:\n  " +
            "\n  ".join(status.missing),
            "安装不完整,请重新安装。\n缺少文件:\n  " +
            "\n  ".join(status.missing),
        )
        if not dry_run:
            launcher._show_messagebox(  # noqa: SLF001 — internal helper
                _t(
                    "Oyster Recorder — install corrupted",
                    "Oyster Recorder — 安装损坏",
                ),
                sess.failure_reason,
            )
        return sess

    # Step 2: build javaw cmd line
    try:
        plan = launcher.build_launch_plan(
            install_root_path=install_root_path,
            username=username,
            java_xmx=java_xmx,
            profile_name=profile_name,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        sess.failure_reason = _t(
            f"Could not build launch command: {e}",
            f"无法构建启动命令: {e}",
        )
        if not dry_run:
            launcher._show_messagebox(  # noqa: SLF001
                _t(
                    "Oyster Recorder — launch failed",
                    "Oyster Recorder — 启动失败",
                ),
                sess.failure_reason,
            )
        return sess
    sess.plan = plan

    if dry_run:
        return sess

    # Step 3: ensure recorder is up.
    #
    # rc16.1c (grill-me revert): the runtime watchdog from rc16.1/.1a was
    # reverted after the second grill found 4 fatal problems:
    #   - 60s blocking wait on main thread = 60s frozen launcher on the
    #     HAPPY PATH (when Rust works fine) — a UX regression on the
    #     common case to handle a hypothetical failure mode
    #   - "process alive == working" is wrong signal — Rust can be
    #     alive-but-hung in libobs init waiting on D3D11 device
    #   - 95 lines of new code with zero Windows execution
    #   - parent pivot (Rust+OBS works on AMD 780M) is unvalidated, so
    #     adding fallback code without that data is premature
    # Falls back to the simpler rc16.0 shape: file-presence-based
    # selection only. If Rust EXE exists, use Rust. If user reports
    # Rust crashes at startup, manual fallback is OYSTER_PY_RECORDER=1
    # documented in TESTER_NOTES.md.
    recorder = find_recorder_exe(install_root_path)
    if recorder is None:
        sess.failure_reason = _t(
            f"OysterRecorder.exe not found near {install_root_path}. "
            "Please reinstall.",
            f"在 {install_root_path} 附近未找到 OysterRecorder.exe。"
            "请重新安装。",
        )
        launcher._show_messagebox(  # noqa: SLF001
            _t(
                "Oyster Recorder — recorder missing",
                "Oyster Recorder — 录制器缺失",
            ),
            sess.failure_reason,
        )
        return sess
    logger.info("rc16.1c: recorder = %s (engine inferred from path)", recorder)

    # rc16.9 (grill-me revision): preflight monitor confirmation INVERTED
    # from opt-out to opt-in. Default behavior is now NO preflight dialog.
    #
    # Why inverted:
    #   1. Preflight in rc16.8 was opt-out (default ON) but had ZERO
    #      functional effect — the Rust recorder doesn't read
    #      OYSTER_PREFLIGHT_MONITOR_IDX. Cosmetic Tk dialog only.
    #   2. The dialog BLOCKS the launcher main thread for up to 30s on
    #      every launch — UX regression on the happy path (same anti-
    #      pattern as the rc16.1 watchdog we already retracted tonight).
    #   3. The dialog may STEAL CURSOR FOCUS before recorder spawns, which
    #      breaks rc16.2's cursor-monitor fallback (Rust uses cursor pos
    #      when game HWND is null — dialog leaves cursor on launcher's
    #      monitor, not game's).
    #
    # When rc16.10 lands and bumps the submodule to the rc16.9-preflight-
    # monitor-wired branch (which makes the Rust recorder READ the env var),
    # preflight becomes functional and the default can flip back to opt-out.
    # Until then: opt-in only via OYSTER_ENABLE_PREFLIGHT=1.
    if os.name == "nt" and os.environ.get("OYSTER_ENABLE_PREFLIGHT", "") \
            .strip().lower() in {"1", "true", "yes", "on"}:
        try:
            from preflight_capture import run_preflight  # noqa: PLC0415
            pf = run_preflight(target_pid=None, skip_dialog=False)
            if pf is None:
                logger.warning("rc16.9 preflight: declined or no monitor — "
                               "continuing with system default")
            else:
                logger.info("rc16.9 preflight: user confirmed monitor %d (%s)",
                            pf.index, pf.name)
                # Effective only after rc16.10 (submodule bump to rc16.9-
                # preflight-monitor-wired); currently cosmetic.
                os.environ["OYSTER_PREFLIGHT_MONITOR_IDX"] = str(pf.index)
        except Exception as e:  # noqa: BLE001 — preflight must never block
            logger.warning("rc16.9 preflight: failed (%s) — continuing "
                           "without preflight", e)

    # rc16.13 environment sanity check — runs AFTER preflight but BEFORE
    # we spawn the recorder. Detects known-bad runtime conditions (HDR,
    # Shadowplay overlay, Discord overlay, OBS already running, RTSS,
    # non-100% DPI) and warns the user. Set OYSTER_SKIP_SANITY=1 to bypass.
    if os.name == "nt" and os.environ.get("OYSTER_SKIP_SANITY", "") \
            .strip().lower() not in {"1", "true", "yes"}:
        try:
            from env_sanity import (  # noqa: PLC0415
                check_environment, show_sanity_warnings,
            )
            report = check_environment()
            if report.issues:
                logger.warning("rc16.13 sanity: %d issues detected",
                               len(report.issues))
                for issue in report.issues:
                    logger.warning("  [%s] %s: %s",
                                   issue.severity, issue.code,
                                   issue.message_en)
                # Show dialog only for warning/fatal severity (not info).
                if any(i.severity in ("warning", "fatal")
                       for i in report.issues):
                    continue_anyway = show_sanity_warnings(
                        report,
                        lang=os.environ.get("OYSTER_LANG", "en"),
                    )
                    if not continue_anyway:
                        logger.info("rc16.13 sanity: user aborted recording")
                        sess.failure_reason = (
                            "User aborted after sanity warnings"
                        )
                        return sess
        except Exception as e:  # noqa: BLE001 — sanity must never block
            logger.warning("rc16.13 sanity: check failed (%s) — continuing",
                           e)

    sess.recorder_proc = spawn_recorder(recorder)

    # rc16.8 input-capture runtime watchdog: daemon thread so it never blocks
    # shutdown. 60s after spawn, if <3 real input events landed in
    # inputs.jsonl we log loud + show a Tk popup pointing at the fallback.
    if os.name == "nt":
        threading.Thread(
            target=_input_watchdog, kwargs={"deadline_seconds": 60.0},
            daemon=True, name="oyster-input-watchdog",
        ).start()

    # Step 4: spawn javaw
    sess.javaw_proc = launcher.launch_javaw(plan, detach=False)
    logger.info("javaw started (pid=%d)", sess.javaw_proc.pid)

    # Step 5: wait for MC ready signal in latest.log
    log_path = launcher.latest_log_path(install_root_path)
    ready = launcher.wait_for_mc_ready(log_path, timeout_sec=ready_timeout_sec)
    if not ready:
        logger.warning(
            "MC ready marker not seen in %.0fs — continuing anyway",
            ready_timeout_sec,
        )

    # Step 6: locate recorder window + click ▶ 开始录制
    sess.recorder_window = wait_for_recorder_window(timeout_sec=20.0)
    if sess.recorder_window is not None and ready:
        sess.armed = click_recorder_button(sess.recorder_window)
        if sess.armed:
            logger.info("recorder armed via UIA")
        else:
            logger.warning("could not arm recorder — user must click manually")
    else:
        logger.warning("recorder window not found; skipping auto-arm")

    # Step 7: wait for javaw exit, then disarm
    rc = sess.javaw_proc.wait()
    logger.info("javaw exited rc=%d", rc)

    if rc != 0:
        sess.failure_reason = f"Minecraft exited with code {rc}"
        launcher.surface_failure_messagebox(plan, rc)
    else:
        if sess.armed and sess.recorder_window is not None:
            click_recorder_button(
                sess.recorder_window, button_label=RECORDER_DISARM_LABEL,
            )

    # rc16.11 (Layer 4): auto-diagnostic uploader. Fires only if lint
    # detected failures OR OYSTER_AUTO_UPLOAD=1 is set. The dialog is
    # non-blocking (runs in background thread) and silent-success on
    # upload completion. User opt-out via OYSTER_DISABLE_AUTODIAG=1.
    #
    # NON-BLOCKING is critical (grill-me lesson, repeated tonight as
    # rc16.1 watchdog and rc16.8 preflight): we MUST NOT join the
    # daemon thread before returning. If the user clicks Decline OR
    # the launcher process exits before the 30s dialog timeout, the
    # daemon thread dies with the process — and per the privacy
    # invariant, no upload happens unless the user explicitly clicks
    # Send. auto_confirm=False is passed inside the worker so
    # run_layer4 cannot silent-upload even if OYSTER_AUTO_UPLOAD=1.
    if os.environ.get("OYSTER_DISABLE_AUTODIAG", "").strip().lower() \
            not in {"1", "true", "yes", "on"}:
        _spawn_diag_uploader_thread()

    return sess


# --------------------------------------------------------------------------
# rc16.10 — Smoke test (fast user self-validation, no Minecraft launch)
# --------------------------------------------------------------------------


def run_smoke_test() -> int:
    """rc16.10: record ~10s of the primary monitor, validate via lint
    Criterion 25 (black-frame check), return 0/1. Plan B — Rust recorder
    has no ``--smoke-test`` CLI flag, so we drive it via env vars
    ``GAMEDATA_CI_MODE=1`` + ``GAMEDATA_OUTPUT_DIR=<d>`` (see
    ``vendor/recorder/src/config.rs``). Auto-record needs 10s of stable
    window + 20s process lifetime, so we let it run ~30s then signal
    graceful shutdown to flush the MP4.
    """
    import shutil  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    print("Oyster Recorder smoke test (rc16.10)")
    print("  Step 1/4: Locating recorder binary")
    root = launcher.install_root(None)
    recorder = find_recorder_exe(root) or find_recorder_exe(
        Path(sys.executable).resolve().parent,
    )
    if recorder is None:
        print(f"  FAIL: recorder binary not found (checked {root} + exe-sibling)")
        return 1
    print(f"     OK: {recorder}")

    session_dir = Path(tempfile.mkdtemp(prefix="oyster_smoke_"))
    print(f"  Step 2/4: Recording ~10s into {session_dir}")
    print("     (recorder needs ~20s stability gate before capture begins)")

    env = os.environ.copy()
    env["GAMEDATA_CI_MODE"] = "1"
    env["GAMEDATA_OUTPUT_DIR"] = str(session_dir)
    env.pop("OYSTER_ENABLE_PREFLIGHT", None)  # preflight dialog would block

    # CREATE_NEW_PROCESS_GROUP enables CTRL_BREAK_EVENT for graceful Windows
    # shutdown; on non-Windows it's a no-op and we use proc.terminate().
    creationflags = (0x00000200 | 0x08000000) if os.name == "nt" else 0
    proc = subprocess.Popen(
        [str(recorder)], cwd=str(recorder.parent), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    try:
        proc.wait(timeout=30.0)  # 20s gate + 10s capture
        print(f"     NOTE: recorder exited early (rc={proc.returncode})")
    except subprocess.TimeoutExpired:
        pass

    if proc.poll() is None:
        if os.name == "nt":
            try:
                import signal as _signal  # noqa: PLC0415
                proc.send_signal(_signal.CTRL_BREAK_EVENT)
            except (OSError, ValueError):
                proc.terminate()
        else:
            proc.terminate()
    try:
        proc.wait(timeout=15.0)
    except subprocess.TimeoutExpired:
        print("     WARN: recorder didn't exit gracefully — killing")
        proc.kill()
        proc.wait(timeout=5.0)
    print("     OK: recording phase complete")

    print("  Step 3/4: Locating output video")
    # Rust recorder writes ``main_record.mp4`` (see
    # ``vendor/recorder/src/record/session_manager.rs``); lint Criterion 25
    # globs for ``video.mp4`` / ``recording.mp4``, so we copy to that name.
    mp4s = list(session_dir.glob("**/*.mp4"))
    if not mp4s:
        print(f"  FAIL: no .mp4 produced in {session_dir}")
        print("     (recorder spawned but never captured — likely OBS or D3D init failure)")
        return 1
    src_video = mp4s[0]
    canonical = session_dir / "video.mp4"
    if src_video.resolve() != canonical.resolve():
        shutil.copyfile(src_video, canonical)
    print(f"     OK: {src_video.name} ({src_video.stat().st_size / 1024 / 1024:.1f} MB)")

    print("  Step 4/4: Validating frame content (black-frame check)")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from lint_v3_prd_grounded import (  # noqa: PLC0415
            LintReport, _check_video_content_health,
        )
    except ImportError as e:
        print(f"  WARN: lint module unavailable ({e}); recording kept at {session_dir}")
        return 1
    rpt = LintReport(data_dir=session_dir)
    _check_video_content_health(session_dir, rpt)
    crit = next((r for r in rpt.results if r.criterion_id == 25), None)
    if crit is None:
        print(f"  WARN: no Criterion 25 result (ffmpeg missing?); kept {session_dir}")
        return 1
    if crit.passed:
        print(f"     PASS: {crit.message}")
        print(f"     details: {crit.details}")
        shutil.rmtree(session_dir, ignore_errors=True)
        print("\nSMOKE TEST PASSED — recorder is working on this rig")
        return 0
    print(f"     FAIL: {crit.message}")
    print(f"     details: {crit.details}")
    print(f"\nSMOKE TEST FAILED — recording at {session_dir} kept for inspection")
    print("   Send this directory to Howard for diagnostic.")
    return 1


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------



# --------------------------------------------------------------------------
# rc17.4-form — operator config: first-launch + per-session route prompt
# --------------------------------------------------------------------------


def _apply_operator_config() -> None:
    """Handle first-launch form + per-session route_type prompt.

    1. If operator_config.json does not exist, show the first-launch form.
    2. Load config and apply OYSTER_* env vars.
    3. Prompt for per-session route_type (overrides saved default).
    """
    # Step 1: first-launch form if config missing
    if not config_exists():
        logger.info("rc17.4-form: no operator config found — showing setup form")
        cfg = show_first_launch_form()
        if cfg is None:
            logger.warning(
                "rc17.4-form: first-launch form cancelled or unavailable. "
                "Continuing without operator metadata."
            )
            return
        if not save_config(cfg):
            logger.error("rc17.4-form: failed to save operator config")
            return
        logger.info("rc17.4-form: operator config saved")

    # Step 2: load config and apply env vars
    cfg = load_config()
    if cfg is None:
        logger.warning("rc17.4-form: could not load operator config")
        return
    apply_config_to_env(cfg)

    # Step 3: per-session route_type prompt
    last_route = cfg.get("route_type", "1")
    session_route = prompt_route_type(last_used=str(last_route))
    if session_route is not None:
        os.environ["OYSTER_ROUTE_TYPE"] = str(session_route)
        logger.info("rc17.4-form: OYSTER_ROUTE_TYPE=%s", session_route)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Oyster single-button consumer launcher",
    )
    parser.add_argument("--install-root", type=Path, default=None,
                        help="Override install root (default per-user)")
    parser.add_argument("--username", default="Player")
    parser.add_argument("--xmx", default="4G")
    parser.add_argument("--profile-name", default=launcher.FABRIC_PROFILE_NAME)
    parser.add_argument("--ready-timeout", type=float,
                        default=DEFAULT_READY_TIMEOUT_SEC)
    parser.add_argument("--dry-run", action="store_true",
                        help="Build the plan + print the constructed javaw "
                             "cmd line; never spawn anything.")
    parser.add_argument("--write-desktop-shortcut", action="store_true",
                        help="(Windows only) write the desktop .lnk and exit.")
    parser.add_argument(
        "--smoke-test", action="store_true",
        help="rc16.10: record ~10s of primary monitor + validate output, "
             "then exit. No Minecraft launch. Fast self-test for users to "
             "verify the recorder works on their rig.",
    )
    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.smoke_test:
        return run_smoke_test()

    root = launcher.install_root(args.install_root)

    if args.write_desktop_shortcut:
        # Sibling exe assumed to be OysterPlay.exe (when bundled).
        target = Path(sys.executable).resolve()
        path = write_desktop_shortcut(target_exe=target)
        if path:
            print(f"Desktop shortcut: {path}")
            return 0
        return 1


    # ------------------------------------------------------------------
    # rc17.4-form — operator metadata: first-launch form + per-session
    # route_type prompt. Runs BEFORE spawn_recorder so env vars are set.
    # ------------------------------------------------------------------
    if not args.dry_run and not args.smoke_test:
        _apply_operator_config()

    sess = run_session(
        install_root_path=root,
        username=args.username,
        java_xmx=args.xmx,
        profile_name=args.profile_name,
        ready_timeout_sec=args.ready_timeout,
        dry_run=args.dry_run,
    )

    if args.dry_run:
        print("=== oyster_play.py dry-run ===")
        if sess.failure_reason:
            print(f"FAIL: {sess.failure_reason}")
            return 1
        if sess.plan is None:
            print("FAIL: no plan built")
            return 1
        print(f"install_root  : {root}")
        print(f"main_class    : {sess.plan.main_class}")
        print(f"argc          : {len(sess.plan.cmd)}")
        print("--- javaw cmd line ---")
        for token in sess.plan.cmd:
            print(token)
        return 0

    if sess.failure_reason:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
