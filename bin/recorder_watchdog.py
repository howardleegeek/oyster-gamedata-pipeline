#!/usr/bin/env python3
"""
Record-time watchdog (Phase 1) — runs concurrent with OysterRecorder.

Detects quality-killing events in real time and aborts/marks the session
before more time is wasted.

Checks every 2 seconds:
  - GetForegroundWindow() == mc_hwnd  (Alt-Tab detect)
  - OysterRecorder.exe still alive    (recorder crash detect)
  - game_state.jsonl position unchanged 60+ ticks → idle warning
  - MC log tail for [Death], [Server thread/INFO]: Player was slain
  - OCR-cheap pixel-sample on 4 known UI zones

Auto-abort thresholds (configurable):
  - alt-tab out > 20s  → DEGRADED
  - pause-menu open > 30s → ABORTED
  - idle > 5 min → ABORTED
  - death screen + stationary > 60s → DEGRADED

Outputs:
  - watchdog_events.jsonl
  - session_grade.json
"""

import ctypes
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Configuration (all thresholds in seconds)
# ---------------------------------------------------------------------------

CONFIG = {
    "poll_interval_s": 2,
    "alt_tab_degraded_threshold_s": 20,
    "pause_menu_abort_threshold_s": 30,
    "idle_abort_threshold_s": 300,       # 5 min
    "idle_warning_ticks": 60,            # ticks before warning
    "death_stationary_degraded_threshold_s": 60,
    "recorder_exe_name": "OysterRecorder.exe",
    "game_state_path": "game_state.jsonl",
    "mc_log_path": "mc_server.log",
    "watchdog_events_path": "watchdog_events.jsonl",
    "session_grade_path": "session_grade.json",
    "ui_zones": {
        "chat":        {"x": 10,  "y": 500, "w": 300, "h": 100},
        "pause_menu":  {"x": 300, "y": 200, "w": 400, "h": 300},
        "death_screen": {"x": 300, "y": 250, "w": 400, "h": 200},
        "inventory":   {"x": 200, "y": 150, "w": 500, "h": 350},
    },
    "base_payout_usd": 10.0,
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [watchdog] %(levelname)s %(message)s",
)
log = logging.getLogger("watchdog")

# ---------------------------------------------------------------------------
# Platform helpers
# ---------------------------------------------------------------------------

_is_windows = sys.platform == "win32"

if _is_windows:
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    GetForegroundWindow = user32.GetForegroundWindow
    GetForegroundWindow.restype = wintypes.HWND

    GetWindowThreadProcessId = user32.GetWindowThreadProcessId
    GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    GetWindowThreadProcessId.restype = wintypes.DWORD

    OpenProcess = kernel32.OpenProcess
    OpenProcess.restype = wintypes.HANDLE
    OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]

    CloseHandle = kernel32.CloseHandle
    CloseHandle.argtypes = [wintypes.HANDLE]
    CloseHandle.restype = wintypes.BOOL

    PROCESS_QUERY_INFORMATION = 0x0400
    PROCESS_VM_READ = 0x0010


def get_foreground_window_pid() -> Optional[int]:
    """Return the PID of the foreground window process."""
    if not _is_windows:
        return None
    hwnd = GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def is_process_alive(pid: int) -> bool:
    """Check if a process with the given PID is still running."""
    if _is_windows:
        handle = OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if handle:
            CloseHandle(handle)
            return True
        return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError as exc:
            log.debug("_is_pid_alive: pid %s check failed: %s", pid, exc)
            return False


def find_mc_hwnd() -> Optional[int]:
    """Find the Minecraft main window handle (Windows only)."""
    if not _is_windows:
        return None
    try:
        import win32gui
        import win32process

        def enum_cb(hwnd, results):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if "Minecraft" in title or "minecraft" in title.lower():
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    results.append((hwnd, pid, title))

        results: list = []
        win32gui.EnumWindows(enum_cb, results)
        if results:
            return results[0][0]
    except ImportError as exc:
        log.debug("win32gui/win32process not available: %s", exc)
    return None


# ---------------------------------------------------------------------------
# Pixel sampling (cheap OCR alternative)
# ---------------------------------------------------------------------------

def sample_ui_zone(zone: Dict[str, int]) -> Optional[bytes]:
    """
    Sample pixels from a known UI zone.
    Returns raw pixel bytes or None if sampling is unavailable.
    On non-Windows or without PIL/mss, returns None (graceful degradation).
    """
    if not _is_windows:
        return None
    try:
        import mss
        with mss.mss() as sct:
            monitor = {
                "top": zone["y"],
                "left": zone["x"],
                "width": zone["w"],
                "height": zone["h"],
            }
            img = sct.grab(monitor)
            return bytes(img.raw)
    except ImportError as exc:
        log.debug("sample_ui_zone: mss not available, pixel sampling disabled: %s", exc)
        return None


def detect_pause_menu_from_pixels(pixel_data: Optional[bytes]) -> bool:
    """
    Heuristic: pause menu has a distinctive dark overlay.
    Check if average brightness of the pause_menu zone is below threshold.
    """
    if pixel_data is None:
        return False
    # BGRA format from mss — check average of B channel
    b_channel = pixel_data[0::4]
    avg_b = sum(b_channel) / len(b_channel) if b_channel else 255
    # Pause menu overlay is typically dark (avg < 80)
    return avg_b < 80


def detect_death_screen_from_pixels(pixel_data: Optional[bytes]) -> bool:
    """
    Heuristic: death screen has red tint and "You died!" text area.
    """
    if pixel_data is None:
        return False
    r_channel = pixel_data[2::4]
    avg_r = sum(r_channel) / len(r_channel) if r_channel else 0
    # Death screen has reddish tint
    return avg_r > 120


def detect_chat_overlay_from_pixels(pixel_data: Optional[bytes]) -> bool:
    """
    Heuristic: chat overlay shows text in bottom-left zone.
    """
    if pixel_data is None:
        return False
    # Chat overlay typically has semi-transparent dark background with white text
    b_channel = pixel_data[0::4]
    variance = sum((b - sum(b_channel) / len(b_channel)) ** 2 for b in b_channel) / len(b_channel)
    # High variance suggests text overlay
    return variance > 500


def detect_inventory_from_pixels(pixel_data: Optional[bytes]) -> bool:
    """
    Heuristic: inventory screen has grid-like pattern.
    """
    if pixel_data is None:
        return False
    g_channel = pixel_data[1::4]
    avg_g = sum(g_channel) / len(g_channel) if g_channel else 0
    # Inventory has greyish background
    return 80 < avg_g < 160


# ---------------------------------------------------------------------------
# Game state reader
# ---------------------------------------------------------------------------

def read_last_position(game_state_path: str) -> Optional[Dict[str, float]]:
    """Read the last line of game_state.jsonl and extract position."""
    try:
        path = Path(game_state_path)
        if not path.exists():
            return None
        with open(path, "r") as f:
            lines = f.readlines()
            if not lines:
                return None
            last_line = lines[-1].strip()
            if not last_line:
                return None
            data = json.loads(last_line)
            pos = data.get("position")
            if pos and isinstance(pos, dict):
                return {
                    "x": float(pos.get("x", 0)),
                    "y": float(pos.get("y", 0)),
                    "z": float(pos.get("z", 0)),
                }
    except (json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
        log.warning("Failed to parse game_state.jsonl line: %s", e)
    return None


def read_mc_log_tail(mc_log_path: str, n_lines: int = 100) -> List[str]:
    """Read the last N lines of the MC server log."""
    try:
        path = Path(mc_log_path)
        if not path.exists():
            return []
        with open(path, "r") as f:
            lines = f.readlines()
            return lines[-n_lines:]
    except Exception as e:
        log.warning("Failed to read MC log %s: %s", mc_log_path, e)
        return []


def detect_death_in_log(log_lines: List[str]) -> bool:
    """Detect death events in MC log lines."""
    death_patterns = [
        "[Death]",
        "Player was slain",
        "Player was killed",
        "fell from a high place",
        "drowned",
        "burned to death",
        "was shot by",
        "was pricked to death",
        "hit the ground too hard",
        "went up in flames",
        "walked into a cactus",
        "was blown up by",
        "starved to death",
        "was killed by",
    ]
    for line in log_lines:
        for pattern in death_patterns:
            if pattern in line:
                return True
    return False


# ---------------------------------------------------------------------------
# Event tracking
# ---------------------------------------------------------------------------

@dataclass
class EventState:
    """Tracks the current state of a monitored event."""
    event_name: str
    started_at: Optional[float] = None
    total_duration_s: float = 0.0
    triggered: bool = False
    severity: str = "warning"  # warning, degraded, aborted


class Watchdog:
    """Main watchdog loop."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or CONFIG
        self.events: List[Dict[str, Any]] = []
        self.mc_hwnd: Optional[int] = None
        self.recorder_pid: Optional[int] = None
        self.running = True
        self.session_grade = "PASS"
        self.grade_reasons: List[str] = []

        # Event states
        self.alt_tab_state = EventState("alt_tab_out")
        self.pause_menu_state = EventState("pause_menu_open")
        self.idle_state = EventState("idle_detected")
        self.death_state = EventState("death_screen")
        self.recorder_crash_state = EventState("recorder_crash")
        self.chat_overlay_state = EventState("chat_overlay")

        # Position tracking for idle detection
        self.last_position: Optional[Dict[str, float]] = None
        self.position_unchanged_ticks = 0
        self.last_log_read_pos = 0

        # Death + stationary tracking
        self.death_detected_at: Optional[float] = None
        self.death_position: Optional[Dict[str, float]] = None

        # Alt-tab tracking
        self.alt_tab_started_at: Optional[float] = None

        # Pause menu tracking
        self.pause_menu_started_at: Optional[float] = None

        # Chat overlay tracking
        self.chat_overlay_started_at: Optional[float] = None

        # Setup signal handlers
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum, frame):
        log.info(f"Received signal {signum}, shutting down watchdog...")
        self.running = False

    def _emit_event(self, event_name: str, duration_s: float, severity: str = "warning"):
        """Emit an event to the watchdog_events.jsonl."""
        event = {
            "t_ns": time.time_ns(),
            "event": event_name,
            "duration_s": round(duration_s, 2),
            "severity": severity,
        }
        self.events.append(event)
        self._write_event(event)
        log.info(f"EVENT: {event_name} duration={duration_s:.1f}s severity={severity}")

    def _write_event(self, event: Dict[str, Any]):
        """Append a single event to watchdog_events.jsonl."""
        path = self.config["watchdog_events_path"]
        with open(path, "a") as f:
            f.write(json.dumps(event) + "\n")

    def _update_grade(self, severity: str, reason: str):
        """Update session grade based on event severity."""
        if severity == "aborted":
            self.session_grade = "ABORTED"
            if reason not in self.grade_reasons:
                self.grade_reasons.append(reason)
        elif severity == "degraded" and self.session_grade != "ABORTED":
            self.session_grade = "DEGRADED"
            if reason not in self.grade_reasons:
                self.grade_reasons.append(reason)
        elif severity == "warning" and self.session_grade == "PASS":
            # Warnings don't change grade but are tracked
            pass

    def _check_alt_tab(self):
        """Check if Minecraft is the foreground window."""
        if not _is_windows:
            # On non-Windows, simulate by checking env var
            mc_visible = os.environ.get("MC_FOREGROUND", "1") == "1"
        else:
            fg_pid = get_foreground_window_pid()
            if fg_pid is None:
                mc_visible = False
            else:
                # Check if foreground process is Minecraft
                mc_visible = False
                try:
                    import psutil
                    proc = psutil.Process(fg_pid)
                    name = proc.name().lower()
                    if "java" in name or "minecraft" in name:
                        mc_visible = True
                except ImportError as exc:
                    # Fallback: check if mc_hwnd matches foreground
                    log.debug("_check_alt_tab: psutil not available, using hwnd fallback: %s", exc)
                    fg_hwnd = GetForegroundWindow()
                    mc_visible = (fg_hwnd == self.mc_hwnd) if self.mc_hwnd else True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    mc_visible = False

        if not mc_visible:
            if self.alt_tab_started_at is None:
                self.alt_tab_started_at = time.time()
                log.info("Alt-tab detected: MC lost focus")
            else:
                duration = time.time() - self.alt_tab_started_at
                if duration >= self.config["alt_tab_degraded_threshold_s"] and not self.alt_tab_state.triggered:
                    self.alt_tab_state.triggered = True
                    self.alt_tab_state.total_duration_s = duration
                    self._emit_event("alt_tab_out", duration, "degraded")
                    self._update_grade("degraded", f"alt_tab_out_{int(duration)}s")
        else:
            if self.alt_tab_started_at is not None:
                duration = time.time() - self.alt_tab_started_at
                if duration > 2:  # Only log if alt-tab was meaningful (>2s)
                    self._emit_event("alt_tab_out", duration, "warning")
                self.alt_tab_started_at = None

    def _check_pause_menu(self):
        """Check if pause menu is open via pixel sampling."""
        zone = self.config["ui_zones"]["pause_menu"]
        pixel_data = sample_ui_zone(zone)
        is_pause = detect_pause_menu_from_pixels(pixel_data)

        if is_pause:
            if self.pause_menu_started_at is None:
                self.pause_menu_started_at = time.time()
                log.info("Pause menu detected")
            else:
                duration = time.time() - self.pause_menu_started_at
                if duration >= self.config["pause_menu_abort_threshold_s"] and not self.pause_menu_state.triggered:
                    self.pause_menu_state.triggered = True
                    self.pause_menu_state.total_duration_s = duration
                    self._emit_event("pause_menu_open", duration, "aborted")
                    self._update_grade("aborted", f"pause_menu_open_{int(duration)}s")
        else:
            if self.pause_menu_started_at is not None:
                duration = time.time() - self.pause_menu_started_at
                if duration > 2:
                    self._emit_event("pause_menu_open", duration, "warning")
                self.pause_menu_started_at = None

    def _check_idle(self):
        """Check if player has been idle (position unchanged)."""
        current_pos = read_last_position(self.config["game_state_path"])
        if current_pos is None:
            return

        if self.last_position is not None:
            dx = abs(current_pos["x"] - self.last_position["x"])
            dy = abs(current_pos["y"] - self.last_position["y"])
            dz = abs(current_pos["z"] - self.last_position["z"])
            distance = (dx + dy + dz)

            if distance < 0.01:  # Essentially stationary
                self.position_unchanged_ticks += 1
            else:
                if self.position_unchanged_ticks >= self.config["idle_warning_ticks"]:
                    idle_duration = self.position_unchanged_ticks * self.config["poll_interval_s"]
                    self._emit_event("idle_detected", idle_duration, "warning")
                self.position_unchanged_ticks = 0
                self.idle_state.started_at = None
        else:
            self.position_unchanged_ticks = 0

        self.last_position = current_pos

        # Check idle abort threshold
        if self.position_unchanged_ticks >= self.config["idle_warning_ticks"]:
            idle_duration = self.position_unchanged_ticks * self.config["poll_interval_s"]
            if idle_duration >= self.config["idle_abort_threshold_s"] and not self.idle_state.triggered:
                self.idle_state.triggered = True
                self.idle_state.total_duration_s = idle_duration
                self._emit_event("idle_detected", idle_duration, "aborted")
                self._update_grade("aborted", f"idle_{int(idle_duration)}s")

    def _check_death(self):
        """Check MC log for death events and track stationary time after death."""
        log_lines = read_mc_log_tail(self.config["mc_log_path"])
        death_in_log = detect_death_in_log(log_lines)

        # Also check death screen via pixel sampling
        zone = self.config["ui_zones"]["death_screen"]
        pixel_data = sample_ui_zone(zone)
        death_screen_visible = detect_death_screen_from_pixels(pixel_data)

        if death_in_log or death_screen_visible:
            if self.death_detected_at is None:
                self.death_detected_at = time.time()
                self.death_position = self.last_position
                log.info("Death detected")
                self._emit_event("death_detected", 0, "warning")
            else:
                duration = time.time() - self.death_detected_at
                # Check if stationary after death
                if self.last_position and self.death_position:
                    dx = abs(self.last_position["x"] - self.death_position["x"])
                    dy = abs(self.last_position["y"] - self.death_position["y"])
                    dz = abs(self.last_position["z"] - self.death_position["z"])
                    if (dx + dy + dz) < 0.1:
                        if duration >= self.config["death_stationary_degraded_threshold_s"] and not self.death_state.triggered:
                            self.death_state.triggered = True
                            self.death_state.total_duration_s = duration
                            self._emit_event("death_stationary", duration, "degraded")
                            self._update_grade("degraded", f"death_stationary_{int(duration)}s")
        else:
            if self.death_detected_at is not None:
                duration = time.time() - self.death_detected_at
                if duration > 5:
                    self._emit_event("death_resolved", duration, "info")
                self.death_detected_at = None
                self.death_position = None

    def _check_recorder_alive(self):
        """Check if OysterRecorder.exe is still running."""
        if self.recorder_pid is None:
            # Try to find the recorder process
            try:
                import psutil
                for proc in psutil.process_iter(["name"]):
                    try:
                        if self.config["recorder_exe_name"].lower() in (proc.info["name"] or "").lower():
                            self.recorder_pid = proc.pid
                            log.info(f"Found recorder PID: {self.recorder_pid}")
                            break
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
            except ImportError as exc:
                # Without psutil, we can't check — assume alive
                log.debug("_check_recorder_alive: psutil not available, assuming recorder alive: %s", exc)
                return

        if self.recorder_pid is not None:
            if not is_process_alive(self.recorder_pid):
                if not self.recorder_crash_state.triggered:
                    self.recorder_crash_state.triggered = True
                    self._emit_event("recorder_crash", 0, "aborted")
                    self._update_grade("aborted", "recorder_crash")
                    log.critical("OysterRecorder.exe is no longer running!")

    def _check_chat_overlay(self):
        """Check if chat overlay is covering the screen."""
        zone = self.config["ui_zones"]["chat"]
        pixel_data = sample_ui_zone(zone)
        chat_visible = detect_chat_overlay_from_pixels(pixel_data)

        if chat_visible:
            if self.chat_overlay_started_at is None:
                self.chat_overlay_started_at = time.time()
                log.info("Chat overlay detected")
            else:
                duration = time.time() - self.chat_overlay_started_at
                if duration >= 30 and not self.chat_overlay_state.triggered:
                    self.chat_overlay_state.triggered = True
                    self.chat_overlay_state.total_duration_s = duration
                    self._emit_event("chat_overlay", duration, "degraded")
                    self._update_grade("degraded", f"chat_overlay_{int(duration)}s")
        else:
            if self.chat_overlay_started_at is not None:
                duration = time.time() - self.chat_overlay_started_at
                if duration > 2:
                    self._emit_event("chat_overlay", duration, "warning")
                self.chat_overlay_started_at = None

    def write_session_grade(self):
        """Write session_grade.json at end of session."""
        grade = self.session_grade
        if grade == "ABORTED":
            final_grade = "FAIL"
            payout = 0.0
        elif grade == "DEGRADED":
            final_grade = "DEGRADED"
            payout = self.config["base_payout_usd"] * 0.5
        else:
            final_grade = "PASS"
            payout = self.config["base_payout_usd"]

        grade_data = {
            "grade": final_grade,
            "reasons": self.grade_reasons,
            "estimated_payout_usd": payout,
            "events_count": len(self.events),
            "session_duration_s": round(time.time() - self._start_time, 2) if hasattr(self, "_start_time") else 0,
        }

        path = self.config["session_grade_path"]
        with open(path, "w") as f:
            json.dump(grade_data, f, indent=2)
        log.info(f"Session grade written: {grade_data}")
        return grade_data

    def run(self):
        """Main watchdog loop."""
        self._start_time = time.time()
        log.info("Watchdog starting...")
        log.info(f"Poll interval: {self.config['poll_interval_s']}s")
        log.info(f"Alt-tab degraded threshold: {self.config['alt_tab_degraded_threshold_s']}s")
        log.info(f"Pause menu abort threshold: {self.config['pause_menu_abort_threshold_s']}s")
        log.info(f"Idle abort threshold: {self.config['idle_abort_threshold_s']}s")
        log.info(f"Death stationary degraded threshold: {self.config['death_stationary_degraded_threshold_s']}s")

        # Find MC window
        if _is_windows:
            self.mc_hwnd = find_mc_hwnd()
            if self.mc_hwnd:
                log.info(f"Found Minecraft window: hwnd={self.mc_hwnd}")
            else:
                log.warning("Could not find Minecraft window handle")

        # Clear previous events file
        events_path = self.config["watchdog_events_path"]
        open(events_path, "w").close()  # truncate

        try:
            while self.running:
                self._check_alt_tab()
                self._check_pause_menu()
                self._check_idle()
                self._check_death()
                self._check_recorder_alive()
                self._check_chat_overlay()

                time.sleep(self.config["poll_interval_s"])
        except KeyboardInterrupt:
            log.info("Watchdog interrupted by user")
        finally:
            self.write_session_grade()
            log.info("Watchdog stopped")


def main():
    """Entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Record-time watchdog for Minecraft sessions")
    parser.add_argument("--config", type=str, help="Path to JSON config file")
    parser.add_argument("--game-state", type=str, default=None, help="Path to game_state.jsonl")
    parser.add_argument("--mc-log", type=str, default=None, help="Path to MC server log")
    parser.add_argument("--output-dir", type=str, default=".", help="Output directory for events and grade")
    parser.add_argument("--poll-interval", type=float, default=None, help="Poll interval in seconds")
    parser.add_argument("--base-payout", type=float, default=None, help="Base payout in USD")

    args = parser.parse_args()

    config = dict(CONFIG)

    if args.config:
        with open(args.config, "r") as f:
            user_config = json.load(f)
        config.update(user_config)

    if args.game_state:
        config["game_state_path"] = args.game_state
    if args.mc_log:
        config["mc_log_path"] = args.mc_log
    if args.poll_interval:
        config["poll_interval_s"] = args.poll_interval
    if args.base_payout:
        config["base_payout_usd"] = args.base_payout

    # Ensure output paths are relative to output_dir
    output_dir = args.output_dir
    config["watchdog_events_path"] = os.path.join(output_dir, config["watchdog_events_path"])
    config["session_grade_path"] = os.path.join(output_dir, config["session_grade_path"])

    watchdog = Watchdog(config)
    watchdog.run()


if __name__ == "__main__":
    main()
