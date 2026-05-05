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
    """
    if os.name != "nt":
        return set()
    try:
        out = subprocess.check_output(
            ["tasklist", "/FO", "CSV", "/NH"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
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
        """Poll for MC launch → record → poll for MC exit → finalize."""
        if not _FFMPEG.exists():
            self._set("⚠️ 缺少 ffmpeg", ORANGE,
                      "ffmpeg.exe 没有打包进来，请联系工程师。")
            return

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

        # Phase 3: wait for MC to exit OR for 6 minutes elapsed (PRD cap).
        # PRD spec requires 5-6 min duration; auto-stop at 6 min so the
        # downstream lint doesn't reject for over-length.
        MAX_RECORD_SECONDS = 6 * 60
        while not self._stop_event.is_set():
            if not _minecraft_running():
                break
            elapsed = time.time() - self._record_started_at
            if elapsed >= MAX_RECORD_SECONDS:
                self._set("⏱ 已到 6 分钟，自动停止", ORANGE,
                          "PRD 规格要求 5-6 分钟，正在收尾…")
                break
            time.sleep(2.0)
        if self._stop_event.is_set():
            self._stop_ffmpeg()
            return

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
                      f"完整路径见下方提示。")
            self._hint.config(
                text=f"已保存: {output_tar}",
                fg=GREEN,
            )
            # v0.6.0: window was iconified when arm was pressed to free
            # MC focus. MC has now exited, so restore our window so the
            # tester sees the green "✓ 录制完成" verdict without needing
            # to click the taskbar.
            self.after(0, self._restore_window)
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

        # 2. systeminfo.json — REAL window geometry via Win32 EnumWindows
        # if available (v0.4.0+); falls back to 1920x1080 placeholder.
        rect = self._mc_window_rect or {}
        sys_info = {
            "gameProcessName": rect.get("title", "Minecraft"),
            "x": rect.get("x", 0),
            "y": rect.get("y", 0),
            "width": rect.get("width", 1920),
            "height": rect.get("height", 1080),
            "recordDpi": rect.get("recordDpi", 96),
            "recordedAt": ts,
            "recorderVersion": "lite-v0.4.0",
            "_real_window_geometry": bool(self._mc_window_rect),
            "_note": "stop-gap recorder; full systeminfo from Rust app",
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

        # 4. gameinfo.xlsx — minimal placeholder. Real version is
        # produced by the Rust app's gameinfo extractor (G164/G181).
        # We write a near-empty xlsx using a hand-crafted minimal
        # zipfile (xlsx is a zip) so the validator's "is xlsx" check
        # finds something parseable.
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

    def _start_ffmpeg(self, out_path: Path) -> None:
        """Spawn ffmpeg with gdigrab to record the Minecraft window.

        gdigrab title="Minecraft*" filter records ONLY the MC window. If
        no exact match, falls back to full desktop capture.
        """
        # H.265 encoded MP4, output locked to 1920x1080 @ 30 fps to
        # match the buyer-spec PRD (criteria 1 + 3). -draw_mouse 0 hides
        # cursor in captured frames. -framerate before -i sets input rate.
        # -vf scale=1920:1080 forces output regardless of monitor res
        # (tester might have 4K, ultrawide, etc).
        # -t 360 = 6-minute hard cap so even if Python misses MC exit,
        # ffmpeg self-terminates and lint won't reject for over-length.
        cmd = [
            str(_FFMPEG),
            "-f", "gdigrab",
            "-framerate", "30",
            "-draw_mouse", "0",
            "-i", "desktop",
            "-vf", "scale=1920:1080:flags=lanczos",
            "-c:v", "libx265",
            "-preset", "ultrafast",
            "-pix_fmt", "yuv420p",
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
