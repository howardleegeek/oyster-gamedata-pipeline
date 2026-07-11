#!/usr/bin/env python3
"""
bin/recorder_consumer_lite.py — Oyster Recorder Lite (stop-gap MVP)

Howard 2026-05-05: "直接下载 然后 打开 然后 打开minecraft 直接测试"
                   "他们的流程就是什么都不需要设置"
                   "我需要快 出release"

This is a STOP-GAP single-.exe recorder so Howard's testers can run the
"download → open → play MC → get clip" loop TODAY, while Howard's team
finishes the Rust gamedata-recorder full refactor (per
~/Downloads/plans/polished-gathering-sifakis.md Phase 0-3).

What it does:
  1. Tester double-clicks OysterRecorder.exe
  2. Window opens with: "请打开 Minecraft 开始游戏，自动开始录制"
  3. Background thread polls Windows process list every 2s for
     javaw.exe (Minecraft Java launcher) or Minecraft.exe (Bedrock)
  4. When MC detected → spawns bundled ffmpeg.exe to record the
     Minecraft window with H.265, saving to
     %USERPROFILE%\\Documents\\OysterClips\\clip-YYYYMMDD-HHMMSS.mp4
  5. Window updates: "正在录制 — 玩你的 Minecraft 即可"
  6. When MC process exits → kills ffmpeg → finalizes mp4 →
     window shows: "✓ 录制完成 — 文件已保存"
  7. Done. Tester closes the window.

What it INTENTIONALLY does NOT do (deferred to Howard's team's Rust app):
  - Audio extractor (G196)
  - Per-frame depth files (G198 shader pack)
  - 20-field action_camera.json (G164 intrinsics + G163 keycode)
  - gameinfo.xlsx scene metadata
  - 5-file PRD tarball — output is just .mp4 for now

  The validator (qa-validator-v0.2.0+) will say "✗ FAIL" on these clips
  because they're missing PRD fields. That's fine for stop-gap — testers
  see "recording works", engineers see "still missing X/Y/Z fields".
  Howard's team's Rust app produces full PRD-compliant tarballs.

Built into a single Windows .exe by .github/workflows/build-recorder-exe.yml
using PyInstaller --onefile --windowed with bundled ffmpeg.exe (added via
--add-binary). No Python, no admin, no extra installs needed by tester.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# ---- Startup tracing (runs BEFORE any heavyweight import) -----------------
# Howard tester 2026-05-05: "反馈过来一点就闪退" — v0.1.0 silently crashed
# on launch with --windowed (which swallows stderr). To diagnose, every
# import + init step writes a single line to ~/OysterRecorder.log BEFORE
# any GUI work. Tester can email/Slack that file even after the .exe is
# gone. The log is also tail-printed in the messagebox if Tk is alive.
_STARTUP_LOG = Path.home() / "OysterRecorder.log"


def _trace(step: str) -> None:
    try:
        with _STARTUP_LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now().isoformat(timespec='seconds')} {step}\n")
    except Exception:
        # Even logging failed — nothing more we can do this early.
        pass


_trace("=== OysterRecorder boot ===")
_trace(f"sys.executable={sys.executable}")
_trace(f"sys.frozen={getattr(sys, 'frozen', False)}")
_trace(f"sys.platform={sys.platform}")
_trace(f"os.name={os.name}")

# Bumped on every release — used by self-update logic.
# CRITICAL: this MUST match the recorder-vX.Y.Z tag we're publishing
# under. Out-of-sync versions cause v0.13 onedir installs to think
# they're v0.8 and "update" themselves to v0.9 single-file, breaking
# the bundled _internal/ layout. See v0.14.0 commit for postmortem.
RECORDER_VERSION = "lite-v0.28.0-rc9"

# R01 iron-law: supported MC versions for real game-state Fabric mod.
# Kept in sync with .github/workflows/build-mc-mod.yml matrix.
SUPPORTED_MC_VERSIONS = [
    "1.20.1", "1.20.2", "1.20.4", "1.20.6",
    "1.21.1", "1.21.2", "1.21.3", "1.21.4", "1.21.5",
]


class RecorderError(RuntimeError):
    """Hard-fail error for iron-law violations (no silent fallback)."""
RELEASES_API = (
    "https://api.github.com/repos/howardleegeek/oyster-gamedata-pipeline"
    "/releases?per_page=20"
)


def _is_onedir_install() -> bool:
    """Detect whether we're running as a PyInstaller --onedir bundle.

    --onedir bundles have a ``_internal/`` directory next to the .exe
    holding all DLLs. --onefile extracts to a temp dir and the .exe
    sits alone. Self-update can ONLY safely overwrite same-format
    bundles (onefile→onefile or onedir→onedir-zip), so this gate
    prevents an onedir install from being clobbered by a single-file
    .exe download.
    """
    if not getattr(sys, "frozen", False):
        return False
    exe = Path(sys.executable).resolve()
    return (exe.parent / "_internal").is_dir()


# ---- Self-update (engineer ships once, recorder updates itself) ---------
# Howard 2026-05-05: "能不能 自动给这个电脑上的更新"
# On launch (and once an hour while running), check the GitHub Releases
# API for the latest `recorder-v*` tag. If a newer version exists,
# download the new .exe to %TEMP%, then write a tiny Windows .bat that
# (a) waits 3s for us to fully exit, (b) copies the new .exe over our
# path, (c) re-launches us. We spawn the .bat detached and quit.
# Tester sees the recorder briefly close + reopen — every ~5 min when
# we ship a new release, never has to touch the file manually again.

def _current_version_tag() -> str:
    """Return the recorder-vX.Y.Z tag derived from RECORDER_VERSION."""
    # RECORDER_VERSION = "lite-v0.8.0" → tag = "recorder-v0.8.0"
    semver = RECORDER_VERSION.split("-", 1)[-1]  # 'v0.8.0'
    return f"recorder-{semver}"


def _latest_release_tag_and_url() -> tuple[Optional[str], Optional[str]]:
    """Query GitHub API for the latest recorder-v* release. Returns
    (tag, exe_download_url) or (None, None) on any failure (network,
    rate-limit, etc).
    """
    try:
        import urllib.request
        req = urllib.request.Request(
            RELEASES_API,
            headers={"User-Agent": "OysterRecorder/lite", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            releases = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        _trace(f"update_check: api error {e}")
        return None, None
    for rel in releases:
        tag = rel.get("tag_name", "")
        if not tag.startswith("recorder-v"):
            continue
        if rel.get("draft") or rel.get("prerelease"):
            continue
        for asset in rel.get("assets", []):
            if asset.get("name") == "OysterRecorder.exe":
                return tag, asset.get("browser_download_url")
    return None, None


def _is_newer_tag(latest: str, current: str) -> bool:
    """Compare recorder-vA.B.C semver-ish tags. Returns True if latest > current."""
    def _key(t: str) -> tuple[int, ...]:
        v = t.replace("recorder-v", "").split(".")
        out = []
        for piece in v:
            try:
                out.append(int(piece))
            except ValueError:
                out.append(0)
        return tuple(out)
    return _key(latest) > _key(current)


def _stage_self_update(new_exe_url: str) -> bool:
    """Download the new .exe and write a self-replacing .bat.

    Returns True if the update was staged (caller should exit so the
    .bat can take over). Returns False on any failure (no harm, recorder
    keeps running on the current version).

    v0.14.0 GUARD: refuses to self-update an --onedir install with a
    single-file .exe. The single-file .exe assumes %TEMP% extraction
    and overwrites our bootstrap, leaving the _internal/ folder
    orphaned and crashing the next launch with PYI_APPLICATION_HOME_DIR.
    """
    # v0.20.1: fail-loud diagnostic trace at every gate (was silently False)
    if os.name != "nt":
        _trace(f"update: SKIP — non-Windows OS ({os.name})")
        return False
    if not getattr(sys, "frozen", False):
        _trace("update: SKIP — not running as packaged .exe (dev mode)")
        return False
    if _is_onedir_install():
        _trace("update: SKIP — onedir bundle, refuses single-.exe overwrite (would orphan _internal/)")
        return False
    try:
        import urllib.request
        new_path = Path(tempfile.gettempdir()) / "OysterRecorder-update.exe"
        _trace(f"update: downloading {new_exe_url} -> {new_path}")
        with urllib.request.urlopen(new_exe_url, timeout=120) as resp, \
                new_path.open("wb") as fh:
            shutil.copyfileobj(resp, fh)
        size = new_path.stat().st_size
        _trace(f"update: downloaded {size} bytes")
        if size < 1_000_000:  # under 1 MB is suspicious — likely a 4xx error page
            _trace(f"update: ABORT — downloaded file too small ({size} bytes), likely error page not exe")
            return False
    except Exception as e:
        # v0.20.1: explicit exception type for diagnostic clarity
        _trace(f"update: download failed [{type(e).__name__}]: {e}")
        return False

    current_exe = Path(sys.executable)
    bat_path = Path(tempfile.gettempdir()) / "OysterRecorder-update.bat"
    bat_body = (
        "@echo off\r\n"
        "rem Wait for the recorder to fully exit before swapping.\r\n"
        "timeout /t 3 /nobreak > nul\r\n"
        f'move /Y "{new_path}" "{current_exe}"\r\n'
        f'start "" "{current_exe}"\r\n'
        f'del "{bat_path}"\r\n'
    )
    try:
        bat_path.write_text(bat_body, encoding="ascii")
        # v0.15.0: add CREATE_NO_WINDOW (0x08000000) so the cmd.exe
        # invocation doesn't FLASH a black console window on screen.
        # DETACHED_PROCESS (0x08) alone only detaches from the parent's
        # console — Windows still creates a fresh visible one for cmd.
        # CREATE_NO_WINDOW + CREATE_NEW_PROCESS_GROUP (0x200) combined
        # give us: no visible window, no parent console, survives parent
        # exit.
        subprocess.Popen(
            ["cmd.exe", "/c", str(bat_path)],
            creationflags=0x08 | 0x200 | 0x08000000,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _trace(f"update: staged via {bat_path}")
        return True
    except Exception as e:
        _trace(f"update: stage failed {e}")
        return False


def _check_for_update_in_background(on_done=None) -> None:
    """Fire-and-forget update check. Calls on_done(tag, url, is_newer)."""
    def _go():
        latest_tag, exe_url = _latest_release_tag_and_url()
        current_tag = _current_version_tag()
        if not latest_tag:
            _trace("update_check: no release returned by API")
            if on_done:
                on_done(None, None, False)
            return
        is_newer = _is_newer_tag(latest_tag, current_tag)
        _trace(f"update_check: current={current_tag} latest={latest_tag} newer={is_newer}")
        if on_done:
            on_done(latest_tag, exe_url, is_newer)
    threading.Thread(target=_go, daemon=True).start()


# ---- Remote telemetry (so engineer can see logs without tester action) ---
# Howard 2026-05-05: "你这边 能不能有log 到信息和日志"
# v0.20.1 hotfix: ix.io is DEAD (offline since 2024). Switch to 0x0.st,
# which is alive, free, no-auth, accepts multipart file uploads.
# Always write a local diagnostic zip to Desktop as fallback so the tester
# can manually send via WeChat/email even if both endpoints fail.
TELEMETRY_ENDPOINT = "https://0x0.st"  # multipart POST with field name "file"
DIAGNOSTIC_ZIP_NAME = "OysterRecorder_diagnostic.zip"


def _desktop_path() -> Path:
    """Return path to user's Desktop, falling back to home if not present."""
    desktop = Path.home() / "Desktop"
    if desktop.exists():
        return desktop
    # 中文 Windows 桌面 may localize — try OneDrive Desktop too
    onedrive = Path.home() / "OneDrive" / "Desktop"
    if onedrive.exists():
        return onedrive
    return Path.home()


def _build_diagnostic_zip() -> Optional[Path]:
    """Build a diagnostic zip on the Desktop containing log + system info.

    Tester can send this zip via WeChat / email to engineer when remote
    upload fails. Returns the zip path on success, None on failure.
    """
    try:
        import zipfile, platform
        zip_path = _desktop_path() / DIAGNOSTIC_ZIP_NAME
        sys_info_lines = [
            f"recorder_version: {RECORDER_VERSION}",
            f"timestamp: {datetime.now().isoformat()}",
            f"platform: {platform.platform()}",
            f"python: {sys.version}",
            f"frozen: {getattr(sys, 'frozen', False)}",
            f"is_onedir: {_is_onedir_install() if getattr(sys, 'frozen', False) else 'N/A'}",
            f"sys.executable: {sys.executable}",
            f"home: {Path.home()}",
            f"log_file: {_STARTUP_LOG}",
            f"log_exists: {_STARTUP_LOG.exists()}",
            f"log_size_bytes: {_STARTUP_LOG.stat().st_size if _STARTUP_LOG.exists() else 0}",
        ]
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("sysinfo.txt", "\n".join(sys_info_lines))
            if _STARTUP_LOG.exists():
                zf.write(_STARTUP_LOG, "OysterRecorder.log")
        _trace(f"diagnostic_zip: built {zip_path}")
        return zip_path
    except Exception as exc:
        _trace(f"diagnostic_zip: build failed: {exc}")
        return None


def _upload_log_remote() -> Optional[str]:
    """POST ~/OysterRecorder.log to 0x0.st. Returns short URL or None.

    v0.20.1 hotfix: previously POSTed to ix.io which has been offline since
    2024. Now uses 0x0.st (multipart upload, anonymous, free, alive).
    Whether or not the remote upload succeeds, _build_diagnostic_zip() is
    also called so the tester always has a local fallback.
    """
    if not _STARTUP_LOG.exists():
        _trace("upload_log: no local log file yet")
        return None
    try:
        body = _STARTUP_LOG.read_text(encoding="utf-8", errors="replace").encode("utf-8")
    except Exception as exc:
        _trace(f"upload_log: read failed: {exc}")
        return None
    if len(body) > 5_000_000:  # 0x0.st limit ~512MB but trim aggressively
        body = body[-1_000_000:]
    try:
        # 0x0.st expects multipart/form-data with field name "file".
        # Build a minimal multipart body with stdlib (no `requests` dep).
        import urllib.request
        import uuid
        boundary = f"----OysterBoundary{uuid.uuid4().hex}"
        crlf = "\r\n"
        head = (
            f"--{boundary}{crlf}"
            f'Content-Disposition: form-data; name="file"; filename="OysterRecorder.log"{crlf}'
            f"Content-Type: text/plain{crlf}{crlf}"
        ).encode("utf-8")
        tail = f"{crlf}--{boundary}--{crlf}".encode("utf-8")
        data = head + body + tail
        req = urllib.request.Request(
            TELEMETRY_ENDPOINT,
            data=data,
            headers={
                "User-Agent": "OysterRecorder/lite (engineer-contact)",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            url = resp.read().decode("utf-8", errors="replace").strip()
        if url.startswith("http"):
            _trace(f"upload_log: success {url}")
            try:
                with _STARTUP_LOG.open("a", encoding="utf-8") as fh:
                    fh.write(f"{datetime.now().isoformat()} REMOTE_LOG_URL={url}\n")
            except Exception:
                pass
            return url
        _trace(f"upload_log: server returned non-URL response: {url[:200]}")
    except Exception as exc:
        # v0.20.1: explicit exception type + message for diagnostic clarity
        _trace(f"upload_log: POST failed [{type(exc).__name__}]: {exc}")
    return None


def _upload_log_in_background(callback=None) -> None:
    """Fire-and-forget upload; optional callback receives the URL or None."""
    def _go():
        url = _upload_log_remote()
        if callback is not None:
            try:
                callback(url)
            except Exception:
                pass
    threading.Thread(target=_go, daemon=True).start()

try:
    _trace("importing tkinter…")
    import tkinter as tk
    from tkinter import messagebox, ttk  # ttk: rc9 depth-progress bar
    _trace("tkinter ok")
except Exception:
    _trace(f"tkinter FAILED:\n{traceback.format_exc()}")
    raise

# pynput is lazily imported in InputCapture.start() so that startup of
# the .exe doesn't fail if pynput's hooks misbehave on a tester's box.
# PyInstaller still picks it up because we --hidden-import it in the
# workflow.

# When PyInstaller-frozen, ffmpeg.exe lives in sys._MEIPASS.
if getattr(sys, "frozen", False):
    _BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", "."))
else:
    _BUNDLE_ROOT = Path(__file__).resolve().parent

# Path to bundled ffmpeg binary. On Windows this is ffmpeg.exe; on
# non-frozen runs (developer testing on macOS / Linux) we fall back to
# whatever ffmpeg is on PATH.
_FFMPEG = _BUNDLE_ROOT / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
if not _FFMPEG.exists():
    found = shutil.which("ffmpeg")
    _FFMPEG = Path(found) if found else _FFMPEG  # may not exist on dev box

# Tester output directory: ~/Documents/OysterClips/
#
# rc8 fix: on Windows, "Documents" can be redirected to OneDrive (common
# default on consumer boxes). When it is, `Path.home() / "Documents"` still
# returns the un-redirected NTFS path, but Explorer's sidebar "Documents"
# shortcut points at the redirected OneDrive path — so the tester opens
# Explorer, clicks Documents, sees no OysterClips folder, panics. We resolve
# the *real* Documents path from the registry (User Shell Folders\Personal)
# the same way Explorer does, so files land where the tester actually looks.
def _real_documents_dir() -> Path:
    if os.name != "nt":
        return Path.home() / "Documents"
    try:
        import winreg  # noqa: PLC0415 — Windows-only stdlib
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as k:
            raw, _ = winreg.QueryValueEx(k, "Personal")
            return Path(os.path.expandvars(raw))
    except OSError:
        return Path.home() / "Documents"


def _output_dir() -> Path:
    docs = _real_documents_dir() / "OysterClips"
    docs.mkdir(parents=True, exist_ok=True)
    return docs


def _detect_gpu_available() -> bool:
    """Return True if any GPU-accelerated depth inference path is reachable.

    rc9 (Howard 2026-05-09): testers running on integrated-GPU laptops sit
    through 30-60 minutes of CPU DepthAnything inference and assume the
    recorder is hung. We detect available accelerators here so the UI can
    pre-check the "Skip depth maps" button on no-GPU boxes (the tester
    can still uncheck it manually if they want to wait).

    Detection order on Windows (most → least preferred):
      1. NVIDIA CUDA — try ``ctypes.WinDLL("nvcuda.dll")`` then
         ``cuInit(0) == 0``. Loading nvcuda.dll alone isn't sufficient
         because Windows ships the DLL stub even on Intel-only boxes.
      2. DirectML / DXGI hardware adapter — every Win10+ machine has
         ``dxgi.dll``, but a discrete GPU is implied by ``torch_directml``
         being importable (we only ship it in the DML build).

    Returns False on non-Windows (the recorder is Windows-only in
    production; on dev macOS we just default to "GPU available"-style
    behaviour by returning True since the integrated Apple GPU is fast
    enough for testing). On unexpected exceptions we return False — the
    UI prefers "default to skip" over a falsely promising progress bar.
    """
    if os.name != "nt":
        # Dev / smoke-test path: macOS/Linux contributors testing the
        # recorder still want the depth UI exercised. Return True so the
        # skip button is NOT pre-checked. Real tester boxes are Windows.
        return True
    try:
        import ctypes  # noqa: PLC0415
    except Exception:
        return False
    # 1. NVIDIA path
    try:
        nvcuda = ctypes.WinDLL("nvcuda.dll")  # type: ignore[attr-defined]
        cu_init = getattr(nvcuda, "cuInit", None)
        if cu_init is not None:
            try:
                # cuInit returns 0 (CUDA_SUCCESS) on a real CUDA-capable box.
                # Anything else (CUDA_ERROR_NO_DEVICE, etc) means no GPU.
                rc = int(cu_init(0))
                if rc == 0:
                    return True
            except Exception:
                pass
    except OSError:
        # nvcuda.dll not present at all — no NVIDIA driver installed.
        pass
    except Exception:
        pass
    # 2. DirectML path — only meaningful if torch-directml made it into the
    # bundle. Pure dxgi.dll presence isn't sufficient (every Win10+ has it).
    try:
        import importlib.util  # noqa: PLC0415

        if importlib.util.find_spec("torch_directml") is not None:
            return True
    except Exception:
        pass
    return False


# Process names treated as "Minecraft" — both Java and Bedrock variants.
MC_PROCESS_NAMES = {"javaw.exe", "java.exe", "Minecraft.exe", "MinecraftLauncher.exe"}


def _get_minecraft_window_rect() -> Optional[dict[str, int]]:
    """Return Minecraft window geometry on Windows, or None if not found.

    Uses Win32 EnumWindows + GetWindowText + GetWindowRect via ctypes
    (no extra deps, built into Python). We scan all top-level windows
    and pick the first whose title contains 'Minecraft'. PRD requires
    gameProcessName / x / y / width / height / recordDpi (criterion 8),
    so this powers the real systeminfo.json.

    Returns None on non-Windows or if no MC window is visible yet.
    """
    if os.name != "nt":
        return None

    try:
        import ctypes
        import ctypes.wintypes as wt
    except Exception:
        return None

    user32 = ctypes.windll.user32

    EnumWindows = user32.EnumWindows
    GetWindowText = user32.GetWindowTextW
    GetWindowTextLength = user32.GetWindowTextLengthW
    IsWindowVisible = user32.IsWindowVisible
    GetWindowRect = user32.GetWindowRect
    GetDpiForWindow = getattr(user32, "GetDpiForWindow", None)

    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    found_hwnd: list[int] = []
    found_title: list[str] = []

    def _callback(hwnd, _lparam):
        if not IsWindowVisible(hwnd):
            return True
        ln = GetWindowTextLength(hwnd)
        if ln == 0:
            return True
        buf = ctypes.create_unicode_buffer(ln + 1)
        GetWindowText(hwnd, buf, ln + 1)
        title = buf.value
        if "minecraft" in title.lower():
            found_hwnd.append(hwnd)
            found_title.append(title)
            return False  # stop iteration
        return True

    EnumWindows(EnumWindowsProc(_callback), 0)

    if not found_hwnd:
        return None

    rect = wt.RECT()
    if not GetWindowRect(found_hwnd[0], ctypes.byref(rect)):
        return None

    dpi = 96
    if GetDpiForWindow is not None:
        try:
            dpi = int(GetDpiForWindow(found_hwnd[0])) or 96
        except Exception:
            dpi = 96

    return {
        "title": found_title[0],
        "x": int(rect.left),
        "y": int(rect.top),
        "width": int(rect.right - rect.left),
        "height": int(rect.bottom - rect.top),
        "recordDpi": dpi,
    }


import re as _re  # noqa: E402

_MC_VERSION_RE = _re.compile(r"Minecraft\s+([\d.]+)")


def _parse_mc_version_from_title(title: str) -> Optional[str]:
    """Extract Minecraft version from window title (any locale).

    Works for titles like:
      - "Minecraft 1.21.4"
      - "Minecraft 1.21.4 - 单人游戏"
      - "Minecraft 1.21.4 - シングルプレイ"
    """
    m = _MC_VERSION_RE.search(title)
    return m.group(1) if m else None


def _recorder_version_tuple() -> tuple[int, ...]:
    """Parse RECORDER_VERSION into a comparable numeric tuple.

    "lite-v0.27.0-iron-law-strict" → (0, 27, 0)
    """
    import re  # noqa: PLC0415
    nums = re.findall(r"\d+", RECORDER_VERSION.split("-v")[-1].split("-")[0])
    return tuple(int(n) for n in nums)


class InputCapture:
    """Lightweight keyboard + mouse capture into a JSON Lines buffer.

    Each event is a dict with: timestamp_ms (relative to start), event_type
    (key_down / key_up / mouse_move / mouse_click), and event-specific
    fields (keyCode int, mouseX, mouseY, button). Used to populate
    action_camera.json's `records` array (real data for the keyboard +
    mouse fields; camera/quaternion fields stay placeholder until the
    Rust app's shader pack lands).

    pynput's Listener runs on its own thread. We tee events into an
    in-memory list (self.events) which is later flushed to disk.
    """

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._kbd_listener = None
        self._mouse_listener = None
        self._start_time = 0.0
        self._lock = threading.Lock()

    def _now_ms(self) -> int:
        return int((time.time() - self._start_time) * 1000)

    def start(self) -> bool:
        """Begin listening. Returns False if pynput unavailable."""
        try:
            from pynput import keyboard, mouse
        except Exception:  # noqa: BLE001 — best-effort
            return False

        self._start_time = time.time()

        def on_press(key):  # noqa: ANN001
            self._record_key(key, "key_down")

        def on_release(key):  # noqa: ANN001
            self._record_key(key, "key_up")

        def on_move(x: float, y: float) -> None:
            """Handle mouse move events from pynput.

            Records mouse position updates to the event log for later replay.

            Args:
                x: Current X coordinate of the mouse.
                y: Current Y coordinate of the mouse.
            """
            with self._lock:
                self.events.append({
                    "timestamp_ms": self._now_ms(),
                    "event_type": "mouse_move",
                    "mouseX": int(x),
                    "mouseY": int(y),
                })

        def on_click(x: float, y: float, button: Any, pressed: bool) -> None:
            """Handle mouse click events from pynput.

            Records mouse click events to the event log for later replay.

            Args:
                x: X coordinate where the click occurred.
                y: Y coordinate where the click occurred.
                button: The mouse button that was clicked.
                pressed: True if button was pressed, False if released.
            """
            with self._lock:
                self.events.append({
                    "timestamp_ms": self._now_ms(),
                    "event_type": "mouse_click",
                    "mouseX": int(x),
                    "mouseY": int(y),
                    "button": str(button),
                    "pressed": bool(pressed),
                })

        self._kbd_listener = keyboard.Listener(
            on_press=on_press, on_release=on_release
        )
        self._mouse_listener = mouse.Listener(
            on_move=on_move, on_click=on_click
        )
        self._kbd_listener.start()
        self._mouse_listener.start()
        return True

    def _record_key(self, key: Any, event_type: str) -> None:
        # PRD requires keyCode as an int. pynput exposes Key.<name> for
        # special keys and KeyCode(char='X') for char keys. Map to a
        # stable int via vk (Windows virtual-key) when available, else
        # fall back to the Unicode codepoint of the char.
        kc: int = -1
        try:
            vk = getattr(key, "vk", None)
            if isinstance(vk, int):
                kc = vk
            else:
                ch = getattr(key, "char", None)
                if ch:
                    kc = ord(ch)
                else:
                    name = getattr(key, "name", None)
                    if name:
                        kc = hash(name) & 0xFFFF
        except Exception:
            kc = -1
        with self._lock:
            self.events.append({
                "timestamp_ms": self._now_ms(),
                "event_type": event_type,
                "keyCode": kc,
            })

    def realtime_check(self, last_n: int = 100) -> dict[str, Any]:
        """v0.22.0 (Howard '录的时候确保数据的精确度'): lightweight per-tick
        validation on the live event buffer. Caller polls this every ~1s
        on the UI thread to display data-quality status WHILE recording.

        Cheap checks only — no heavy residuals, no copies. Returns:
          - total_events: int
          - monotonic_ts: bool (timestamps non-decreasing in last_n)
          - invalid_keycodes: int (keyCode == -1 count in last_n)
          - mouse_oob: int (off-screen mouse positions in last_n; uses
            primary screen res 1920×1080 as bound, expands on multi-monitor)
          - last_event_age_ms: int (time since last event)
          - healthy: bool (all checks pass)
        """
        with self._lock:
            sample = self.events[-last_n:] if len(self.events) > last_n else list(self.events)
        total = len(self.events)
        if not sample:
            return {
                "total_events": 0,
                "monotonic_ts": True,
                "invalid_keycodes": 0,
                "mouse_oob": 0,
                "last_event_age_ms": -1,
                "healthy": True,
            }
        # Monotonic timestamps
        prev = -1
        monotonic = True
        for ev in sample:
            ts = ev.get("timestamp_ms", 0)
            if ts < prev:
                monotonic = False
                break
            prev = ts
        # Invalid keyCodes (failed VK mapping)
        invalid_kc = sum(
            1 for ev in sample
            if ev.get("event_type", "").startswith("key_")
            and ev.get("keyCode", -1) == -1
        )
        # Mouse out-of-bounds (primary screen heuristic)
        # NOTE: multi-monitor setups can legitimately have negative coords;
        # we use a generous ±5000 envelope to avoid false alarms.
        mouse_oob = sum(
            1 for ev in sample
            if ev.get("event_type", "").startswith("mouse_")
            and (ev.get("mouseX", 0) < -5000 or ev.get("mouseX", 0) > 10000
                 or ev.get("mouseY", 0) < -5000 or ev.get("mouseY", 0) > 10000)
        )
        last_age = self._now_ms() - sample[-1].get("timestamp_ms", 0)
        healthy = monotonic and invalid_kc == 0 and mouse_oob == 0 and last_age < 5000
        return {
            "total_events": total,
            "monotonic_ts": monotonic,
            "invalid_keycodes": invalid_kc,
            "mouse_oob": mouse_oob,
            "last_event_age_ms": last_age,
            "healthy": healthy,
        }

    def stop(self) -> list[dict[str, Any]]:
        """Stop keyboard/mouse listeners and return captured events.

        Stops both the keyboard and mouse event listeners (if running),
        then acquires the lock and returns a copy of all captured events
        as a list of dictionaries.

        Returns:
            List of event dictionaries captured since start() was called.
        """
        for L in (self._kbd_listener, self._mouse_listener):
            try:
                if L is not None:
                    L.stop()
            except Exception:
                pass
        with self._lock:
            return list(self.events)


def _list_windows_processes() -> set[str]:
    """Return a set of running process executable names (case-preserved).

    Uses `tasklist` on Windows (no extra deps). On non-Windows dev boxes
    this returns an empty set — the recorder is Windows-only.

    v0.15.0: pass CREATE_NO_WINDOW (0x08000000) so the CMD console
    spawned for tasklist DOES NOT FLASH on screen. Without this flag,
    tester sees a black popup every 2s during arm-wait — extremely
    annoying. The flag attaches the child process to no console rather
    than the visible default one.
    """
    if os.name != "nt":
        return set()
    try:
        out = subprocess.check_output(
            ["tasklist", "/FO", "CSV", "/NH"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
    except Exception:  # noqa: BLE001
        return set()
    names = set()
    for line in out.splitlines():
        # CSV: "Image Name","PID","Session Name","Session#","Mem Usage"
        if line.startswith('"'):
            try:
                names.add(line.split('","', 1)[0].lstrip('"'))
            except Exception:
                continue
    return names


def _minecraft_running() -> bool:
    """True iff any process matching MC_PROCESS_NAMES is alive."""
    return bool(MC_PROCESS_NAMES & _list_windows_processes())


# ---- Recorder app ----------------------------------------------------------

GREEN = "#2e7d32"
RED = "#c62828"
ORANGE = "#ef6c00"
TEXT_GRAY = "#546e7a"


class RecorderApp(tk.Tk):
    """One-window recorder: detect MC, record, save, show status."""

    def __init__(self) -> None:
        super().__init__()
        self.title("Oyster 录制器")
        self.geometry("520x340")
        self.minsize(440, 300)
        self.configure(bg="white")

        self._ffmpeg_proc: Optional[subprocess.Popen] = None
        self._output_path: Optional[Path] = None
        self._stop_event = threading.Event()
        self._input_capture: Optional[InputCapture] = None
        self._captured_events: list[dict[str, Any]] = []
        # v0.4.0: tester explicitly opts in to recording. Default is
        # observe-only mode so our .exe can NEVER be blamed for MC
        # crashing — a tester whose MC crashes can verify it crashes
        # WITHOUT us recording first.
        self._record_armed = False
        self._mc_window_rect: Optional[dict[str, int]] = None

        # rc9 (Howard 2026-05-09): depth-progress UX state.
        #
        # _skip_depth_flag is a threading.Event the inference loop polls
        # between frames. The recorder GUI sets it when the tester clicks
        # "跳过深度图" / when GPU isn't detected and the tester left the
        # default-skip checkbox armed.
        #
        # _depth_default_skip is a boolean computed once at startup —
        # True means the GPU probe came back negative so the skip
        # checkbox is pre-checked. The tester can still untick it on the
        # progress UI to override.
        #
        # _depth_progress_widgets holds the live tk widgets for the
        # progress UI so we can tear it down once inference returns to
        # ready state.
        self._skip_depth_flag = threading.Event()
        self._depth_default_skip: bool = not _detect_gpu_available()
        self._depth_progress_widgets: list[Any] = []
        self._depth_progress_started_at: float = 0.0

        self._build_ui()
        # Start background watcher immediately — testers do not click anything.
        threading.Thread(target=self._watch_loop, daemon=True).start()

        # Clean shutdown if window is closed mid-recording.
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # v0.8.0: kick off self-update check immediately. If a newer
        # release exists, stage the update and exit cleanly so the .bat
        # can swap us out and re-launch. Tester sees a brief flicker.
        _check_for_update_in_background(self._on_update_check)

    def _build_ui(self) -> None:
        # v0.4.0: explicit "Arm recording" button. Default state = NOT
        # recording. Tester can verify Minecraft works first, then arm
        # the recorder. This also distinguishes "MC crashes by itself"
        # from "MC crashes because of our ffmpeg".
        self._verdict = tk.Label(
            self,
            text="…",
            font=("Helvetica", 30, "bold"),
            bg="white",
            fg=TEXT_GRAY,
            height=2,
        )
        self._verdict.pack(fill="x", padx=20, pady=(20, 8))

        self._subtitle = tk.Label(
            self,
            text="先正常打开 Minecraft 玩一下，确认 MC 不崩。\n要开始录制再点下面按钮。",
            font=("Helvetica", 12),
            bg="white",
            fg=TEXT_GRAY,
            wraplength=480,
            justify="center",
        )
        self._subtitle.pack(pady=(0, 4))

        # The arm-recording button (default: idle / unarmed)
        self._arm_btn = tk.Button(
            self,
            text="▶ 开始录制",
            font=("Helvetica", 14, "bold"),
            bg="#1976d2",
            fg="white",
            activebackground="#1565c0",
            activeforeground="white",
            bd=0,
            padx=22,
            pady=12,
            cursor="hand2",
            command=self._toggle_arm,
        )
        self._arm_btn.pack(pady=(16, 4))

        # "Send log" — manual telemetry push so tester can give engineering
        # a remote-readable log URL even if the recorder hasn't crashed.
        # Useful when MC crashes but our recorder is still alive.
        self._upload_btn = tk.Button(
            self,
            text="↗ 发送日志给工程师",
            font=("Helvetica", 10, "underline"),
            bg="white",
            fg="#1976d2",
            bd=0,
            cursor="hand2",
            command=self._upload_log_now,
        )
        self._upload_btn.pack(pady=(0, 6))

        # Spacer
        tk.Frame(self, bg="white").pack(expand=True, fill="both")

        # Tiny output-dir hint + log path at the bottom
        self._hint = tk.Label(
            self,
            text=(
                f"录制完成后会保存到: {_output_dir()}\n"
                f"如果出问题，点上面按钮自动打包诊断包到桌面。"
            ),
            font=("Helvetica", 9),
            bg="white",
            fg=TEXT_GRAY,
            wraplength=500,
            justify="center",
        )
        self._hint.pack(pady=(0, 4))

        # v0.20.1: extra row of small action buttons for tester self-help
        helpbar = tk.Frame(self, bg="white")
        helpbar.pack(pady=(0, 8))
        tk.Button(
            helpbar, text="打开日志文件夹",
            font=("Helvetica", 8), bg="white", fg="#666",
            bd=0, cursor="hand2",
            command=lambda: self._open_path(_STARTUP_LOG.parent),
        ).pack(side="left", padx=4)
        tk.Button(
            helpbar, text="复制日志路径",
            font=("Helvetica", 8), bg="white", fg="#666",
            bd=0, cursor="hand2",
            command=lambda: self._copy_to_clipboard(str(_STARTUP_LOG)),
        ).pack(side="left", padx=4)
        tk.Button(
            helpbar, text="导出诊断包到桌面",
            font=("Helvetica", 8), bg="white", fg="#666",
            bd=0, cursor="hand2",
            command=self._export_diagnostic_only,
        ).pack(side="left", padx=4)
        # rc8: prominent "View My Recordings" button — opens the OysterClips
        # folder in Explorer at the registry-resolved (OneDrive-aware) path
        # where session tarballs actually live. Bigger / styled vs the other
        # helpbar buttons because this is the action testers ask for first.
        tk.Button(
            helpbar, text="📂 我的录像",
            font=("Helvetica", 9, "bold"), bg="#1976d2", fg="white",
            activebackground="#1565c0", activeforeground="white",
            bd=0, padx=10, pady=3, cursor="hand2",
            command=lambda: self._open_path(_output_dir()),
        ).pack(side="left", padx=4)

    def _export_diagnostic_only(self) -> None:
        """Build diagnostic zip and open the Desktop folder. No network."""
        _trace("user clicked export-diagnostic button")
        def _go():
            zp = _build_diagnostic_zip()
            def apply():
                if zp:
                    self._hint.config(
                        text=f"诊断包已导出: {zp}\n请发给工程师",
                        fg="#1976d2",
                    )
                    self._open_path(zp.parent)
                else:
                    self._hint.config(
                        text="导出失败，请截屏后联系工程师",
                        fg="#dc2626",
                    )
            self.after(0, apply)
        threading.Thread(target=_go, daemon=True).start()

    def _upload_log_now(self) -> None:
        """Tester clicked '发送日志给工程师'. v0.20.1: always build a local
        diagnostic zip on Desktop as fallback, also try remote upload.
        Tester always has a path they can manually send via WeChat/email.
        """
        _trace("user clicked send-log button")
        self._upload_btn.config(text="↗ 上传中…", state="disabled")

        def _go():
            # Always build diagnostic zip first (works offline, no network).
            zip_path = _build_diagnostic_zip()
            # Then attempt remote upload (best-effort).
            url = _upload_log_remote()

            def apply():
                if url:
                    self._upload_btn.config(
                        text="✓ 上传成功 — 复制链接",
                        state="normal",
                        command=lambda u=url: self._copy_to_clipboard(u),
                    )
                    self._hint.config(
                        text=f"工程师可访问: {url}\n本地诊断包也在: {zip_path or _desktop_path()}",
                        fg="#1976d2",
                    )
                elif zip_path:
                    self._upload_btn.config(
                        text="✓ 诊断包已生成 — 打开桌面",
                        state="normal",
                        command=lambda p=zip_path: self._open_path(p.parent),
                    )
                    self._hint.config(
                        text=f"远程上传失败，但已生成桌面诊断包:\n{zip_path}\n请通过微信/邮件发给工程师",
                        fg="#d97706",  # amber
                    )
                else:
                    self._upload_btn.config(
                        text="✗ 全部失败 — 复制日志路径",
                        state="normal",
                        command=lambda: self._copy_to_clipboard(str(_STARTUP_LOG)),
                    )
                    self._hint.config(
                        text=f"上传 + 打包都失败。请手动发送:\n{_STARTUP_LOG}",
                        fg="#dc2626",  # red
                    )
            self.after(0, apply)

        threading.Thread(target=_go, daemon=True).start()

    def _copy_to_clipboard(self, text: str) -> None:
        """Copy text to system clipboard via Tk."""
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update()
            _trace(f"clipboard: copied {text!r}")
        except Exception as e:
            _trace(f"clipboard: copy failed {e}")

    def _open_path(self, path: Path) -> None:
        """Open a file or folder in the OS file explorer."""
        try:
            if os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
            _trace(f"open_path: opened {path}")
        except Exception as e:
            _trace(f"open_path: failed {e}")

    def _auto_lint(self, tarball: Path) -> None:
        """v0.7.0: run G165 lint v3 on the just-recorded tarball + show
        24-criteria PRD result inline. Runs on a daemon thread so the
        UI stays responsive while numpy/PIL spin up.
        """
        def _go():
            try:
                # Lazy import — keeps recorder cold-start fast.
                if getattr(sys, "frozen", False):
                    sys.path.insert(0, str(_BUNDLE_ROOT))
                import lint_v3_prd_grounded as lint_mod  # noqa: PLC0415

                # Extract the tarball into a temp dir for the linter.
                with tempfile.TemporaryDirectory() as td:
                    td_path = Path(td)
                    with tarfile.open(tarball, "r:gz") as tf:
                        tf.extractall(td_path)
                    inner = [p for p in td_path.iterdir() if p.is_dir()]
                    target = inner[0] if len(inner) == 1 else td_path
                    rpt = lint_mod.run_all_checks(target)

                _trace(
                    f"auto_lint: {rpt.passed_count}/{rpt.total_checks} "
                    f"PASS, {rpt.failed_count} FAIL"
                )

                def _ui():
                    if rpt.failed_count == 0:
                        self._set(
                            f"✓ {rpt.passed_count}/{rpt.total_checks} 全过",
                            GREEN,
                            "完全符合买家规格 — 可以交付。",
                        )
                    else:
                        # Lite recorder is expected to fail several
                        # depth/audio/intrinsics checks; that's known
                        # scope (Rust app fixes those). Still surface
                        # the count so Howard sees what's collected.
                        self._set(
                            f"⚠️ {rpt.passed_count}/{rpt.total_checks} 通过",
                            ORANGE,
                            f"{rpt.failed_count} 项缺失（深度/音频/内参等需要正式 Rust 版补）。"
                            f"已收的部分可以先用。",
                        )
                    # Detail in hint label.
                    fail_names = [
                        f"#{r.criterion_id}"
                        for r in rpt.results if not r.passed
                    ][:8]
                    self._hint.config(
                        text=(
                            f"已保存: {tarball}\n"
                            f"未通过: {', '.join(fail_names) if fail_names else '(none)'}"
                        ),
                        fg=ORANGE if rpt.failed_count else GREEN,
                    )
                self.after(0, _ui)
            except Exception as exc:  # noqa: BLE001
                _trace(f"auto_lint failed: {exc}\n{traceback.format_exc()}")
                self.after(0, lambda: self._hint.config(
                    text=f"已保存: {tarball}\n（自动验证失败 — 见远程日志）",
                    fg=ORANGE,
                ))

        threading.Thread(target=_go, daemon=True).start()

    def _auto_bft(self, tarball: Path) -> None:
        """v0.21.0: BFT N=4 self-verification on the just-recorded tarball.

        Goes deeper than _auto_lint (which runs PRD-grounded shallow checks).
        BFT runs V₁ Claude / V₂ MiniMax / V₂' GLM / V₃ Physics-Table on every
        frame pair, computes per-residual COMMIT/REJECT/VIEW_CHANGE/INSUFFICIENT
        and a dataset_decision. If FAIL, surfaces specific residuals + which
        verifier rejected, so tester knows exactly what to re-record.

        Howard's "shift-left" ask: 数据对的最强保证 = recorder 自验。
        """
        def _go():
            try:
                # Lazy import — keeps cold-start fast.
                if getattr(sys, "frozen", False):
                    sys.path.insert(0, str(_BUNDLE_ROOT))
                from bin.bft_orchestrator import orchestrator as orch  # noqa: PLC0415
                import json  # noqa: PLC0415

                # Extract tarball + read action_camera.json.
                with tempfile.TemporaryDirectory() as td:
                    td_path = Path(td)
                    with tarfile.open(tarball, "r:gz") as tf:
                        tf.extractall(td_path)
                    # v0.23.0: target = whichever directory level holds
                    # action_camera.json. Some tarballs wrap everything in a
                    # single dir (recorder default), others extract files at
                    # root (sample_tarball_builder). Pick whichever works.
                    if (td_path / "action_camera.json").exists():
                        target = td_path
                    else:
                        inner = [p for p in td_path.iterdir() if p.is_dir()]
                        candidates = [
                            p for p in inner
                            if (p / "action_camera.json").exists()
                        ]
                        target = candidates[0] if candidates else (
                            inner[0] if len(inner) == 1 else td_path
                        )
                    ac_path = target / "action_camera.json"
                    if not ac_path.exists():
                        _trace("auto_bft: action_camera.json not found, skip")
                        return
                    records = json.loads(ac_path.read_text(encoding="utf-8"))

                    # v0.23.0: gather multimodal artifact paths so the
                    # orchestrator-level residuals (R13/R15/R16/R20/R22/R23)
                    # actually fire instead of ABSTAINing. Each path is
                    # backward-compatible — orchestrator self-handles None.
                    inputs_path = target / "inputs.jsonl"
                    depth_dir = target / "depth"
                    depth_manifest_path = target / "depth_manifest.json"
                    video_path = target / "video.mp4"
                    inputs_arg = inputs_path if inputs_path.exists() else None
                    depth_dir_arg = depth_dir if depth_dir.exists() else None
                    depth_manifest_arg = (
                        depth_manifest_path if depth_manifest_path.exists() else None
                    )
                    video_arg = video_path if video_path.exists() else None

                    # Run BFT on the first 60 contiguous frames (= 2 seconds).
                    # Must be contiguous because R03/R04/R05 require real
                    # frame[n+1] neighbors — non-adjacent pairs produce false
                    # REJECTs (Δposition over a gap ≠ speed · dt). The full
                    # dataset analysis runs on engineer's side via the CLI
                    # `python -m bin.bft_orchestrator.orchestrator <ac.json>`.
                    n = len(records)
                    spot_check_size = min(60, n)
                    contiguous_records = records[:spot_check_size]
                    report = orch.aggregate_dataset(
                        contiguous_records,
                        fps=30.0,
                        inputs_path=inputs_arg,
                        depth_dir=depth_dir_arg,
                        depth_manifest_path=depth_manifest_arg,
                        video_path=video_arg,
                    )

                _trace(f"auto_bft: report={report}")

                def _ui():
                    decision = report.get("dataset_decision", "UNKNOWN")
                    residuals = report.get("residuals", {})
                    # v0.23.0: separate single-modal (R01..R12) from
                    # multimodal/dataset (R13/R15/R16/R20*/R22/R23) so the
                    # tester sees which class failed.
                    multimodal_prefixes = ("R13", "R15", "R16", "R20", "R22", "R23")
                    multimodal_bad = []
                    single_bad = []
                    for rname, stats in residuals.items():
                        rej = stats.get("REJECT", 0)
                        vc = stats.get("VIEW_CHANGE", 0)
                        if rej == 0 and vc == 0:
                            continue
                        tag = (
                            f"{rname}({rej}REJ)" if rej > 0
                            else f"{rname}({vc}VC)"
                        )
                        if rname.startswith(multimodal_prefixes):
                            multimodal_bad.append(tag)
                        else:
                            single_bad.append(tag)
                    if decision == "PASS":
                        self._set(
                            f"✓ BFT 共识: 全过 ({len(residuals)} 残差)",
                            GREEN,
                            "数据通过 N=4 拜占庭共识 — 可以上传。",
                        )
                        self._hint.config(
                            text=f"已保存: {tarball}\nBFT N=4 全过 ({len(residuals)} 残差)",
                            fg=GREEN,
                        )
                    else:
                        bad_combined = multimodal_bad + single_bad
                        detail_lines = []
                        if multimodal_bad:
                            detail_lines.append(
                                f"多模态失败: {', '.join(multimodal_bad[:5])}"
                            )
                        if single_bad:
                            detail_lines.append(
                                f"单模态失败: {', '.join(single_bad[:5])}"
                            )
                        if not detail_lines:
                            detail_lines.append("(详情见日志)")
                        self._set(
                            f"⚠️ BFT 共识 FAIL ({decision})",
                            ORANGE,
                            "\n".join(detail_lines)
                            + "\n建议重录或检查 producer。",
                        )
                        self._hint.config(
                            text=(
                                f"已保存: {tarball}\n"
                                f"BFT 数据决议: {decision} — "
                                f"问题: {', '.join(bad_combined[:5]) if bad_combined else '见日志'}"
                            ),
                            fg=ORANGE,
                        )
                self.after(0, _ui)
            except Exception as exc:
                _trace(f"auto_bft failed: {exc}\n{traceback.format_exc()}")

        threading.Thread(target=_go, daemon=True).start()

    def _on_update_check(self, latest_tag, exe_url, is_newer):
        """Self-update callback. If newer release found, stage + restart."""
        if not is_newer or not exe_url:
            self.after(0, lambda: self._hint.config(
                text=f"已经是最新版 ({_current_version_tag()})\n如果出问题，请把 {_STARTUP_LOG} 截图给工程师。",
                fg=TEXT_GRAY,
            ))
            return
        # Don't auto-replace mid-recording; wait until tester is idle.
        if self._record_armed:
            _trace(f"update: deferred — recording in progress, will retry on close")
            return
        _trace(f"update: staging {latest_tag}")
        self.after(0, lambda: self._set("⏳ 自动更新中…", ORANGE,
                                         f"正在下载 {latest_tag}，几秒后会自动重启。"))
        if _stage_self_update(exe_url):
            _trace("update: staged ok, exiting for relaunch")
            self.after(2000, self._on_close)
        else:
            self.after(0, lambda: self._hint.config(
                text=f"自动更新失败 — 见 {_STARTUP_LOG}",
                fg=ORANGE,
            ))

    def _tick_recording_status(self) -> None:
        """v0.12.0: tick once per second while ffmpeg is alive, updating
        the subtitle with elapsed time + current video file size +
        progress toward 6-minute cap. Self-stops when ffmpeg dies.
        """
        if self._ffmpeg_proc is None or self._ffmpeg_proc.poll() is not None:
            return  # ffmpeg has exited; let watch_loop's finalizer take over
        try:
            elapsed = max(0.0, time.time() - self._record_started_at)
        except Exception:
            elapsed = 0.0
        # Format mm:ss
        mm = int(elapsed // 60)
        ss = int(elapsed % 60)
        # Read current video file size; the file may not exist for the
        # first ~1s as ffmpeg sets up its container.
        size_str = "—"
        try:
            if self._video_path and self._video_path.exists():
                mb = self._video_path.stat().st_size / (1024 * 1024)
                size_str = f"{mb:.1f} MB"
        except Exception:
            pass
        # Progress bar visual (caps at 6 min = 360s)
        cap = 360.0
        pct = min(100, int((elapsed / cap) * 100))
        bar_w = 18
        filled = int(bar_w * pct / 100)
        bar = "█" * filled + "░" * (bar_w - filled)
        # v0.22.0: real-time data quality (Howard '录的时候确保数据的精确度')
        # Lightweight check on the live event buffer — if anything looks off,
        # surface it NOW so the tester aborts and re-records instead of
        # discovering a problem 6 minutes later in _auto_bft.
        quality_line = ""
        try:
            if self._input_capture is not None:
                rt = self._input_capture.realtime_check(last_n=200)
                if rt["healthy"]:
                    quality_line = f"\n✓ 数据精度 OK ({rt['total_events']} 事件)"
                else:
                    issues = []
                    if not rt["monotonic_ts"]:
                        issues.append("时间戳乱序")
                    if rt["invalid_keycodes"] > 0:
                        issues.append(f"{rt['invalid_keycodes']}个无效键码")
                    if rt["mouse_oob"] > 0:
                        issues.append(f"{rt['mouse_oob']}个鼠标越界")
                    if rt["last_event_age_ms"] > 5000:
                        issues.append(f"事件停顿 {rt['last_event_age_ms']/1000:.1f}s")
                    quality_line = f"\n⚠️ 数据精度: {', '.join(issues)}"
        except Exception:
            pass
        try:
            self._subtitle.config(
                text=f"⏱  {mm}分{ss:02d}秒  /  6 分钟  ({pct}%)\n[{bar}]\n📦 视频文件 {size_str}{quality_line}",
                fg=RED,
            )
        except Exception:
            pass
        # Schedule next tick.
        self.after(1000, self._tick_recording_status)

    def _restore_window(self) -> None:
        """Bring the recorder window back from minimized state.

        Called from the watcher thread (via self.after) after recording
        finishes, so the tester sees the verdict banner without having
        to find us in the taskbar.
        """
        try:
            self.deiconify()
            self.lift()
            _trace("window restored from taskbar")
        except Exception as e:
            _trace(f"deiconify failed: {e}")

    # ---- rc9 depth-progress UI ----------------------------------------
    # Howard 2026-05-09: testers thought the recorder was hung because
    # DepthAnything V2 inference runs invisibly for 30-60 minutes on CPU
    # (10800 frames × 1-3 sec each). These methods replace the normal
    # arm/idle UI with a progress bar + ETA + Skip button while inference
    # is in flight, then restore the normal UI when it finishes (or the
    # tester skips). All Tk mutations happen on the main thread via
    # self.after(0, ...) so the depth runner can call back from any
    # worker thread safely.
    def _show_depth_progress_ui(self) -> None:
        """Replace the normal recorder UI with a depth-inference progress
        view. Force the window to re-appear from the taskbar so the tester
        actually sees it.
        """
        # rc9 Bug 1: force restore from iconified — tester complained the
        # recorder "stayed hidden" while depth ran for ~40 min.
        try:
            self.deiconify()
            self.lift()
            self.attributes("-topmost", True)
            self.after(500, lambda: self.attributes("-topmost", False))
            _trace("depth_progress: restored window from taskbar")
        except Exception as e:
            _trace(f"depth_progress: deiconify failed: {e}")

        # Reset state.
        self._skip_depth_flag.clear()
        self._depth_progress_started_at = time.time()

        # Hide the normal arm button + helpbar; we'll restore them on
        # finish. We don't destroy them so layout state is preserved.
        try:
            self._arm_btn.pack_forget()
            self._upload_btn.pack_forget()
        except Exception:
            pass

        # Top-level status text — replaces the verdict banner.
        self._set("📊 处理深度图中…", "#1976d2",
                  "录制已结束，正在生成每帧深度图。\n"
                  "深度图完成后会打包成最终 tarball。")

        # Build the progress widgets. We keep refs in a list so
        # _hide_depth_progress_ui can tear them down cleanly.
        progress_frame = tk.Frame(self, bg="white")
        progress_frame.pack(pady=(8, 4), fill="x", padx=24)

        # Counter label: "1240 / 10800 帧"
        counter = tk.Label(
            progress_frame,
            text="0 / ? 帧",
            font=("Helvetica", 12, "bold"),
            bg="white",
            fg="#1976d2",
        )
        counter.pack(pady=(0, 4))

        # Progress bar (ttk indeterminate-capable, but we'll drive it
        # determinately via self["value"] = pct from the callback).
        bar = ttk.Progressbar(
            progress_frame,
            orient="horizontal",
            mode="determinate",
            maximum=100.0,
            length=400,
        )
        bar.pack(pady=(0, 6))
        bar["value"] = 0.0

        # ETA label: computed from rate × frames remaining.
        eta_label = tk.Label(
            progress_frame,
            text="预计剩余时间：—",
            font=("Helvetica", 10),
            bg="white",
            fg="#546e7a",
        )
        eta_label.pack(pady=(0, 6))

        # Skip-depth row: pre-checked when no GPU detected.
        skip_row = tk.Frame(progress_frame, bg="white")
        skip_row.pack(pady=(4, 0))
        skip_var = tk.BooleanVar(value=self._depth_default_skip)
        skip_check = tk.Checkbutton(
            skip_row,
            text=(
                "默认跳过 (未检测到独立显卡，CPU 推断会很慢)"
                if self._depth_default_skip
                else "跳过深度图（保留视频和操作数据）"
            ),
            variable=skip_var,
            font=("Helvetica", 10),
            bg="white",
            fg="#546e7a",
            selectcolor="white",
            activebackground="white",
            command=lambda: self._on_skip_depth_toggle(skip_var.get()),
        )
        skip_check.pack(side="left", padx=(0, 6))

        skip_btn = tk.Button(
            skip_row,
            text="🚫 跳过深度图",
            font=("Helvetica", 11, "bold"),
            bg="#d97706",
            fg="white",
            activebackground="#b45309",
            activeforeground="white",
            bd=0,
            padx=14,
            pady=6,
            cursor="hand2",
            command=self._on_skip_depth_clicked,
        )
        skip_btn.pack(side="left", padx=(6, 0))

        # If GPU isn't detected, set the flag immediately. The tester can
        # still uncheck the box to override (which clears the flag).
        if self._depth_default_skip:
            self._skip_depth_flag.set()
            _trace("depth_progress: default-skip armed (no GPU detected)")

        self._depth_progress_widgets = [
            progress_frame, counter, bar, eta_label,
            skip_row, skip_check, skip_btn, skip_var,
        ]

    def _on_skip_depth_toggle(self, checked: bool) -> None:
        """Skip checkbox toggled. Set/clear the cooperative skip flag."""
        if checked:
            self._skip_depth_flag.set()
            _trace("depth_progress: skip flag SET via checkbox")
        else:
            self._skip_depth_flag.clear()
            _trace("depth_progress: skip flag CLEARED via checkbox")

    def _on_skip_depth_clicked(self) -> None:
        """Tester clicked the prominent 'Skip depth maps' button."""
        _trace("depth_progress: tester clicked SKIP DEPTH button")
        self._skip_depth_flag.set()
        # Replace the inline status text so the tester immediately sees
        # the click landed even before the inference loop polls the flag.
        self._set("⏳ 正在收尾跳过…", "#d97706",
                  "等当前帧推断完，就会打包不带深度图的 tarball。")

    def _on_depth_progress(self, frames_done: int, total_frames: int) -> None:
        """Called from the depth runner thread — marshal to Tk via after().

        Updates the counter, progress bar, and ETA. Safe to call with
        total_frames == 0 (we just hide the percent + ETA in that case).
        """
        # Compute everything off the GUI thread, then schedule the apply.
        elapsed = max(0.001, time.time() - self._depth_progress_started_at)
        rate = frames_done / elapsed if elapsed > 0 else 0.0  # frames/sec
        remaining = max(0, (total_frames - frames_done))
        eta_sec = remaining / rate if rate > 0 else 0.0
        pct = (100.0 * frames_done / total_frames) if total_frames > 0 else 0.0

        def _apply() -> None:
            try:
                if not self._depth_progress_widgets:
                    return  # UI already torn down
                _, counter, bar, eta_label, *_rest = self._depth_progress_widgets
                if total_frames > 0:
                    counter.config(
                        text=f"{frames_done} / {total_frames} 帧 ({pct:.1f}%)",
                    )
                    bar["value"] = pct
                else:
                    counter.config(text=f"{frames_done} / ? 帧")
                # ETA — only meaningful once we have a rate AND a total.
                if total_frames > 0 and rate > 0 and frames_done > 0:
                    if eta_sec >= 60:
                        eta_label.config(
                            text=f"预计剩余时间：约 {int(round(eta_sec / 60))} 分钟",
                        )
                    else:
                        eta_label.config(
                            text=f"预计剩余时间：约 {int(round(eta_sec))} 秒",
                        )
                else:
                    eta_label.config(text="预计剩余时间：估算中…")
            except Exception as e:
                _trace(f"depth_progress: apply failed: {e}")

        try:
            self.after(0, _apply)
        except RuntimeError:
            # Tk closed mid-inference; nothing to update.
            pass

    def _hide_depth_progress_ui(self) -> None:
        """Tear down the depth-progress widgets and restore normal UI.

        Called when inference finishes (success OR skip). Idempotent.
        """
        widgets = self._depth_progress_widgets
        self._depth_progress_widgets = []

        def _apply() -> None:
            for w in widgets:
                try:
                    if hasattr(w, "destroy"):
                        w.destroy()
                except Exception:
                    pass
            # Re-add the arm + upload buttons so the tester can record
            # again without restarting the .exe. Order matches _build_ui.
            try:
                self._arm_btn.pack(pady=(16, 4))
                self._upload_btn.pack(pady=(0, 6))
            except Exception:
                pass
            _trace("depth_progress: UI restored to ready state")

        try:
            self.after(0, _apply)
        except RuntimeError:
            pass

    def _toggle_arm(self) -> None:
        """Tester clicked the arm button. Toggle recording state.

        v0.6.0: Howard 反馈 '界面 影响玩游戏'. As soon as recording is
        armed we minimize ourselves to the taskbar so MC reclaims full
        screen focus. Critical for exclusive-fullscreen MC sessions
        where our window stealing focus could crash MC. The user can
        click the taskbar icon any time to bring us back and click
        '停止录制'.
        """
        if not self._record_armed:
            # Arm
            self._record_armed = True
            self._arm_btn.config(
                text="■ 停止录制",
                bg="#c62828",
                activebackground="#b71c1c",
            )
            _trace("recording armed by user click")
            # v0.17.0 BUG FIX: do NOT iconify here. Earlier versions
            # iconified the window the moment ▶ was clicked, hiding the
            # GUI before the watch_loop's Phase-1 status messages
            # ('⏸ 已 arm — 请打开 Minecraft') could be seen. Tester saw
            # window vanish and assumed crash. v0.17.0 defers iconify to
            # the moment ffmpeg actually starts (in _run_one_session, see
            # 'iconify after ffmpeg starts' marker).
        else:
            # Disarm — request the watcher to stop any in-flight ffmpeg.
            self._record_armed = False
            self._stop_event.set()
            self._stop_ffmpeg()
            self._arm_btn.config(
                text="▶ 开始录制",
                bg="#1976d2",
                activebackground="#1565c0",
            )
            _trace("recording disarmed by user click")

    # ---- worker --------------------------------------------------------
    def _watch_loop(self) -> None:
        """Poll for MC launch → record → poll for MC exit → finalize.

        v0.9.0: this is now the SESSION wrapper. After a session
        finishes (recording packaged + verdict shown), it loops back to
        Phase 1 so re-arming via the GUI button works without restarting
        the .exe. Only the close-the-window or app exit path tears the
        loop down for good (via self._stop_event when _on_close fires).
        """
        if not _FFMPEG.exists():
            self._set("⚠️ 缺少 ffmpeg", ORANGE,
                      "ffmpeg.exe 没有打包进来，请联系工程师。")
            return

        while not self._stop_event.is_set():
            self._run_one_session()
            # After a session ends naturally, reset the arm state so the
            # GUI button starts fresh and Phase 1 (wait for arm + MC)
            # picks up cleanly on the next iteration. _stop_event is
            # only set by _on_close, which means tester-quit-the-app.
            if not self._stop_event.is_set():
                self.after(0, self._reset_arm_button)

    def _reset_arm_button(self) -> None:
        """After a recording session, restore the button to '▶ 开始录制' so
        the tester can immediately start another session without
        restarting the .exe.
        """
        try:
            self._record_armed = False
            self._arm_btn.config(
                text="▶ 开始录制",
                bg="#1976d2",
                activebackground="#1565c0",
            )
            _trace("button reset for next session")
        except Exception:
            pass

    def _run_one_session(self) -> None:
        """One arm→record→package→verdict cycle. Returns when finished."""
        # Phase 1: wait for tester to arm AND MC to start.
        # v0.4.0 split: do NOT spawn ffmpeg until the tester explicitly
        # clicks "开始录制". This protects testers whose MC otherwise
        # works fine — our auto-ffmpeg won't blame us for MC issues.
        # v0.16.0: live status update so tester always sees what we're
        # waiting for. Previous bug: tester clicked ▶ → button changed
        # to '■ 停止录制' → but if MC wasn't running, watch_loop sat in
        # silent Phase 1 sleep forever and 'time stays 0' = '时间不动'.
        _trace("watch_loop: waiting for arm + MC")
        self._set("准备好", TEXT_GRAY,
                  "先打开 Minecraft 玩一会儿确认它不崩。\n确认后再点上面 ▶ 开始录制。")
        arm_announced_at = None
        last_status_update = 0.0
        while not self._stop_event.is_set():
            armed = self._record_armed
            mc_alive = _minecraft_running()
            if armed and mc_alive:
                _trace("watch_loop: armed + MC alive → entering recording")
                break
            now = time.time()
            # First-time arm message
            if armed and arm_announced_at is None:
                arm_announced_at = now
            # Update status every 2s while waiting so tester sees progress
            if now - last_status_update >= 2.0:
                last_status_update = now
                if armed and not mc_alive:
                    waited = int(now - (arm_announced_at or now))
                    self._set("⏸ 已 arm — 请打开 Minecraft", ORANGE,
                              f"录制器在等 Minecraft 启动…（已等 {waited} 秒）\n"
                              f"打开 Minecraft 后 1-2 秒会自动开始录")
                elif not armed and mc_alive:
                    self._set("Minecraft 已开 — 等你点 ▶ 开始录制", TEXT_GRAY,
                              "MC 检测到了，点上面蓝色按钮就开始录")
                elif not armed and not mc_alive:
                    self._set("准备好", TEXT_GRAY,
                              "先打开 Minecraft 玩一会儿确认它不崩。\n"
                              "确认后再点上面 ▶ 开始录制。")
            time.sleep(0.5)
        if self._stop_event.is_set():
            _trace("watch_loop: stopped before recording")
            return

        # 5-second settle delay. Lets MC fully transition out of any
        # loading screen / world generation before ffmpeg attaches to
        # its window or grabs the desktop. Reported MC crashes were
        # likely caused by GDI capture starting mid-loading.
        _trace("watch_loop: 5s settle before ffmpeg")
        self._set("● 即将开始", ORANGE, "5 秒后开始录制（让 Minecraft 稳定）…")
        for _ in range(5):
            if self._stop_event.is_set() or not self._record_armed:
                _trace("watch_loop: aborted during settle")
                return
            time.sleep(1.0)

        # Capture window geometry (if visible) for systeminfo.json.
        self._mc_window_rect = _get_minecraft_window_rect()
        _trace(f"watch_loop: mc_window={self._mc_window_rect}")

        # Phase 2: start recording into a temp dir; we'll package it as
        # a 5-file PRD-shaped tarball after MC exits.
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        self._tmp_dir = Path(tempfile.mkdtemp(prefix=f"oyster-rec-{ts}-"))
        self._video_path = self._tmp_dir / "video.mp4"
        self._record_started_at = time.time()
        # R18 session-binding: one UUID per recording, propagated into
        # session_manifest.json + every action_camera frame + inputs.jsonl
        # session_start. Closes red-team B-05 (Frankenstein splice).
        import uuid as _uuid_mod
        self._session_id = str(_uuid_mod.uuid4())
        try:
            self._start_ffmpeg(self._video_path)
        except Exception as exc:  # noqa: BLE001
            self._set("⚠️ 录制启动失败", ORANGE,
                      f"{type(exc).__name__}: {exc}")
            return

        # Start input capture in parallel with video. If pynput fails
        # (rare — e.g. tester hardened OS), record video only and note
        # in subtitle so the tester knows.
        self._input_capture = InputCapture()
        input_ok = self._input_capture.start()
        if input_ok:
            self._set("● 正在录制", RED,
                      "玩你的 Minecraft 即可，退出游戏会自动停止录制。"
                      "（视频 + 键鼠输入同步采集中）")
        else:
            self._set("● 正在录制（仅视频）", RED,
                      "键鼠采集未启动，仅录制视频。继续玩游戏即可。")

        # v0.12.0: live progress ticker so the tester knows recording is
        # actually working. Updates every second with elapsed seconds +
        # current video file size. Self-stops when ffmpeg ends.
        self.after(0, self._tick_recording_status)

        # v0.17.0: iconify after ffmpeg starts marker — moved here from
        # _toggle_arm. Now Phase 1 status messages stay visible until
        # recording actually begins.
        try:
            self.after(0, self.iconify)
            _trace("window iconified to taskbar (after ffmpeg start)")
        except Exception as e:
            _trace(f"iconify failed: {e}")

        # Phase 3: wait for MC to exit OR for 6 minutes elapsed (PRD cap)
        # OR for the user to disarm.
        # PRD spec requires 5-6 min duration; auto-stop at 6 min so the
        # downstream lint doesn't reject for over-length.
        MAX_RECORD_SECONDS = 6 * 60
        while True:
            if self._stop_event.is_set():
                _trace("watch_loop: stop_event set — finalizing whatever we have")
                break
            if not self._record_armed:
                _trace("watch_loop: user disarmed — finalizing whatever we have")
                break
            if not _minecraft_running():
                _trace("watch_loop: MC exited — finalizing")
                break
            elapsed = time.time() - self._record_started_at
            if elapsed >= MAX_RECORD_SECONDS:
                self._set("⏱ 已到 6 分钟，自动停止", ORANGE,
                          "PRD 规格要求 5-6 分钟，正在收尾…")
                break
            time.sleep(2.0)

        # v0.9.0 BUG FIX: previously, hitting `_stop_event` returned
        # before `_package_tarball`, throwing away the recording. Now ANY
        # stop reason (MC exit / user disarm / 6-min cap / stop_event)
        # falls through to packaging so the tester always gets a tarball
        # representing what was actually recorded up to that point.

        # Phase 4: finalize ffmpeg + input capture, then package.
        self._stop_ffmpeg()
        if self._input_capture is not None:
            self._captured_events = self._input_capture.stop()
        else:
            self._captured_events = []
        try:
            output_tar = self._package_tarball(ts)
        except Exception as exc:  # noqa: BLE001
            self._set("⚠️ 打包失败", ORANGE,
                      f"{type(exc).__name__}: {exc}")
            return

        if output_tar.exists():
            size_mb = output_tar.stat().st_size / (1024 * 1024)
            self._set("✓ 录制完成", GREEN,
                      f"{output_tar.name} ({size_mb:.1f} MB) 已保存。"
                      f"正在验证买家规格…")
            self._hint.config(
                text=f"已保存: {output_tar}",
                fg=GREEN,
            )
            # v0.6.0: window was iconified when arm was pressed to free
            # MC focus. MC has now exited, so restore our window so the
            # tester sees the green "✓ 录制完成" verdict without needing
            # to click the taskbar.
            self.after(0, self._restore_window)
            # v0.7.0: integrated buyer-spec validation. Howard 2026-05-05
            # "不知道有没有录到买家想要的东西" — answer the question
            # directly inside the recorder, no need to launch a separate
            # tool. We import lint_v3 lazily so its numpy/PIL deps don't
            # delay startup.
            self._auto_lint(output_tar)
            # v0.21.0: shift-left BFT N=4 self-verification — Howard
            # 战略反馈"最重要确保数据是对的". Lint v3 is a shallow check
            # (24 PRD criteria); BFT runs the full PINNs residual stack
            # across 4 independent verifiers and surfaces specific
            # disagreements so tester knows what to re-record.
            self._auto_bft(output_tar)
            # Engineer-side telemetry: push the full session log to a
            # remote pastebin so engineering can curl <url> and see what
            # happened on tester's machine without asking for files.
            def _on_url(url: Optional[str]) -> None:
                if url:
                    self.after(0, lambda: self._hint.config(
                        text=f"已保存: {output_tar}\n远程日志: {url}",
                        fg=GREEN,
                    ))
            _upload_log_in_background(_on_url)
        else:
            self._set("⚠️ 录制结束但文件未生成", ORANGE,
                      "请联系工程师并截图本窗口。")

    def _package_tarball(self, ts: str) -> Path:
        """Package the recording into a 5-file PRD-shaped tarball.

        Layout (per docs/CONSUMER_QA_CHECKLIST.md):
            clip-YYYYMMDD-HHMMSS.tar.gz
            ├── video.mp4            (real recording)
            ├── systeminfo.json      (window geometry — best-effort)
            ├── action_camera.json   (placeholder; full impl in Rust app)
            ├── gameinfo.xlsx        (placeholder; full impl in Rust app)
            └── depth/               (empty; full impl needs G198 shader)

        This will FAIL G165 lint (24/24 PRD criteria) on depth/audio,
        but at least gets the SHAPE right so the validator's structural
        checks can pass and engineers see exactly which fields are stubbed.
        """
        clip_dir = self._tmp_dir / f"clip-{ts}"
        clip_dir.mkdir(parents=True, exist_ok=True)

        # Compute recording duration once, up front: the frame-count math
        # below (action_camera.json) and the partial-duration check at the
        # end both need it. Reading time.time() now vs. later differs by
        # microseconds — irrelevant for 30 fps frame bucketing.
        elapsed_sec = max(0.0, time.time() - self._record_started_at)

        # 1. Move the real video into place.
        if self._video_path and self._video_path.exists():
            shutil.move(str(self._video_path), str(clip_dir / "video.mp4"))

        # 2. systeminfo.json — v0.10.0: now uses the proper engineering
        # helper bin/generate_systeminfo_json.build_systeminfo() rather
        # than my hand-rolled dict. Same function the buyer-spec
        # pipeline uses, so backend ingest accepts it without special-casing.
        rect = self._mc_window_rect or {}
        try:
            import generate_systeminfo_json as gsi  # noqa: PLC0415
            sys_info = gsi.build_systeminfo(
                game_process_name="javaw.exe",
                x=int(rect.get("x", 0)),
                y=int(rect.get("y", 0)),
                width=int(rect.get("width", 1920)),
                height=int(rect.get("height", 1080)),
                record_dpi=float(rect.get("recordDpi", 96)) / 96.0,  # ratio
            )
            sys_info["recordedAt"] = ts
            sys_info["recorderVersion"] = "lite-v0.10.0"
            sys_info["_real_window_geometry"] = bool(self._mc_window_rect)
        except Exception as e:  # noqa: BLE001
            _trace(f"systeminfo: helper import failed, using stub ({e})")
            sys_info = {
                "gameProcessName": rect.get("title", "Minecraft"),
                "x": rect.get("x", 0),
                "y": rect.get("y", 0),
                "width": rect.get("width", 1920),
                "height": rect.get("height", 1080),
                "recordDpi": rect.get("recordDpi", 96),
                "recordedAt": ts,
                "recorderVersion": "lite-v0.10.0-fallback",
            }
        (clip_dir / "systeminfo.json").write_text(
            json.dumps(sys_info, indent=2), encoding="utf-8"
        )

        # 3. action_camera.json — v0.19.0 BIG REWRITE: PRD-aligned schema
        # was event-based with mouseX/cameraX scalars; PRD wants 9000
        # frame-aligned records at 30Hz. Sample/sample_tarball_builder.py
        # is the canonical schema reference.
        from datetime import datetime as _dt, timedelta as _td  # noqa: PLC0415

        FPS = 30.0
        target_frame_count = int(elapsed_sec * FPS) if elapsed_sec > 0 else 9000
        SCREEN_W, SCREEN_H = 1920, 1080
        # Build per-frame state by replaying captured pynput events into
        # frame buckets. Each frame inherits the last-known mouse/key
        # state (latching).
        events = sorted(
            self._captured_events,
            key=lambda e: e.get("timestamp_ms", 0),
        )
        cur_keys: list[int] = []
        cur_mx, cur_my = SCREEN_W // 2, SCREEN_H // 2
        prev_mx, prev_my = cur_mx, cur_my
        ev_idx = 0
        base_time = _dt.fromtimestamp(self._record_started_at)
        # Use intrinsics computed from window geometry (recorder.intrinsics.yaml
        # FOV 70° → fy = 540 / tan(35°) ≈ 771.4).
        fy = 540.0 / 0.7002075382097097  # tan(35°) = 0.7002...
        intrinsics = {"fx": round(fy, 3), "fy": round(fy, 3),
                      "cx": 960.0, "cy": 540.0}

        # R01 iron-law: load real game-state from Fabric mod JSONL.
        # On v0.26.0+ this is MANDATORY — hard-fail if missing, unless
        # --allow-placeholder was explicitly passed.
        try:
            from game_state_overlay import load as _gs_load, lookup_at_ms as _gs_lookup, apply_to_record as _gs_apply  # type: ignore  # noqa: PLC0415
        except ImportError:
            _gs_load = _gs_lookup = _gs_apply = None  # type: ignore

        _gs_samples = _gs_load() if _gs_load else None
        if _gs_samples:
            _trace(f"package: real game-state JSONL found, {len(_gs_samples)} samples — overlay enabled")
        else:
            ver = _recorder_version_tuple()
            allow_placeholder = getattr(self, "_allow_placeholder", False)
            if ver >= (0, 26, 0) and not allow_placeholder:
                supported_str = ", ".join(SUPPORTED_MC_VERSIONS)
                raise RecorderError(
                    "Real game-state Fabric mod not loaded.\n"
                    f"Detected MC version: {_parse_mc_version_from_title((self._mc_window_rect or {}).get('title', '')) or 'unknown'}\n"
                    f"Supported mod builds:  {supported_str}\n"
                    "Download from:        https://github.com/howardleegeek/oyster-gamedata-pipeline/releases/latest\n"
                    r"Install path:         %APPDATA%\.minecraft\mods" "\n"
                    "Tarball NOT created."
                )
            if allow_placeholder:
                _trace("package: --allow-placeholder active — using placeholder camera/player fields")
            else:
                _trace("package: no game-state JSONL — using placeholder camera/player fields (pre-v0.26.0)")

        action_records = []
        for f in range(target_frame_count):
            # Frame-relative timestamp in ms
            f_ms = int(f * 1000.0 / FPS)
            t = base_time + _td(milliseconds=f_ms)
            t_str = t.strftime("%Y-%m-%d %H:%M:%S.") + f"{t.microsecond // 1000:03d}"
            # Apply all events whose timestamp is ≤ this frame's window
            while ev_idx < len(events) and events[ev_idx].get("timestamp_ms", 0) <= f_ms:
                ev = events[ev_idx]
                et = ev.get("event_type", "")
                if et == "key_down":
                    kc = int(ev.get("keyCode", -1))
                    if kc >= 0 and kc not in cur_keys:
                        cur_keys.append(kc)
                elif et == "key_up":
                    kc = int(ev.get("keyCode", -1))
                    if kc in cur_keys:
                        cur_keys.remove(kc)
                elif et in ("mouse_move", "mouse_click"):
                    cur_mx = int(ev.get("mouseX", cur_mx))
                    cur_my = int(ev.get("mouseY", cur_my))
                ev_idx += 1
            # Normalized mouse coords (0..1) per PRD; deltas
            mx_n = cur_mx / SCREEN_W
            my_n = cur_my / SCREEN_H
            mdx = (cur_mx - prev_mx) / SCREEN_W
            mdy = (cur_my - prev_my) / SCREEN_H
            prev_mx, prev_my = cur_mx, cur_my
            rec = {
                "frame": f,
                "time": t_str,
                "fps": FPS,
                "route_type": 1,
                # PRD 文件2 字面：mouse_* 都是 list[float]，例 `{"mouse_x": [0.5]}`.
                # mouse_x/y ∈ [0, 1]，mouse_dx/dy ∈ [-1, 1] (带方向).
                "mouse_x": [mx_n],
                "mouse_y": [my_n],
                "mouse_dx": [mdx],
                "mouse_dy": [mdy],
                # PRD: keyCode is list[int] of currently-held keys (VK code, NOT ASCII).
                "keyCode": list(cur_keys) if cur_keys else [],
                # camera_* fields placeholder — Replay Mod postprocess
                # (G274) overwrites these when .mcpr present. For vanilla
                # tester these stay [0,64,0] / identity.
                "camera_position": [0.0, 64.0, 0.0],
                # PRD page 4 字面 'camera_rotation_oula' (拼音). DO NOT rename to euler.
                "camera_rotation_oula": [0.0, 0.0, 0.0],
                "camera_rotation_quaternion": [0.0, 0.0, 0.0, 1.0],
                # PRD page 4 字面 'camera_Follow Offset' (带空格 + 大写 F).
                # DO NOT rename to snake_case. Quirky but PRD-mandated.
                "camera_Follow Offset": [0.0, 1.6, 0.0],
                "camera_intrinsics": intrinsics,
                "camera_speed": [0.0, 0.0, 0.0],
                "player_position": [0.0, 64.0, 0.0],
                # PRD page 5 字面 'player_rotation_oula' (拼音). DO NOT rename.
                "player_rotation_oula": [0.0, 0.0, 0.0],
                "player_rotation_quaternion": [0.0, 0.0, 0.0, 1.0],
                "player_speed": [0.0, 0.0, 0.0],
                "metric_scale": 1.0,
                # R18 session-binding: identifies which recording this
                # frame belongs to. Verified against session_manifest.json.
                "session_id": getattr(self, "_session_id", ""),
            }
            # Howard 2026-05-07: if the mod is installed, overlay real
            # camera/player fields from the JSONL onto this record.
            if _gs_samples and _gs_apply:
                sample = _gs_lookup(_gs_samples, f_ms)
                if sample is not None:
                    _gs_apply(rec, sample)
            action_records.append(rec)

        (clip_dir / "action_camera.json").write_text(
            # PRD format: top-level array of records, no wrapper dict.
            # sample_tarball_builder.py writes this format too.
            json.dumps(action_records, separators=(",", ":"), ensure_ascii=False),
            encoding="utf-8",
        )

        # v0.20.2: write inputs.jsonl — raw pynput events (key_down, key_up,
        # mouse_move, mouse_click) with millisecond timestamps. Producer-side
        # artifact for R13 multimodal residual (keyCode-vs-input-replay)
        # which closes the FI-02 blind spot in BFT N=4 single-modal mesh.
        # See docs/SPEC_R13_MULTIMODAL.md § R13. Per IL10, this file is OK
        # to be missing on legacy/headless runs — R13 will ABSTAIN, not FAIL.
        try:
            with (clip_dir / "inputs.jsonl").open("w", encoding="utf-8") as fh:
                # First line: session_start sentinel (frame-time alignment).
                fh.write(json.dumps({
                    "event_type": "session_start",
                    "timestamp_ms": 0,
                    "fps": FPS,
                    "frame_count": target_frame_count,
                    # R18: session_id ties this inputs.jsonl to the same
                    # session_manifest.json that action_camera frames cite.
                    "session_id": getattr(self, "_session_id", ""),
                }) + "\n")
                for ev in self._captured_events:
                    fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
            _trace(f"package: wrote inputs.jsonl ({len(self._captured_events)} events)")
        except Exception as e:
            _trace(f"package: inputs.jsonl write failed: {e}")
            # Non-fatal: action_camera.json still ships; R13 will ABSTAIN.

        # R18: write session_manifest.json so consumer-side R18 residual can
        # bind every artifact (action_camera frames + inputs.jsonl session_start)
        # to a single recording. Closes red-team B-05 (Frankenstein splice).
        try:
            (clip_dir / "session_manifest.json").write_text(
                json.dumps({
                    "session_id": getattr(self, "_session_id", ""),
                    "recorder_version": "lite-v0.21.0",
                    "start_time": _dt.fromtimestamp(self._record_started_at).isoformat(),
                    "frame_count": target_frame_count,
                    "fps": FPS,
                }, indent=2),
                encoding="utf-8",
            )
            _trace("package: wrote session_manifest.json")
        except Exception as e:  # noqa: BLE001
            _trace(f"package: session_manifest.json write failed: {e}")
            # Non-fatal: R18 will ABSTAIN on missing manifest.

        # 4. gameinfo.xlsx — uses bin/generate_gameinfo_xlsx.write_xlsx() for
        # a real 14-field xlsx. Howard 2026-05-06 Iron Law: NO PLACEHOLDER
        # FALLBACK. If the helper fails, the clip is unusable; fail loud
        # so the tester can re-run rather than ship a stub.
        import generate_gameinfo_xlsx as ggx  # noqa: PLC0415
        video_dur = max(0.0, time.time() - self._record_started_at)
        game_info = ggx.build_gameinfo_dict(
            game_name="Minecraft",
            game_version="1.20.4",
            platform="Java Edition",
            scene_name="overworld",
            weather="clear",
            time_of_day="day",
            character_name="DataPilot",
            character_class="player",
            operator_id="lite-recorder",
            total_frames=int(video_dur * 30),  # 30 fps locked
            video_duration_sec=video_dur,
            route_type=1,
            notes=f"recorded by {RECORDER_VERSION} at {ts}",
        )
        ggx.write_xlsx(game_info, str(clip_dir / "gameinfo.xlsx"))

        # 5. depth/ — Howard 2026-05-07: REAL DepthAnything V2 inference on
        # the recorded video.mp4. Iron Law: NO PLACEHOLDER. The .exe is
        # built with torch + transformers + DepthAnything model bundled via
        # PyInstaller (build-recorder-exe.yml --add-data). Inference runs
        # CPU-side on the local machine; ~1-3 sec/frame at 256×256.
        #
        # If the bundled model fails to load (corrupted weights, missing
        # torch), the recording ABORTS the tarball — we refuse to ship
        # without real depth.
        #
        # rc9 (Howard 2026-05-09): show a depth-progress UI to the tester
        # so they don't think the recorder is hung during the 30-60 min
        # CPU pass. Also honour the cooperative skip flag — if the tester
        # clicks "跳过深度图" (or no GPU was detected and the default-skip
        # was left armed), we ship the tarball WITHOUT a depth/ directory.
        # Iron-law-compatible: a skipped tarball is partial-by-choice, not
        # a placeholder; downstream lint will FAIL on missing depth which
        # is the correct behaviour.
        depth_skipped = False
        try:
            from depth_anything_v2_inference import infer_depth_for_video  # noqa: PLC0415
            video_path = clip_dir / "video.mp4"
            depth_dir = clip_dir / "depth"
            _trace(f"depth: running DepthAnything V2 inference on {video_path}")
            self.after(0, self._show_depth_progress_ui)
            try:
                manifest = infer_depth_for_video(
                    video_path,
                    depth_dir,
                    model_variant="vits",
                    device="cpu",
                    progress_callback=self._on_depth_progress,
                    should_skip=self._skip_depth_flag.is_set,
                )
                _trace(f"depth: rendered {len(manifest)} REAL EXR frames")
                if self._skip_depth_flag.is_set():
                    # Cooperative skip raced past the loop's last poll.
                    depth_skipped = True
                    _trace("depth: skip flag observed after loop — treating as user skip")
            finally:
                self.after(0, self._hide_depth_progress_ui)
            if depth_skipped:
                # Drop the partial depth dir so the tarball cleanly OMITS
                # depth/ rather than shipping a half-finished version.
                try:
                    if depth_dir.exists():
                        shutil.rmtree(depth_dir, ignore_errors=True)
                except Exception:
                    pass
                _trace("package: depth skipped by user — partial tarball")
                # Surface the result to the tester before the lint runs.
                self._set("⚠️ 已跳过深度图", "#d97706",
                          "tarball 完成，但深度数据未包含。\n"
                          "下游买家规格会在深度项标记 FAIL。")
        except Exception as e:
            self.after(0, self._hide_depth_progress_ui)
            _trace(f"depth: DepthAnything inference FAILED: {e!r}")
            # No fake fallback — abort the entire packaging. The tester
            # sees a clear error in the log; we never ship placeholder.
            raise RuntimeError(
                f"Depth inference failed: {e}. The recorder refuses to "
                f"ship a tarball with placeholder depth. See "
                f"~/OysterRecorder.log for details."
            )

        # R22 (D-04 defense): hash every *.exr in depth/ and write
        # depth_manifest.json next to the directory. Stop-gap path
        # has zero EXR files so the manifest is the empty object {},
        # which still lets the consumer-side R22 vote PASS instead of
        # ABSTAIN once the full recorder ships EXR frames.
        try:
            import hashlib as _hashlib  # noqa: PLC0415
            depth_dir_path = clip_dir / "depth"
            depth_manifest: dict[str, str] = {}
            for exr_path in sorted(depth_dir_path.glob("*.exr")):
                sha = _hashlib.sha256()
                with exr_path.open("rb") as fh:
                    for chunk in iter(lambda fh=fh: fh.read(1 << 20), b""):
                        sha.update(chunk)
                depth_manifest[exr_path.name] = sha.hexdigest()
            (clip_dir / "depth_manifest.json").write_text(
                json.dumps(depth_manifest, indent=2),
                encoding="utf-8",
            )
            _trace(f"package: wrote depth_manifest.json ({len(depth_manifest)} entries)")
        except Exception as e:  # noqa: BLE001
            _trace(f"package: depth_manifest.json write failed: {e}")
            # Non-fatal — R22 will ABSTAIN on missing manifest, same as
            # behaviour before this hook landed.

        # 6. v0.18.0: intrinsics.yaml — buyer expects fx/fy/Cx/Cy.
        # Standard MC at 1920x1080 with default 70° vertical FOV:
        #   fy = (height/2) / tan(FOV_v/2) = 540 / tan(35°) ≈ 771.4
        #   fx = fy (square pixels)  per criterion 12 (fx==fy)
        #   Cx = width/2 = 960, Cy = height/2 = 540
        # MC's actual FOV is user-settable so this is a default; full
        # Rust recorder will read FOV from game config.
        import math  # noqa: PLC0415 — local import keeps cold-start fast
        fov_v_deg = 70.0
        focal = (1080 / 2) / math.tan(math.radians(fov_v_deg / 2))
        intrinsics = {
            "fx": round(focal, 3),
            "fy": round(focal, 3),
            # PRD 文件2 例：cx/cy 小写（JSON wire 格式权威）。
            # 表格描述写大写 Cx/Cy 是 PRD 内部不一致，wire 例为准.
            "cx": 960.0,
            "cy": 540.0,
            "width": 1920,
            "height": 1080,
            "fov_vertical_deg": fov_v_deg,
            "_note": "default MC 70° vertical FOV; tester may have changed in-game",
        }
        try:
            import yaml  # noqa: PLC0415
            (clip_dir / "intrinsics.yaml").write_text(
                yaml.safe_dump(intrinsics, sort_keys=False),
                encoding="utf-8",
            )
        except Exception:
            # Fall back to a plain text file in YAML-ish format.
            (clip_dir / "intrinsics.yaml").write_text(
                "\n".join(f"{k}: {v}" for k, v in intrinsics.items()),
                encoding="utf-8",
            )

        # 7. v0.18.0: duration enforcement — if recording <5 min the
        # buyer-spec rejects (criterion 2 wants 5-6 min). We can't
        # extend a too-short recording, but we mark it 'partial' in
        # systeminfo so backend ingest can quarantine + ask tester to
        # redo. (elapsed_sec computed at top of method.)
        sys_info["actual_duration_sec"] = round(elapsed_sec, 1)
        sys_info["partial"] = elapsed_sec < 300.0  # <5 min
        # Re-write systeminfo.json with the new fields.
        (clip_dir / "systeminfo.json").write_text(
            json.dumps(sys_info, indent=2), encoding="utf-8"
        )

        # R01: if --allow-placeholder is active and JSONL was missing,
        # stamp metadata.json with data_authenticity='placeholder' so
        # buyers can identify non-real game-state tarballs.
        if getattr(self, "_allow_placeholder", False) and not _gs_samples:
            meta = {
                "data_authenticity": "placeholder",
                "warning": "camera/player fields are constant [0.0, 64.0, 0.0]",
            }
            (clip_dir / "metadata.json").write_text(
                json.dumps(meta, indent=2), encoding="utf-8"
            )
            _trace("package: wrote metadata.json with data_authenticity=placeholder")

        # Write the tarball into the user's Documents/OysterClips/.
        out_tar = _output_dir() / f"clip-{ts}.tar.gz"
        with tarfile.open(out_tar, "w:gz") as tf:
            tf.add(clip_dir, arcname=f"clip-{ts}")

        # Cleanup tmp dir.
        try:
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
        except Exception:
            pass
        return out_tar

    def _detect_audio_device(self) -> Optional[str]:
        """Probe ffmpeg dshow for the first audio input device.

        Returns the alt-name for use as `-i audio=<name>`. Returns None
        if no device found or probe fails — caller should record video
        only in that case. v0.11.0.
        """
        if os.name != "nt":
            return None
        try:
            res = subprocess.run(
                [str(_FFMPEG), "-hide_banner", "-list_devices", "true",
                 "-f", "dshow", "-i", "dummy"],
                capture_output=True, timeout=8, text=True,
                creationflags=0x08000000 if os.name == "nt" else 0,
            )
            # ffmpeg writes device list to stderr
            output = res.stderr or res.stdout or ""
        except Exception as e:
            _trace(f"audio_probe: ffmpeg list_devices failed: {e}")
            return None

        # Parse "DirectShow audio devices" section, capture device names.
        # Format: [dshow @ 0x...]  "Microphone (Realtek...)" (audio)
        in_audio = False
        first_audio = None
        for line in output.splitlines():
            if "DirectShow audio devices" in line:
                in_audio = True
                continue
            if in_audio:
                if "DirectShow video devices" in line:
                    break
                # Match: '"<device-name>"'
                if '"' in line and "Alternative name" not in line:
                    name = line.split('"')[1] if '"' in line else None
                    if name and first_audio is None:
                        first_audio = name
                        break
        _trace(f"audio_probe: device={first_audio!r}")
        return first_audio

    def _start_ffmpeg(self, out_path: Path) -> None:
        """Spawn ffmpeg with gdigrab to record the Minecraft window.

        R01 v2 (iron-law-strict): ALWAYS uses cropped-desktop capture
        with geometry from mc_window rect. Title encoding is irrelevant —
        we use -offset_x/-offset_y/-video_size + -i desktop, which is
        fully locale-blind. Hard-fails if mc_window is None (no window
        detected). The old title-based branch has been removed.

        v0.11.0: also captures audio via dshow if a device is detected.
        Falls back to video-only if no audio device or audio capture
        fails to start.
        """
        # R01 iron-law: hard-fail if Minecraft window not detected.
        if self._mc_window_rect is None:
            raise RecorderError(
                "Minecraft window not detected. Is Minecraft running and visible?"
            )

        rect = self._mc_window_rect
        mc_title = rect.get("title", "")
        x = rect.get("x", 0)
        y = rect.get("y", 0)
        w = rect.get("width", 1920)
        h = rect.get("height", 1080)

        # R01 D section: parse MC version from title and warn if unsupported.
        mc_ver = _parse_mc_version_from_title(mc_title)
        if mc_ver and mc_ver not in SUPPORTED_MC_VERSIONS:
            _trace(
                f"WARN: Minecraft {mc_ver} not in supported list. "
                "Real game-state mod only loads on stable releases. "
                "Recording will hard-fail at packaging unless you switch "
                "to a supported version OR pass --allow-placeholder."
            )

        # Audio probe.
        audio_dev = self._detect_audio_device()
        audio_inputs = []
        audio_codec = []
        if audio_dev:
            audio_inputs = ["-f", "dshow", "-i", f"audio={audio_dev}"]
            audio_codec = ["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"]
            _trace(f"ffmpeg: capturing audio from '{audio_dev}'")
        else:
            _trace("ffmpeg: no audio device found, recording video only")

        # R01 v2: always cropped-desktop capture using detected geometry.
        # locale-blind — title encoding never participates in the ffmpeg cmd.
        video_input = [
            "-f", "gdigrab",
            "-framerate", "30",
            "-draw_mouse", "0",
            "-offset_x", str(x),
            "-offset_y", str(y),
            "-video_size", f"{w}x{h}",
            "-i", "desktop",
        ]
        _trace(
            f"ffmpeg: window-area capture title='{mc_title}' "
            f"geometry={x},{y},{w},{h}"
        )

        cmd = [
            str(_FFMPEG),
            *video_input,
            *audio_inputs,
            "-vf", "scale=1920:1080:flags=lanczos",
            "-c:v", "libx265",
            "-preset", "ultrafast",
            "-pix_fmt", "yuv420p",
            *audio_codec,
            "-r", "30",
            "-t", "360",
            "-y",
            str(out_path),
        ]
        # On Windows, CREATE_NO_WINDOW (0x08000000) hides the ffmpeg
        # console window so the tester only sees our Tk window.
        flags = 0x08000000 if os.name == "nt" else 0
        self._ffmpeg_proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )

    def _stop_ffmpeg(self) -> None:
        """Send 'q' to ffmpeg's stdin (clean shutdown), then wait 5s."""
        proc = self._ffmpeg_proc
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.write(b"q\n")
                proc.stdin.flush()
        except Exception:
            pass
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                proc.kill()
        self._ffmpeg_proc = None

    def _on_close(self) -> None:
        _trace("on_close: user closed window")
        self._stop_event.set()
        self._stop_ffmpeg()
        # Final telemetry push so engineer sees the full session log.
        # Synchronous-ish but capped to 15s by urlopen timeout.
        try:
            _upload_log_remote()
        except Exception:
            pass
        self.destroy()

    # ---- UI updates (thread-safe via after) -----------------------------
    def _set(self, big: str, color: str, sub: str) -> None:
        def apply():
            self._verdict.config(text=big, fg=color)
            self._subtitle.config(text=sub, fg=color)
        # Tk requires UI updates from the main thread.
        try:
            self.after(0, apply)
        except RuntimeError:
            pass


def _emergency_error_box(exc: BaseException) -> None:
    # Best-effort: push the local log to remote pastebin BEFORE showing
    # the dialog, so even if the tester ignores the dialog and force-
    # quits, engineering already has the log.
    _trace(f"=== EMERGENCY: {type(exc).__name__}: {exc} ===")
    _trace(traceback.format_exc())
    remote_url = None
    try:
        remote_url = _upload_log_remote()
    except Exception:
        pass

    try:
        root = tk.Tk()
        root.withdraw()
        msg = (
            "录制器启动失败。\n\n"
            f"{type(exc).__name__}: {exc}\n\n"
        )
        if remote_url:
            msg += (
                f"日志已自动上传，工程师可访问：\n{remote_url}\n\n"
                "你不用做任何事，工程师会从这个链接看到出错原因。"
            )
        else:
            msg += (
                f"日志在本机：{_STARTUP_LOG}\n"
                "如果方便，把这个文件发给工程师。"
            )
        messagebox.showerror(
            title="Oyster 录制器 — 启动错误",
            message=msg,
        )
        root.destroy()
    except Exception:
        log = Path.home() / "OysterRecorder-error.log"
        try:
            log.write_text(f"=== startup error ===\n{traceback.format_exc()}\n")
        except Exception:
            pass


def _try_install_mod_first_launch() -> None:
    """Howard 2026-05-07 (D17): on every launch, best-effort try to
    install the bundled Fabric loader + Oyster mod into the user's MC.

    Idempotent — skipped on subsequent launches when already installed.
    Fails completely silently if anything goes wrong (no tray pop-up, no
    crash). The recorder still works without the mod; action_camera just
    falls back to placeholder camera/player fields per the existing
    fallback path.

    PyInstaller bundles the two jars via ``--add-data``; we resolve their
    paths via ``sys._MEIPASS`` when running as a frozen .exe.
    """
    try:
        # Resolve bundle dir: frozen → _MEIPASS, source → script dir
        if hasattr(sys, "_MEIPASS"):
            bundle = Path(sys._MEIPASS)
        else:
            bundle = Path(__file__).resolve().parent
        fabric_installer = bundle / "fabric-installer.jar"
        mod_jar = next(bundle.glob("oyster-recorder-mod-*.jar"), None)
        if mod_jar is None or not fabric_installer.exists():
            # Bundled assets missing — likely a dev run from source. No-op.
            return
        # Defer the import — only available when running with our wheel,
        # not in legacy contexts.
        sys.path.insert(0, str(bundle))
        from install_fabric_loader import ensure_installed  # type: ignore
        result = ensure_installed(fabric_installer, mod_jar)
        _trace(f"mod-install: {result.to_dict()}")
    except Exception as e:  # noqa: BLE001 — never crash recorder
        try:
            _trace(f"mod-install failed (non-fatal): {e}")
        except Exception:
            pass


def main() -> int:
    """Entry point for OysterRecorder CLI.

    Parses command-line arguments, initializes the RecorderApp, and runs
    the GUI event loop. Returns exit code: 0 on success, 2 on error.

    Args:
        --allow-placeholder: If set, allows placeholder camera/player
            fields (marks tarball as non-real data).

    Returns:
        Exit code: 0 for normal exit, 2 for error.
    """
    import argparse  # noqa: PLC0415
    parser = argparse.ArgumentParser(description="OysterRecorder")
    parser.add_argument(
        "--allow-placeholder",
        action="store_true",
        default=False,
        help="Allow placeholder camera/player fields (marks tarball as non-real)",
    )
    args, _unknown = parser.parse_known_args()

    # R01 D section: print supported MC versions at startup.
    supported_str = ", ".join(SUPPORTED_MC_VERSIONS)
    _trace(
        f"OysterRecorder {RECORDER_VERSION} — supported Minecraft versions "
        f"for real game-state:\n  {supported_str}"
    )

    try:
        _try_install_mod_first_launch()
        app = RecorderApp()
        app._allow_placeholder = args.allow_placeholder  # type: ignore[attr-defined]
        app.mainloop()
        return 0
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001
        _emergency_error_box(exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
