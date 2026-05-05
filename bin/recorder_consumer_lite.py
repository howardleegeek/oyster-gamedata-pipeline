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
RECORDER_VERSION = "lite-v0.15.0"
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
    if os.name != "nt" or not getattr(sys, "frozen", False):
        # Only auto-update when running as a packaged .exe on Windows.
        return False
    if _is_onedir_install():
        _trace("update: SKIP — running as --onedir, refusing single-.exe overwrite")
        return False
    try:
        import urllib.request
        new_path = Path(tempfile.gettempdir()) / "OysterRecorder-update.exe"
        _trace(f"update: downloading {new_exe_url} -> {new_path}")
        with urllib.request.urlopen(new_exe_url, timeout=120) as resp, \
                new_path.open("wb") as fh:
            shutil.copyfileobj(resp, fh)
        _trace(f"update: downloaded {new_path.stat().st_size} bytes")
    except Exception as e:
        _trace(f"update: download failed {e}")
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
# We POST the full local log to ix.io (anonymous, free pastebin) on close
# / crash / record-complete. The returned URL is appended to the local log
# AND shown in the GUI subtitle so tester can read it off if asked. We do
# NOT block startup or recording on this network call — it runs on a
# daemon thread and any failure is silently logged.
TELEMETRY_ENDPOINT = "http://ix.io"


def _upload_log_remote() -> Optional[str]:
    """POST ~/OysterRecorder.log to ix.io. Returns short URL or None.

    Synchronous — caller is expected to run this on a daemon thread.
    Network failures and ix.io downtime degrade gracefully (return None;
    tester just doesn't see a remote URL, the local log file is still
    intact).
    """
    if not _STARTUP_LOG.exists():
        _trace("upload_log: no local log file yet")
        return None
    try:
        body = _STARTUP_LOG.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        _trace(f"upload_log: read failed: {exc}")
        return None
    if len(body) > 500_000:  # ix.io limit ~256KB; truncate the head.
        body = body[-450_000:]
    try:
        # urllib + form-encoded body — built-in to Python, no requests dep.
        import urllib.parse
        import urllib.request
        data = urllib.parse.urlencode({"f:1": body}).encode("ascii")
        req = urllib.request.Request(
            TELEMETRY_ENDPOINT,
            data=data,
            headers={"User-Agent": "OysterRecorder/lite"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            url = resp.read().decode("utf-8", errors="replace").strip()
        if url.startswith("http"):
            _trace(f"upload_log: success {url}")
            # Append URL to the local log so subsequent runs see it.
            try:
                with _STARTUP_LOG.open("a", encoding="utf-8") as fh:
                    fh.write(f"{datetime.now().isoformat()} REMOTE_LOG_URL={url}\n")
            except Exception:
                pass
            return url
    except Exception as exc:
        _trace(f"upload_log: POST failed: {exc}")
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
    from tkinter import messagebox
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
def _output_dir() -> Path:
    docs = Path.home() / "Documents" / "OysterClips"
    docs.mkdir(parents=True, exist_ok=True)
    return docs


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

        def on_move(x, y):  # noqa: ANN001
            with self._lock:
                self.events.append({
                    "timestamp_ms": self._now_ms(),
                    "event_type": "mouse_move",
                    "mouseX": int(x),
                    "mouseY": int(y),
                })

        def on_click(x, y, button, pressed):  # noqa: ANN001
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

    def stop(self) -> list[dict[str, Any]]:
        for L in (self._kbd_listener, self._mouse_listener):
            try:
                if L is not None:
                    L.stop()
            except Exception:
                pass
        with self._lock:
            return list(self.events)


def _write_minimal_xlsx(path: Path) -> None:
    """Write a minimum-viable single-sheet xlsx without an xlsx library.

    .xlsx is a zip of XML parts. The validator only needs the file to be
    a parseable zip with the OOXML sheet structure; placeholder content is
    fine. Hand-rolled to avoid depending on openpyxl/xlsxwriter inside
    PyInstaller (smaller .exe). The minimum parts the OOXML spec requires
    are: [Content_Types].xml, _rels/.rels, xl/workbook.xml,
    xl/_rels/workbook.xml.rels, and one xl/worksheets/sheetN.xml.
    """
    import zipfile

    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""

    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

    workbook = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
          xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="GameInfo" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>"""

    workbook_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""

    sheet1 = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1"><c r="A1" t="inlineStr"><is><t>placeholder - stop-gap recorder</t></is></c></row>
  </sheetData>
</worksheet>"""

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        zf.writestr("xl/worksheets/sheet1.xml", sheet1)


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
                f"如果出问题，请把 {_STARTUP_LOG} 截图给工程师。"
            ),
            font=("Helvetica", 9),
            bg="white",
            fg=TEXT_GRAY,
            wraplength=500,
            justify="center",
        )
        self._hint.pack(pady=(0, 10))

    def _upload_log_now(self) -> None:
        """Tester clicked '发送日志给工程师'. Upload then show URL."""
        _trace("user clicked send-log button")
        self._upload_btn.config(text="↗ 上传中…", state="disabled")

        def on_done(url: Optional[str]) -> None:
            def apply():
                if url:
                    self._upload_btn.config(
                        text=f"✓ 日志已上传 — {url}",
                        state="normal",
                    )
                    self._hint.config(
                        text=f"工程师查日志: {url}",
                        fg="#1976d2",
                    )
                else:
                    self._upload_btn.config(
                        text="✗ 上传失败 — 重试",
                        state="normal",
                    )
            self.after(0, apply)

        _upload_log_in_background(on_done)

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
        try:
            self._subtitle.config(
                text=f"⏱  {mm}分{ss:02d}秒  /  6 分钟  ({pct}%)\n[{bar}]\n📦 视频文件 {size_str}",
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
            # Get out of MC's way.
            try:
                self.iconify()
                _trace("window iconified to taskbar")
            except Exception as e:
                _trace(f"iconify failed: {e}")
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
        _trace("watch_loop: waiting for arm + MC")
        self._set("准备好", TEXT_GRAY,
                  "先打开 Minecraft 玩一会儿确认它不崩。\n确认后再点上面 ▶ 开始录制。")
        while not self._stop_event.is_set():
            if self._record_armed and _minecraft_running():
                break
            time.sleep(1.0)
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

        # 3. action_camera.json — REAL keyboard + mouse data from
        # InputCapture, plus placeholder camera/quaternion fields. The
        # Rust app's shader pack (G198) provides per-frame camera data;
        # in this stop-gap we expose only what we can collect from
        # user-space (key/mouse).
        action_records = []
        for ev in self._captured_events:
            rec: dict[str, Any] = {
                "timestamp_ms": ev.get("timestamp_ms", 0),
                "event_type": ev.get("event_type", "unknown"),
                # Required PRD field: keyCode is int. -1 for non-key events.
                "keyCode": int(ev.get("keyCode", -1)),
                # Mouse position (real for mouse events, 0 otherwise).
                "mouseX": int(ev.get("mouseX", 0)),
                "mouseY": int(ev.get("mouseY", 0)),
                # Camera fields placeholder until Rust app's depth shader.
                "cameraX": 0.0, "cameraY": 0.0, "cameraZ": 0.0,
                # Quaternion xyzw order per PRD (criterion 14).
                "cameraQX": 0.0, "cameraQY": 0.0,
                "cameraQZ": 0.0, "cameraQW": 1.0,
            }
            # Carry button info on mouse_click for downstream ML.
            if "button" in ev:
                rec["mouseButton"] = str(ev["button"])
                rec["pressed"] = bool(ev.get("pressed", False))
            action_records.append(rec)

        (clip_dir / "action_camera.json").write_text(
            json.dumps(
                {
                    "_placeholder_camera": True,
                    "_note": (
                        "key + mouse fields are real; camera/quaternion "
                        "fields are placeholder until Rust app's depth "
                        "shader (G198) lands"
                    ),
                    "recordCount": len(action_records),
                    "records": action_records,
                },
                separators=(",", ":"),  # compact for large arrays
            ),
            encoding="utf-8",
        )

        # 4. gameinfo.xlsx — v0.10.0: uses bin/generate_gameinfo_xlsx
        # write_xlsx() for a real 14-field xlsx instead of my
        # _write_minimal_xlsx hand-rolled stub. Same fields buyer
        # ingest expects.
        try:
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
                notes=f"recorded by recorder_consumer_lite v0.10.0 at {ts}",
            )
            ggx.write_xlsx(game_info, str(clip_dir / "gameinfo.xlsx"))
        except Exception as e:  # noqa: BLE001
            _trace(f"gameinfo: helper failed ({e}), using minimal stub")
            _write_minimal_xlsx(clip_dir / "gameinfo.xlsx")

        # 5. depth/ — empty directory placeholder. Real version needs
        # G198 depth shader pack injecting per-frame z-buffer EXR files.
        (clip_dir / "depth").mkdir(exist_ok=True)
        (clip_dir / "depth" / "_README.txt").write_text(
            "Stop-gap recorder does not produce per-frame depth files.\n"
            "Full Rust recorder + G198 shader pack adds 1800 .exr frames here.\n",
            encoding="utf-8",
        )

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

        gdigrab title="Minecraft*" filter records ONLY the MC window. If
        no exact match, falls back to full desktop capture.

        v0.11.0: also captures audio via dshow if a device is detected.
        Falls back to video-only if no audio device or audio capture
        fails to start.
        """
        # H.265 encoded MP4, output locked to 1920x1080 @ 30 fps to
        # match the buyer-spec PRD (criteria 1 + 3). -draw_mouse 0 hides
        # cursor in captured frames. -framerate before -i sets input rate.
        # -vf scale=1920:1080 forces output regardless of monitor res
        # (tester might have 4K, ultrawide, etc).
        # -t 360 = 6-minute hard cap so even if Python misses MC exit,
        # ffmpeg self-terminates and lint won't reject for over-length.
        # v0.11.0: try to capture audio alongside video.
        audio_dev = self._detect_audio_device()
        audio_inputs = []
        audio_codec = []
        if audio_dev:
            audio_inputs = ["-f", "dshow", "-i", f"audio={audio_dev}"]
            # AAC at 128kbps, 48kHz stereo — common buyer-spec compatible.
            audio_codec = ["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"]
            _trace(f"ffmpeg: capturing audio from '{audio_dev}'")
        else:
            _trace("ffmpeg: no audio device found, recording video only")

        # v0.14.0: prefer window-title capture so we only record MC, not
        # the entire desktop (which includes our own GUI, browser, etc).
        # Falls back to full desktop if window not found at ffmpeg start
        # time. The MC window title was already detected in
        # _get_minecraft_window_rect for systeminfo.json, so use it here
        # too if available — gives ffmpeg a stable handle that survives
        # MC moving / resizing.
        if self._mc_window_rect and self._mc_window_rect.get("title"):
            mc_title = self._mc_window_rect["title"]
            video_input = ["-f", "gdigrab", "-framerate", "30",
                           "-draw_mouse", "0",
                           "-i", f"title={mc_title}"]
            _trace(f"ffmpeg: window-mode capture title='{mc_title}'")
        else:
            video_input = ["-f", "gdigrab", "-framerate", "30",
                           "-draw_mouse", "0",
                           "-i", "desktop"]
            _trace("ffmpeg: full-desktop capture (no MC window detected)")

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


def main() -> int:
    try:
        app = RecorderApp()
        app.mainloop()
        return 0
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001
        _emergency_error_box(exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
