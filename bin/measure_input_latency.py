#!/usr/bin/env python3
"""
measure_input_latency.py — Synthetic end-to-end input-to-frame latency measurement.

PRD §3.1 requires delay ≤ 20 ms. This tool measures the median latency between
a synthetic keypress and the resulting visual change on screen.

Methodology:
  1. Locate the Minecraft window (by title pattern).
  2. Bring it to foreground.
  3. Send a synthetic keypress (e.g. 'W' or arrow key) via platform-native API:
       - Windows: ctypes → SendInput
       - macOS:   Quartz/CoreGraphics (CGEventCreateKeyboardEvent)
       - Linux:   X11 XTestFakeKeyEvent (via python-xlib)
  4. Capture the screen at high frequency (~500 Hz) via mss.
  5. Detect the first frame where the viewport pixel region changed vs baseline.
  6. Compute latency = (first_changed_frame_timestamp - keypress_timestamp).
  7. Repeat N=50 trials, report median, p50, p95, p99, mean, std.

Output: JSON written to session_dir with full measurement details.

Usage:
    python bin/measure_input_latency.py --session-dir ./latency_results --trials 50
    python bin/measure_input_latency.py --help
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import statistics
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------
try:
    import mss
    import mss.tools
except ImportError:
    raise ImportError("mss is required. Install with: pip install mss")

try:
    import numpy as np
except ImportError:
    raise ImportError("numpy is required. Install with: pip install numpy")

try:
    from PIL import Image
except ImportError:
    raise ImportError("Pillow is required. Install with: pip install Pillow")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_TRIALS = 50
DEFAULT_CAPTURE_FPS = 500  # target capture rate (~2 ms per frame)
DEFAULT_WARMUP_FRAMES = 5  # frames to skip before sending keypress
DEFAULT_KEY = "w"  # key to press (forward movement in MC)
DEFAULT_ROI_WIDTH = 64  # width of region-of-interest in pixels
DEFAULT_ROI_HEIGHT = 64  # height of ROI
DEFAULT_CHANGE_THRESHOLD = 30  # per-pixel RGB delta threshold (0-255)
DEFAULT_PRD_LIMIT_MS = 20.0  # PRD §3.1 limit

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class TrialResult:
    """Result of a single latency trial."""
    trial_id: int
    latency_ms: float
    frames_until_change: int
    actual_capture_fps: float
    roi_mean_delta: float  # mean pixel delta of the first changed frame
    success: bool
    error: Optional[str] = None


@dataclass
class SessionReport:
    """Aggregate report for a full measurement session."""
    tool: str = "measure_input_latency"
    version: str = "1.0.0"
    timestamp_utc: str = ""
    platform: str = ""
    python_version: str = ""
    trials_requested: int = 0
    trials_completed: int = 0
    trials_failed: int = 0
    capture_fps_target: int = 0
    key_pressed: str = ""
    roi_width: int = 0
    roi_height: int = 0
    change_threshold: int = 0
    latencies_ms: List[float] = field(default_factory=list)
    median_ms: float = 0.0
    mean_ms: float = 0.0
    std_ms: float = 0.0
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0
    min_ms: float = 0.0
    max_ms: float = 0.0
    prd_limit_ms: float = DEFAULT_PRD_LIMIT_MS
    prd_pass: bool = True
    trial_details: List[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Platform-specific input injection
# ---------------------------------------------------------------------------

class InputInjector:
    """Platform-agnostic synthetic keypress injector."""

    def __init__(self, key: str = DEFAULT_KEY):
        self.key = key
        self._platform = platform.system()

    def press_and_release(self) -> float:
        """
        Send a key press + release and return the monotonic timestamp
        of when the press was issued (in seconds, high-resolution).
        """
        if self._platform == "Windows":
            return self._windows_press_release()
        elif self._platform == "Darwin":
            return self._macos_press_release()
        else:
            return self._linux_press_release()

    # -- Windows: SendInput via ctypes ---------------------------------------
    def _windows_press_release(self) -> float:
        import ctypes
        from ctypes import wintypes

        # Virtual key codes
        VK_MAP = {
            "w": 0x57, "a": 0x41, "s": 0x53, "d": 0x44,
            "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
            "space": 0x20, "shift": 0x10,
        }
        vk = VK_MAP.get(self.key.lower(), ord(self.key.upper()) if len(self.key) == 1 else 0x57)

        INPUT_KEYBOARD = 1
        KEYEVENTF_KEYUP = 0x0002

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [
                ("dx", wintypes.LONG),
                ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
            ]

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
            ]

        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [
                ("uMsg", wintypes.DWORD),
                ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD),
            ]

        class INPUT(ctypes.Structure):
            class _INPUT(ctypes.Union):
                _fields_ = [
                    ("mi", MOUSEINPUT),
                    ("ki", KEYBDINPUT),
                    ("hi", HARDWAREINPUT),
                ]
            _fields_ = [
                ("type", wintypes.DWORD),
                ("_input", _INPUT),
            ]

        def _make_input(vk_code: int, keyup: bool = False) -> INPUT:
            inp = INPUT()
            inp.type = INPUT_KEYBOARD
            flags = KEYEVENTF_KEYUP if keyup else 0
            inp._input.ki = KEYBDINPUT(vk_code, 0, flags, 0, None)
            return inp

        press_inp = _make_input(vk, keyup=False)
        release_inp = _make_input(vk, keyup=True)

        # Timestamp the press
        ts = time.monotonic()
        ctypes.windll.user32.SendInput(1, ctypes.byref(press_inp), ctypes.sizeof(press_inp))
        ctypes.windll.user32.SendInput(1, ctypes.byref(release_inp), ctypes.sizeof(release_inp))
        return ts

    # -- macOS: Quartz/CoreGraphics -----------------------------------------
    def _macos_press_release(self) -> float:
        import Quartz
        import Carbon

        KEY_MAP = {
            "w": 0x0D, "a": 0x00, "s": 0x01, "d": 0x02,
            "up": 0x7E, "down": 0x7D, "left": 0x7B, "right": 0x7C,
            "space": 0x31, "shift": 0x38,
        }
        keycode = KEY_MAP.get(self.key.lower(), 0x0D)

        ts = time.monotonic()

        # Press
        event_down = Quartz.CGEventCreateKeyboardEvent(None, keycode, True)
        Quartz.CGEventPost(Quartz.kCGSessionEventTap, event_down)
        # Release
        event_up = Quartz.CGEventCreateKeyboardEvent(None, keycode, False)
        Quartz.CGEventPost(Quartz.kCGSessionEventTap, event_up)

        return ts

    # -- Linux: X11 XTest ---------------------------------------------------
    def _linux_press_release(self) -> float:
        try:
            from Xlib import X, display
            from Xlib.ext import xtest
        except ImportError:
            raise RuntimeError(
                "python-xlib required on Linux. Install with: pip install python-xlib"
            )

        KEY_MAP = {
            "w": 25, "a": 38, "s": 39, "d": 40,
            "up": 111, "down": 116, "left": 113, "right": 114,
            "space": 65, "shift": 50,
        }
        keycode = KEY_MAP.get(self.key.lower(), 25)

        dpy = display.Display()
        ts = time.monotonic()

        xtest.fake_input(dpy, X.KeyPress, keycode)
        dpy.sync()
        xtest.fake_input(dpy, X.KeyRelease, keycode)
        dpy.sync()

        return ts


# ---------------------------------------------------------------------------
# Window management
# ---------------------------------------------------------------------------

class WindowManager:
    """Find and focus the Minecraft window."""

    @staticmethod
    def find_mc_window() -> Optional[dict]:
        """
        Return a dict with window info: {title, left, top, width, height}.
        Returns None if no MC window found.
        """
        system = platform.system()

        if system == "Windows":
            return WindowManager._find_windows()
        elif system == "Darwin":
            return WindowManager._find_macos()
        else:
            return WindowManager._find_linux()

    @staticmethod
    def _find_windows() -> Optional[dict]:
        import ctypes
        from ctypes import wintypes

        MC_TITLE_PATTERNS = ["Minecraft", "minecraft", "mc", "MC"]

        def enum_callback(hwnd, results):
            if ctypes.windll.user32.IsWindowVisible(hwnd):
                length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
                    title = buf.value
                    for pat in MC_TITLE_PATTERNS:
                        if pat.lower() in title.lower():
                            rect = wintypes.RECT()
                            ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
                            results.append({
                                "hwnd": hwnd,
                                "title": title,
                                "left": rect.left,
                                "top": rect.top,
                                "width": rect.right - rect.left,
                                "height": rect.bottom - rect.top,
                            })
            return True

        results = []
        WNDENUMPROC = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, ctypes.POINTER(ctypes.c_int)
        )
        ctypes.windll.user32.EnumWindows(WNDENUMPROC(enum_callback), 0)
        return results[0] if results else None

    @staticmethod
    def _find_macos() -> Optional[dict]:
        import subprocess
        try:
            result = subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to get properties of '
                 '(first process whose name contains "Minecraft" or name contains "java")'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                # Fallback: just return full screen
                return None
        except Exception:
            pass
        return None

    @staticmethod
    def _find_linux() -> Optional[dict]:
        try:
            import subprocess
            result = subprocess.run(
                ["xdotool", "search", "--name", "Minecraft"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                win_id = result.stdout.strip().split("\n")[0]
                geom = subprocess.run(
                    ["xdotool", "getwindowgeometry", "--shell", win_id],
                    capture_output=True, text=True, timeout=5
                )
                info = {}
                for line in geom.stdout.split("\n"):
                    if "=" in line:
                        k, v = line.split("=", 1)
                        info[k] = int(v)
                return {
                    "title": "Minecraft",
                    "left": info.get("X", 0),
                    "top": info.get("Y", 0),
                    "width": info.get("WIDTH", 1920),
                    "height": info.get("HEIGHT", 1080),
                }
        except Exception:
            pass
        return None

    @staticmethod
    def focus_window(win_info: Optional[dict]) -> None:
        """Bring the MC window to foreground."""
        if win_info is None:
            return
        system = platform.system()
        if system == "Windows":
            import ctypes
            ctypes.windll.user32.SetForegroundWindow(win_info["hwnd"])
            time.sleep(0.2)
        elif system == "Darwin":
            import subprocess
            subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to set frontmost of '
                 '(first process whose name contains "Minecraft") to true'],
                capture_output=True, timeout=5
            )
            time.sleep(0.2)
        else:
            try:
                import subprocess
                subprocess.run(["xdotool", "windowactivate", "--sync", "Minecraft"], timeout=5)
                time.sleep(0.2)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Screen capture & change detection
# ---------------------------------------------------------------------------

class LatencyDetector:
    """
    High-frequency screen capture + pixel-delta change detection.

    Captures a small ROI in the center of the screen at ~500 Hz.
    After a warmup period, a keypress is injected. The first frame whose
    mean pixel delta exceeds the threshold is considered the "response frame".
    Latency = response_timestamp - keypress_timestamp.
    """

    def __init__(
        self,
        capture_fps: int = DEFAULT_CAPTURE_FPS,
        warmup_frames: int = DEFAULT_WARMUP_FRAMES,
        roi_width: int = DEFAULT_ROI_WIDTH,
        roi_height: int = DEFAULT_ROI_HEIGHT,
        change_threshold: int = DEFAULT_CHANGE_THRESHOLD,
    ):
        self.capture_fps = capture_fps
        self.warmup_frames = warmup_frames
        self.roi_width = roi_width
        self.roi_height = roi_height
        self.change_threshold = change_threshold
        self._frame_interval = 1.0 / capture_fps

    def run_trial(self, injector: InputInjector) -> TrialResult:
        """Execute a single trial. Returns TrialResult."""
        with mss.mss() as sct:
            monitor = sct.monitors[1]  # primary monitor
            cx = monitor["left"] + monitor["width"] // 2
            cy = monitor["top"] + monitor["height"] // 2
            roi = {
                "left": cx - self.roi_width // 2,
                "top": cy - self.roi_height // 2,
                "width": self.roi_width,
                "height": self.roi_height,
            }

            # Phase 1: Warmup — capture baseline frames
            baseline = None
            for _ in range(self.warmup_frames):
                frame = sct.grab(roi)
                baseline = np.array(frame)[:, :, :3]  # drop alpha
                time.sleep(self._frame_interval * 0.5)

            if baseline is None:
                return TrialResult(
                    trial_id=-1, latency_ms=0, frames_until_change=0,
                    actual_capture_fps=0, roi_mean_delta=0, success=False,
                    error="Failed to capture baseline frame",
                )

            # Phase 2: Send keypress
            keypress_ts = injector.press_and_release()

            # Phase 3: Capture until change detected or timeout
            max_wait = 2.0  # seconds — generous timeout
            start = time.monotonic()
            frame_count = 0
            changed = False
            first_delta = 0.0

            while (time.monotonic() - start) < max_wait:
                frame = sct.grab(roi)
                frame_count += 1
                current = np.array(frame)[:, :, :3]
                delta = np.mean(np.abs(current.astype(np.int16) - baseline.astype(np.int16)))

                if delta > self.change_threshold:
                    changed = True
                    first_delta = float(delta)
                    break

                baseline = current  # rolling baseline to handle ambient drift
                time.sleep(self._frame_interval * 0.5)

            elapsed = time.monotonic() - start
            actual_fps = frame_count / elapsed if elapsed > 0 else 0

            if changed:
                latency_ms = (elapsed) * 1000.0
                return TrialResult(
                    trial_id=-1,
                    latency_ms=round(latency_ms, 3),
                    frames_until_change=frame_count,
                    actual_capture_fps=round(actual_fps, 1),
                    roi_mean_delta=round(first_delta, 2),
                    success=True,
                )
            else:
                return TrialResult(
                    trial_id=-1,
                    latency_ms=0,
                    frames_until_change=frame_count,
                    actual_capture_fps=round(actual_fps, 1),
                    roi_mean_delta=0,
                    success=False,
                    error=f"No change detected within {max_wait}s timeout",
                )


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def percentile(data: List[float], p: float) -> float:
    """Compute the p-th percentile (0-100) of a sorted list."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_data[int(k)]
    d0 = sorted_data[int(f)] * (c - k)
    d1 = sorted_data[int(c)] * (k - f)
    return d0 + d1


def run_measurement_session(
    session_dir: Path,
    trials: int = DEFAULT_TRIALS,
    capture_fps: int = DEFAULT_CAPTURE_FPS,
    key: str = DEFAULT_KEY,
    roi_width: int = DEFAULT_ROI_WIDTH,
    roi_height: int = DEFAULT_ROI_HEIGHT,
    change_threshold: int = DEFAULT_CHANGE_THRESHOLD,
    prd_limit_ms: float = DEFAULT_PRD_LIMIT_MS,
) -> SessionReport:
    """
    Run the full measurement session and write results to session_dir.
    """
    session_dir.mkdir(parents=True, exist_ok=True)

    report = SessionReport(
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
        platform=platform.platform(),
        python_version=platform.python_version(),
        trials_requested=trials,
        capture_fps_target=capture_fps,
        key_pressed=key,
        roi_width=roi_width,
        roi_height=roi_height,
        change_threshold=change_threshold,
        prd_limit_ms=prd_limit_ms,
    )

    print(f"[measure_input_latency] Session started: {report.timestamp_utc}")
    print(f"  Platform : {report.platform}")
    print(f"  Python   : {report.python_version}")
    print(f"  Trials   : {trials}")
    print(f"  Key      : {key}")
    print(f"  Capture  : {capture_fps} FPS target")
    print(f"  ROI      : {roi_width}x{roi_height} px")
    print(f"  Threshold: {change_threshold}")
    print(f"  PRD limit: {prd_limit_ms} ms")
    print()

    # Find and focus MC window
    print("[1/4] Locating Minecraft window...")
    win_info = WindowManager.find_mc_window()
    if win_info:
        print(f"  Found: {win_info['title']} at ({win_info['left']},{win_info['top']}) "
              f"{win_info['width']}x{win_info['height']}")
        WindowManager.focus_window(win_info)
    else:
        print("  WARNING: No Minecraft window detected. "
              "Ensure MC is running and visible. Proceeding with full-screen capture.")
    print()

    # Initialize components
    print("[2/4] Initializing input injector and detector...")
    injector = InputInjector(key=key)
    detector = LatencyDetector(
        capture_fps=capture_fps,
        roi_width=roi_width,
        roi_height=roi_height,
        change_threshold=change_threshold,
    )
    print()

    # Run trials
    print(f"[3/4] Running {trials} trials...")
    all_latencies: List[float] = []
    trial_details: List[dict] = []

    for i in range(1, trials + 1):
        result = detector.run_trial(injector)
        result.trial_id = i

        if result.success:
            all_latencies.append(result.latency_ms)
            print(f"  Trial {i:3d}/{trials}: {result.latency_ms:7.3f} ms "
                  f"(frames={result.frames_until_change}, "
                  f"delta={result.roi_mean_delta:.1f}, "
                  f"fps={result.actual_capture_fps:.0f})")
        else:
            print(f"  Trial {i:3d}/{trials}: FAILED — {result.error}")

        trial_details.append(asdict(result))

        # Small inter-trial delay to let game state settle
        time.sleep(0.15)

    print()

    # Compute statistics
    print("[4/4] Computing statistics...")
    report.trials_completed = len(all_latencies)
    report.trials_failed = trials - len(all_latencies)
    report.latencies_ms = [round(x, 3) for x in all_latencies]
    report.trial_details = trial_details

    if all_latencies:
        report.median_ms = round(statistics.median(all_latencies), 3)
        report.mean_ms = round(statistics.mean(all_latencies), 3)
        report.std_ms = round(statistics.stdev(all_latencies), 3) if len(all_latencies) > 1 else 0.0
        report.p50_ms = round(percentile(all_latencies, 50), 3)
        report.p95_ms = round(percentile(all_latencies, 95), 3)
        report.p99_ms = round(percentile(all_latencies, 99), 3)
        report.min_ms = round(min(all_latencies), 3)
        report.max_ms = round(max(all_latencies), 3)
        report.prd_pass = report.median_ms <= prd_limit_ms
    else:
        report.median_ms = 0.0
        report.mean_ms = 0.0
        report.std_ms = 0.0
        report.prd_pass = False

    # Print summary
    print()
    print("=" * 60)
    print("  INPUT-TO-FRAME LATENCY REPORT")
    print("=" * 60)
    print(f"  Trials completed : {report.trials_completed}/{report.trials_requested}")
    print(f"  Trials failed    : {report.trials_failed}")
    if all_latencies:
        print(f"  Median latency   : {report.median_ms:.3f} ms")
        print(f"  Mean latency     : {report.mean_ms:.3f} ms")
        print(f"  Std deviation    : {report.std_ms:.3f} ms")
        print(f"  P50              : {report.p50_ms:.3f} ms")
        print(f"  P95              : {report.p95_ms:.3f} ms")
        print(f"  P99              : {report.p99_ms:.3f} ms")
        print(f"  Min              : {report.min_ms:.3f} ms")
        print(f"  Max              : {report.max_ms:.3f} ms")
        print(f"  PRD §3.1 limit   : {prd_limit_ms} ms")
        status = "PASS ✓" if report.prd_pass else "FAIL ✗"
        print(f"  PRD compliance   : {status} (median {report.median_ms:.3f} ms {'≤' if report.prd_pass else '>'} {prd_limit_ms} ms)")
    else:
        print("  No successful trials — cannot compute statistics.")
    print("=" * 60)

    # Write JSON report
    output_path = session_dir / "input_latency_report.json"
    report_dict = asdict(report)
    # Remove raw latencies list from top-level to keep it clean (they're in trial_details)
    # Actually keep them for easy analysis
    with open(output_path, "w") as f:
        json.dump(report_dict, f, indent=2)

    # Also write a compact summary
    summary_path = session_dir / "input_latency_summary.json"
    summary = {
        "median_ms": report.median_ms,
        "mean_ms": report.mean_ms,
        "p95_ms": report.p95_ms,
        "p99_ms": report.p99_ms,
        "prd_pass": report.prd_pass,
        "prd_limit_ms": report.prd_limit_ms,
        "trials_completed": report.trials_completed,
        "trials_failed": report.trials_failed,
        "timestamp_utc": report.timestamp_utc,
    }
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\n  Report written to: {output_path}")
    print(f"  Summary written to: {summary_path}")

    return report


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Measure end-to-end input-to-frame latency (PRD §3.1)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Default: 50 trials, key 'w', 500 Hz capture
  python bin/measure_input_latency.py --session-dir ./latency_results

  # Custom: 100 trials, key 'space', 1000 Hz capture
  python bin/measure_input_latency.py --session-dir ./results --trials 100 \\
      --key space --capture-fps 1000

  # Check against custom PRD limit
  python bin/measure_input_latency.py --session-dir ./results --prd-limit 15
        """,
    )
    parser.add_argument(
        "--session-dir",
        type=Path,
        default=Path("./latency_results"),
        help="Directory to write JSON reports (default: ./latency_results)",
    )
    parser.add_argument(
        "--trials",
        type=int,
        default=DEFAULT_TRIALS,
        help=f"Number of measurement trials (default: {DEFAULT_TRIALS})",
    )
    parser.add_argument(
        "--capture-fps",
        type=int,
        default=DEFAULT_CAPTURE_FPS,
        help=f"Target capture framerate (default: {DEFAULT_CAPTURE_FPS})",
    )
    parser.add_argument(
        "--key",
        type=str,
        default=DEFAULT_KEY,
        help=f"Key to press for synthetic input (default: '{DEFAULT_KEY}')",
    )
    parser.add_argument(
        "--roi-width",
        type=int,
        default=DEFAULT_ROI_WIDTH,
        help=f"Width of detection ROI in pixels (default: {DEFAULT_ROI_WIDTH})",
    )
    parser.add_argument(
        "--roi-height",
        type=int,
        default=DEFAULT_ROI_HEIGHT,
        help=f"Height of detection ROI in pixels (default: {DEFAULT_ROI_HEIGHT})",
    )
    parser.add_argument(
        "--change-threshold",
        type=int,
        default=DEFAULT_CHANGE_THRESHOLD,
        help=f"Per-pixel RGB delta threshold (default: {DEFAULT_CHANGE_THRESHOLD})",
    )
    parser.add_argument(
        "--prd-limit",
        type=float,
        default=DEFAULT_PRD_LIMIT_MS,
        help=f"PRD §3.1 latency limit in ms (default: {DEFAULT_PRD_LIMIT_MS})",
    )

    args = parser.parse_args()

    report = run_measurement_session(
        session_dir=args.session_dir,
        trials=args.trials,
        capture_fps=args.capture_fps,
        key=args.key,
        roi_width=args.roi_width,
        roi_height=args.roi_height,
        change_threshold=args.change_threshold,
        prd_limit_ms=args.prd_limit,
    )

    # Exit with non-zero if PRD check failed
    if not report.prd_pass:
        sys.exit(1)


if __name__ == "__main__":
    main()
