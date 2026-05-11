#!/usr/bin/env python3
"""rc16.6 · bin/preflight_capture.py — pre-flight monitor preview / picker.

Tonight's failure mode: tester plays Minecraft for 5 minutes, gets a
black or wrong-monitor file because the recorder captured DISPLAY0
while the game was on DISPLAY1. This module runs *before* capture and
shows a 5-10s preview: enumerate monitors via Win32 EnumDisplayMonitors,
detect which one the game (PID) is on, snap a downscaled screenshot of
each, show a Tk dialog with thumbnails + radio buttons (default = game
monitor, 30s auto-confirm), persist the choice by *rig hash* so re-runs
on the same setup skip the dialog. Returns the chosen ``MonitorInfo``.

Cross-platform: imports cleanly on macOS — Windows-only APIs are
lazy-loaded inside functions, and macOS gets a single fake-monitor
stub. Integration in ``bin/oyster_play.py`` lands in rc16.7.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger("preflight_capture")

APP_DIR_NAME = "Oyster Recorder"
PICK_FILENAME = "preflight_pick.json"
THUMBNAIL_SCALE = 0.25
DIALOG_AUTO_CONFIRM_SECONDS = 30
SCREENSHOT_BUDGET_MS = 200

@dataclass(frozen=True)
class MonitorInfo:
    """One physical monitor. Coords are in virtual-screen space (a
    secondary monitor on Windows may have negative ``x`` / ``y``).
    ``handle`` is the HMONITOR as int; 0 on the macOS mock."""

    index: int
    handle: int
    x: int
    y: int
    width: int
    height: int
    name: str
    is_primary: bool
    is_game_window: bool

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        """PIL-style bbox: (left, top, right, bottom)."""
        return (self.x, self.y, self.x + self.width, self.y + self.height)

    @property
    def resolution_label(self) -> str:
        return f"{self.width}x{self.height}"

def _app_cache_dir() -> Path:
    """Windows: ``%LOCALAPPDATA%/Oyster Recorder``. macOS: ``~/Library/
    Application Support/...``. Linux fallback: ``~/.cache/oyster-recorder``."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_DIR_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    return Path.home() / ".cache" / "oyster-recorder"

def _pick_file() -> Path:
    return _app_cache_dir() / PICK_FILENAME

# --- monitor enumeration -------------------------------------------------
def _enumerate_monitors_windows() -> list[MonitorInfo]:
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32

    class _MONITORINFOEXW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD),
                    ("szDevice", wintypes.WCHAR * 32)]

    MONITORINFOF_PRIMARY = 0x00000001
    results: list[MonitorInfo] = []
    MonitorEnumProc = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
        ctypes.POINTER(wintypes.RECT), wintypes.LPARAM,
    )

    def _cb(hmon, _hdc, _lprc, _lparam):
        info = _MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
        if not user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            return True
        rc = info.rcMonitor
        results.append(MonitorInfo(
            index=len(results), handle=int(hmon),
            x=int(rc.left), y=int(rc.top),
            width=int(rc.right - rc.left), height=int(rc.bottom - rc.top),
            name=str(info.szDevice),
            is_primary=bool(info.dwFlags & MONITORINFOF_PRIMARY),
            is_game_window=False,
        ))
        return True

    user32.EnumDisplayMonitors(0, None, MonitorEnumProc(_cb), 0)
    return results

def _enumerate_monitors_mock() -> list[MonitorInfo]:
    """Single-monitor stub for macOS / Linux / failure fallback."""
    return [MonitorInfo(
        index=0, handle=0, x=0, y=0, width=1920, height=1080,
        name="\\\\.\\MOCKDISPLAY1", is_primary=True, is_game_window=False,
    )]

def enumerate_monitors() -> list[MonitorInfo]:
    """Return all attached monitors. Always at least one entry."""
    try:
        if os.name == "nt":
            mons = _enumerate_monitors_windows()
            if mons:
                return mons
            logger.warning("EnumDisplayMonitors returned 0 — using mock")
    except Exception:
        logger.exception("monitor enumeration failed — using mock")
    return _enumerate_monitors_mock()

# --- game-window detection -----------------------------------------------
def _hwnd_for_pid_windows(target_pid: int) -> int | None:
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found = {"hwnd": 0}
    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if int(pid.value) == int(target_pid):
            found["hwnd"] = int(hwnd)
            return False
        return True

    user32.EnumWindows(EnumWindowsProc(_cb), 0)
    return found["hwnd"] or None

def detect_game_monitor(target_pid: int) -> MonitorInfo | None:
    """Return the ``MonitorInfo`` the target PID's main window is on.
    On macOS / failure returns the primary monitor (best effort)."""
    monitors = enumerate_monitors()
    if not monitors:
        return None
    if os.name != "nt":
        return next((m for m in monitors if m.is_primary), monitors[0])
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = _hwnd_for_pid_windows(target_pid)
        if not hwnd:
            logger.info("no visible window for pid=%s", target_pid)
            return None
        MONITOR_DEFAULTTONEAREST = 2
        hmon = int(user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST))
        for m in monitors:
            if m.handle == hmon:
                return m
        logger.warning("MonitorFromWindow returned %s not in enumerated set", hmon)
        return None
    except Exception:
        logger.exception("detect_game_monitor failed for pid=%s", target_pid)
        return None

# --- screenshots ---------------------------------------------------------
def screenshot_monitor(monitor: MonitorInfo, scale: float = THUMBNAIL_SCALE) -> Path:
    """Snap a downscaled PNG of ``monitor`` and return the file path.
    Target <200 ms per monitor. Always returns a path; on failure writes
    a 1x1 placeholder so callers don't have to special-case missing files."""
    thumb_dir = _app_cache_dir() / "preflight_thumbs"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    out_path = thumb_dir / f"monitor_{monitor.index}.png"
    started = time.perf_counter()
    try:
        from PIL import ImageGrab  # lazy: keeps Mac CI light
        # all_screens=True is Windows-only; Pillow >=9 ignores it elsewhere.
        try:
            img = ImageGrab.grab(bbox=monitor.bbox, all_screens=True)
        except TypeError:
            img = ImageGrab.grab(bbox=monitor.bbox)
        if scale and scale != 1.0:
            img = img.resize((max(1, int(monitor.width * scale)),
                              max(1, int(monitor.height * scale))))
        img.save(out_path, format="PNG", optimize=False)
    except Exception:
        logger.exception("screenshot_monitor failed for idx=%s", monitor.index)
        try:
            from PIL import Image
            Image.new("RGB", (1, 1), (32, 32, 32)).save(out_path)
        except Exception:
            out_path.write_bytes(b"")
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    if elapsed_ms > SCREENSHOT_BUDGET_MS:
        logger.warning("screenshot_monitor idx=%s took %.1f ms (budget %d ms)",
                       monitor.index, elapsed_ms, SCREENSHOT_BUDGET_MS)
    return out_path

# --- rig fingerprint + persisted pick ------------------------------------
def _gpu_name() -> str:
    """Best-effort primary GPU name; empty string on failure."""
    if os.name != "nt":
        return "mock-gpu"
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32

        class _DISPLAY_DEVICEW(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32),
                        ("DeviceString", wintypes.WCHAR * 128), ("StateFlags", wintypes.DWORD),
                        ("DeviceID", wintypes.WCHAR * 128), ("DeviceKey", wintypes.WCHAR * 128)]

        dd = _DISPLAY_DEVICEW()
        dd.cb = ctypes.sizeof(_DISPLAY_DEVICEW)
        if user32.EnumDisplayDevicesW(None, 0, ctypes.byref(dd), 0):
            return str(dd.DeviceString)
    except Exception:
        logger.debug("gpu_name lookup failed", exc_info=True)
    return ""

def compute_rig_hash(monitors: list[MonitorInfo] | None = None) -> str:
    """SHA-256 of (gpu_name | monitor_count | primary_resolution). Changes
    when monitors are plugged/unplugged, auto-invalidating saved picks."""
    mons = monitors if monitors is not None else enumerate_monitors()
    primary = next((m for m in mons if m.is_primary), mons[0] if mons else None)
    primary_res = primary.resolution_label if primary else "unknown"
    payload = f"{_gpu_name()}|{len(mons)}|{primary_res}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

def load_user_pick(rig_hash: str) -> int | None:
    """Return saved monitor index for this ``rig_hash`` or ``None``."""
    path = _pick_file()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("preflight_pick.json unreadable — ignoring")
        return None
    entry = data.get(rig_hash)
    if not isinstance(entry, dict):
        return None
    idx = entry.get("monitor_idx")
    return int(idx) if isinstance(idx, int) else None

def save_user_pick(rig_hash: str, monitor_idx: int) -> None:
    """Persist ``monitor_idx`` under ``rig_hash`` (creates dir + file)."""
    path = _pick_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    data[rig_hash] = {"monitor_idx": int(monitor_idx), "saved_at": int(time.time())}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")

# --- Tk picker dialog ----------------------------------------------------
def show_picker_dialog(monitors: list[MonitorInfo], default_idx: int) -> int:
    """Show a modal Tk dialog with thumbnails + radio buttons. Returns the
    chosen monitor index. Auto-confirms with ``default_idx`` after
    ``DIALOG_AUTO_CONFIRM_SECONDS``. Lazy-imports tkinter."""
    if not monitors:
        raise ValueError("show_picker_dialog requires at least one monitor")
    if not (0 <= default_idx < len(monitors)):
        default_idx = 0

    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("Oyster Recorder — Pre-flight: choose a monitor")
    root.attributes("-topmost", True)
    root.resizable(False, False)
    # PhotoImage refs must outlive the dialog or Tk stops painting them.
    root._oyster_thumb_refs = []  # type: ignore[attr-defined]

    selected = tk.IntVar(value=default_idx)
    countdown = tk.StringVar(value=f"Auto-confirms in {DIALOG_AUTO_CONFIRM_SECONDS}s…")
    cols = max(1, len(monitors))

    ttk.Label(root, padding=(12, 10), text=(
        "Recording will start on the selected monitor.\n"
        "If the wrong screen is highlighted, pick the right one.")
    ).grid(row=0, column=0, columnspan=cols, sticky="w")

    thumb_frame = ttk.Frame(root, padding=(8, 4))
    thumb_frame.grid(row=1, column=0, columnspan=cols)

    for i, mon in enumerate(monitors):
        cell = ttk.Frame(thumb_frame, padding=6, relief="ridge")
        cell.grid(row=0, column=i, padx=6, pady=4)
        try:
            png_path = screenshot_monitor(mon, scale=THUMBNAIL_SCALE)
            img = tk.PhotoImage(file=str(png_path))
            root._oyster_thumb_refs.append(img)  # type: ignore[attr-defined]
            ttk.Label(cell, image=img).pack()
        except Exception:
            ttk.Label(cell, text="(preview unavailable)", width=28, anchor="center").pack(pady=18)
        bits = [f"Monitor {mon.index + 1}", f"({mon.resolution_label})"]
        if mon.is_primary:
            bits.append("· primary")
        if mon.is_game_window:
            bits.append("· game window here")
        ttk.Label(cell, text=" ".join(bits)).pack(pady=(4, 2))
        ttk.Radiobutton(cell, value=i, variable=selected).pack()

    ttk.Label(root, textvariable=countdown, padding=(12, 0)).grid(
        row=2, column=0, columnspan=cols, sticky="w")
    button_row = ttk.Frame(root, padding=(12, 10))
    button_row.grid(row=3, column=0, columnspan=cols, sticky="e")

    result = {"idx": default_idx}
    remaining = {"sec": DIALOG_AUTO_CONFIRM_SECONDS}

    def _confirm() -> None:
        result["idx"] = int(selected.get())
        root.destroy()

    def _tick() -> None:
        remaining["sec"] -= 1
        if remaining["sec"] <= 0:
            result["idx"] = int(selected.get())
            root.destroy()
            return
        countdown.set(f"Auto-confirms in {remaining['sec']}s…")
        root.after(1000, _tick)

    ttk.Button(button_row, text="Confirm", command=_confirm).pack(side="right")
    root.after(1000, _tick)
    root.mainloop()
    return int(result["idx"])

# --- public entry-point --------------------------------------------------
def run_preflight(target_pid: int | None = None,
                  skip_dialog: bool = False) -> MonitorInfo | None:
    """Orchestrate the full preflight. Returns the chosen ``MonitorInfo``.

    Flow: enumerate → detect game monitor (if ``target_pid`` given) →
    consult saved pick by rig hash → else show dialog (or auto-pick when
    ``skip_dialog``) → save chosen index → return ``MonitorInfo``.
    """
    monitors = enumerate_monitors()
    if not monitors:
        logger.error("no monitors found — preflight cannot continue")
        return None

    game_monitor = detect_game_monitor(target_pid) if target_pid is not None else None
    if game_monitor is not None:
        monitors = [
            MonitorInfo(**{**asdict(m), "is_game_window": (m.index == game_monitor.index)})
            for m in monitors
        ]

    rig_hash = compute_rig_hash(monitors)
    saved_idx = load_user_pick(rig_hash)
    if saved_idx is not None and 0 <= saved_idx < len(monitors):
        logger.info("preflight: using saved monitor idx=%s for rig=%s", saved_idx, rig_hash[:8])
        return monitors[saved_idx]

    default_idx = (game_monitor.index if game_monitor
                   else next((m.index for m in monitors if m.is_primary), 0))

    if skip_dialog:
        logger.info("preflight: skip_dialog → idx=%s", default_idx)
        return monitors[default_idx]

    try:
        chosen_idx = show_picker_dialog(monitors, default_idx)
    except Exception:
        logger.exception("preflight dialog failed — falling back to idx=%s", default_idx)
        return monitors[default_idx]

    if not (0 <= chosen_idx < len(monitors)):
        chosen_idx = default_idx
    save_user_pick(rig_hash, chosen_idx)
    return monitors[chosen_idx]
