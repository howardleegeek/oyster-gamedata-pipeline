#!/usr/bin/env python3
"""
bin/recorder_window_capture_helper.py — ffmpeg gdigrab window-only args.

Cross-platform helper that, given a Windows window title (substring match,
case-insensitive), returns the ``ffmpeg`` ``gdigrab`` argument list that will
capture **only that window**, not the full desktop.

Implementation:

  *   On Windows, uses ``ctypes.windll.user32.EnumWindows`` +
      ``GetWindowTextW`` + ``GetWindowRect`` to walk the top-level window
      list and resolve the matching HWND's bounding rectangle.
  *   On non-Windows hosts (Howard's Mac dev environment, CI Linux), the
      helpers gracefully report ``platform_supported=False`` and
      :func:`build_window_args` raises ``NotImplementedError`` — recorder
      callers can fall back to full-desktop capture.

This is a NEW FILE so ``bin/recorder_consumer_lite.py`` can opt-in via::

    from recorder_window_capture_helper import build_window_args
    args = build_window_args("Minecraft 1.20.1")
    subprocess.Popen(["ffmpeg", *args, "-c:v", "libx265", "out.mp4"])

The recorder file itself is **not edited**.

Spec: G263 (W31 wave). PP1 priority. ~120 lines.
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
from dataclasses import asdict, dataclass
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class WindowRect:
    """Bounding rectangle of a top-level window (screen coordinates)."""

    title: str
    hwnd: int
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        """Pixel width of the window."""
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        """Pixel height of the window."""
        return max(0, self.bottom - self.top)


def is_windows() -> bool:
    """Return True iff the current host is Windows."""
    return platform.system() == "Windows"


def enumerate_windows() -> List[WindowRect]:
    """Return every visible top-level window's title + bounding rect.

    Returns:
        List of :class:`WindowRect` (empty list on non-Windows hosts).
    """
    if not is_windows():
        return []

    # Lazy-import ctypes.wintypes only on Windows (the import is fine
    # everywhere, but keeping it scoped clarifies intent).
    import ctypes  # type: ignore[import-not-found]
    from ctypes import wintypes  # type: ignore[import-not-found]

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    EnumWindows = user32.EnumWindows
    GetWindowTextW = user32.GetWindowTextW
    GetWindowTextLengthW = user32.GetWindowTextLengthW
    GetWindowRect = user32.GetWindowRect
    IsWindowVisible = user32.IsWindowVisible

    # WNDENUMPROC: BOOL (HWND, LPARAM)
    EnumWindowsProc = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )

    results: List[WindowRect] = []

    def _callback(hwnd: int, _lparam: int) -> bool:
        if not IsWindowVisible(hwnd):
            return True
        length = GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        GetWindowTextW(hwnd, buf, length + 1)
        rect = wintypes.RECT()
        if not GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        results.append(
            WindowRect(
                title=buf.value,
                hwnd=int(hwnd),
                left=int(rect.left),
                top=int(rect.top),
                right=int(rect.right),
                bottom=int(rect.bottom),
            )
        )
        return True

    EnumWindows(EnumWindowsProc(_callback), 0)
    return results


def find_window(title_substr: str) -> Optional[WindowRect]:
    """Return the first visible window whose title contains ``title_substr``.

    Comparison is case-insensitive.

    Args:
        title_substr: Substring to search for in window titles.

    Returns:
        Matching :class:`WindowRect`, or None if no match (or non-Windows).
    """
    needle = title_substr.lower()
    for win in enumerate_windows():
        if needle in win.title.lower():
            return win
    return None


def build_window_args(
    title_substr: str,
    framerate: int = 30,
) -> Tuple[List[str], WindowRect]:
    """Build the ``ffmpeg gdigrab`` arguments capturing only the target window.

    Returns ``(args, rect)`` where ``args`` is the list of arguments that
    should be inserted **before** the output filename in an ``ffmpeg``
    invocation, and ``rect`` is the resolved window rectangle for logging.

    Example::

        args, rect = build_window_args("Minecraft")
        subprocess.run(["ffmpeg", *args, "-c:v", "libx265", "out.mp4"])

    Args:
        title_substr: Substring of the target window title.
        framerate: Capture framerate (PRD canonical 30 fps for video.mp4).

    Returns:
        Tuple ``(ffmpeg_args, rect)``.

    Raises:
        NotImplementedError: Host is not Windows.
        LookupError: No matching window found.
        ValueError: Window has zero width or height.
    """
    if not is_windows():
        raise NotImplementedError(
            "build_window_args requires Windows (ffmpeg gdigrab is Windows-only)"
        )
    rect = find_window(title_substr)
    if rect is None:
        raise LookupError(f"no visible window matches title containing {title_substr!r}")
    if rect.width <= 0 or rect.height <= 0:
        raise ValueError(
            f"window {rect.title!r} has zero area: {rect.width}x{rect.height}"
        )

    args: List[str] = [
        "-f", "gdigrab",
        "-framerate", str(framerate),
        "-offset_x", str(rect.left),
        "-offset_y", str(rect.top),
        "-video_size", f"{rect.width}x{rect.height}",
        "-i", "desktop",
    ]
    return args, rect


def main(argv: Optional[list] = None) -> int:
    """CLI entry point.

    Prints a JSON object containing the resolved window rectangle and the
    ffmpeg argument list, so other tooling (or shell scripts via ``jq``)
    can consume it.
    """
    parser = argparse.ArgumentParser(
        description="Resolve ffmpeg gdigrab args for a Windows window title"
    )
    parser.add_argument("--title", required=True, help="Substring of window title")
    parser.add_argument("--framerate", type=int, default=30)
    args_ns = parser.parse_args(argv)

    if not is_windows():
        print(
            json.dumps({"platform_supported": False, "platform": platform.system()}),
        )
        return 3
    try:
        ff_args, rect = build_window_args(args_ns.title, framerate=args_ns.framerate)
    except (LookupError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2

    print(
        json.dumps(
            {
                "platform_supported": True,
                "rect": asdict(rect),
                "ffmpeg_args": ff_args,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
