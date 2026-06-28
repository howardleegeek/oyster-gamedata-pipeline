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

    Looks in standard install layouts, in priority order:

        1. <INSTALL_ROOT>/OysterRecorder-onedir/OysterRecorder-onedir.exe
           — the layout R05E actually ships (PyInstaller --onedir with
           `--name OysterRecorder-onedir` produces this naming + path).
        2. <INSTALL_ROOT>/recorder/OysterRecorder.exe
        3. <INSTALL_ROOT>/OysterRecorder.exe
        4. sibling of the running executable (when run from a
           PyInstaller bundle — OysterPlay.exe is dropped at {app})

    Bug 5 (R05E): the .exe is shipped under
    `OysterRecorder-onedir/OysterRecorder-onedir.exe`, NOT
    `OysterRecorder.exe` directly under {app}. Without the onedir
    candidates, the launcher errored::

        OysterRecorder.exe not found near <INSTALL_ROOT>
    """
    candidates: list[Path] = [
        # Onedir layout (current R05E ship layout) — check first.
        install_root_path / RECORDER_ONEDIR_DIR / RECORDER_ONEDIR_EXE,
        # Legacy / hand-installed layouts kept as fallbacks.
        install_root_path / "recorder" / RECORDER_EXE_NAME,
        install_root_path / RECORDER_EXE_NAME,
    ]

    # When bundled, sys.executable is OysterPlay.exe — recorder may be
    # in the same dir under either layout.
    exe = Path(sys.executable).resolve()
    candidates.append(exe.parent / RECORDER_ONEDIR_DIR / RECORDER_ONEDIR_EXE)
    candidates.append(exe.parent / RECORDER_EXE_NAME)
    candidates.append(exe.parent / "recorder" / RECORDER_EXE_NAME)

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
    # CREATE_NEW_PROCESS_GROUP so the recorder lives independently.
    return subprocess.Popen(
        [str(recorder_exe)],
        cwd=str(recorder_exe.parent),
        creationflags=0x00000200,  # CREATE_NEW_PROCESS_GROUP
    )


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
        ps = (
            f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{lnk_path}'); "
            f"$s.TargetPath='{target_exe}'; "
            f"$s.WorkingDirectory='{target_exe.parent}'; "
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

    # Step 1: install integrity check
    status = launcher.verify_install(install_root_path, profile_name)
    if not status.ok:
        sess.failure_reason = (
            "Install incomplete. Please reinstall.\nMissing:\n  " +
            "\n  ".join(status.missing)
        )
        if not dry_run:
            launcher._show_messagebox(  # noqa: SLF001 — internal helper
                "Oyster Recorder — install corrupted", sess.failure_reason,
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
        sess.failure_reason = f"Could not build launch command: {e}"
        if not dry_run:
            launcher._show_messagebox(  # noqa: SLF001
                "Oyster Recorder — launch failed", sess.failure_reason,
            )
        return sess
    sess.plan = plan

    if dry_run:
        return sess

    # Step 3: ensure recorder is up
    recorder = find_recorder_exe(install_root_path)
    if recorder is None:
        sess.failure_reason = (
            f"OysterRecorder.exe not found near {install_root_path}. "
            "Please reinstall."
        )
        launcher._show_messagebox(  # noqa: SLF001
            "Oyster Recorder — recorder missing", sess.failure_reason,
        )
        return sess
    sess.recorder_proc = spawn_recorder(recorder)

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

    return sess


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Run the Oyster single-button consumer launcher CLI.

    Parses command-line arguments, optionally writes a desktop shortcut,
    and launches the game session.

    Args:
        argv: Command-line arguments (default: sys.argv). Pass an empty
              list to run with defaults.

    Returns:
        Exit code: 0 on success, 1 on failure.
    """
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
    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    root = launcher.install_root(args.install_root)

    if args.write_desktop_shortcut:
        # Sibling exe assumed to be OysterPlay.exe (when bundled).
        target = Path(sys.executable).resolve()
        path = write_desktop_shortcut(target_exe=target)
        if path:
            print(f"Desktop shortcut: {path}")
            return 0
        return 1

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
