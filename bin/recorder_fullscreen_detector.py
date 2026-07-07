#!/usr/bin/env python3
"""
bin/recorder_fullscreen_detector.py — exclusive-fullscreen detector (G273, W31).

Purpose
-------
``gdigrab`` (the ffmpeg input we use on Windows) cannot capture frames
when Minecraft is running in *exclusive* fullscreen — it silently produces
an empty mp4. This module identifies that state pre-flight so the
recorder can prompt the tester to switch to windowed/borderless mode.

Implementation
--------------
On Windows we call into ``user32.dll`` via :mod:`ctypes` to read the
foreground window's client rect and compare to ``GetSystemMetrics``
(SM_CXSCREEN / SM_CYSCREEN). If the rects match exactly *and* the window
title contains "Minecraft", we report exclusive fullscreen. The
detection has a known false-positive on borderless-fullscreen — that
mode actually works with gdigrab, so we err on the side of warning
(better one extra prompt than a silent dead capture).

On non-Windows hosts the detector returns ``False`` — Howard's testers
are Windows-only, but a stub keeps macOS dev machines (mac1/mac2) happy
during local CI. Standalone, stdlib only.

Usage
-----
    >>> from recorder_fullscreen_detector import detect_exclusive_fullscreen
    >>> if detect_exclusive_fullscreen().is_exclusive_fullscreen:
    ...     show_banner("请使用窗口化模式 — 全屏会让录制黑屏")
"""
from __future__ import annotations

import ctypes
import logging
import sys
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

WINDOW_TITLE_KEYWORDS = ("minecraft",)
SM_CXSCREEN = 0
SM_CYSCREEN = 1


@dataclass
class DetectionResult:
    """Structured result; ``False``/``None`` fields mean "not detected"."""
    is_exclusive_fullscreen: bool
    foreground_title: Optional[str]
    window_size: Optional[tuple]
    screen_size: Optional[tuple]
    platform: str
    note: str = ""


def _windows_detect() -> DetectionResult:
    """Win32-specific detection. Returns a DetectionResult."""
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    except AttributeError:
        return DetectionResult(False, None, None, None, sys.platform, "user32 unavailable")
    user32.SetProcessDPIAware()
    hwnd = user32.GetForegroundWindow()
    if hwnd == 0:
        return DetectionResult(False, None, None, None, "win32", "no foreground window")
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    title = buf.value
    rect = ctypes.wintypes.RECT() if hasattr(ctypes, "wintypes") else None
    if rect is None:
        class _RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
        rect = _RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return DetectionResult(False, title, None, None, "win32", "GetWindowRect failed")
    win_w = rect.right - rect.left
    win_h = rect.bottom - rect.top
    screen_w = user32.GetSystemMetrics(SM_CXSCREEN)
    screen_h = user32.GetSystemMetrics(SM_CYSCREEN)
    is_minecraft = any(kw in title.lower() for kw in WINDOW_TITLE_KEYWORDS)
    is_fullscreen = (
        is_minecraft
        and win_w == screen_w
        and win_h == screen_h
        and rect.left == 0
        and rect.top == 0
    )
    return DetectionResult(
        is_exclusive_fullscreen=is_fullscreen,
        foreground_title=title,
        window_size=(win_w, win_h),
        screen_size=(screen_w, screen_h),
        platform="win32",
        note="" if is_fullscreen else "windowed/borderless OK",
    )


def detect_exclusive_fullscreen() -> DetectionResult:
    """Top-level entry point. Always safe to call on any platform."""
    if sys.platform.startswith("win"):
        try:
            return _windows_detect()
        except Exception as exc:  # noqa: BLE001 — never crash the recorder
            logger.debug("exclusive-fullscreen detect failed", exc_info=True)
            return DetectionResult(False, None, None, None, "win32", f"detect error: {exc}")
    return DetectionResult(
        is_exclusive_fullscreen=False,
        foreground_title=None,
        window_size=None,
        screen_size=None,
        platform=sys.platform,
        note="non-Windows host — fullscreen check skipped",
    )


def _main(argv: list[str]) -> int:
    result = detect_exclusive_fullscreen()
    print(f"platform           = {result.platform}")
    print(f"is_fullscreen      = {result.is_exclusive_fullscreen}")
    print(f"foreground_title   = {result.foreground_title!r}")
    print(f"window_size        = {result.window_size}")
    print(f"screen_size        = {result.screen_size}")
    if result.note:
        print(f"note               = {result.note}")
    return 1 if result.is_exclusive_fullscreen else 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
