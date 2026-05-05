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

import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

import tkinter as tk
from tkinter import messagebox

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

        self._build_ui()
        # Start background watcher immediately — testers do not click anything.
        threading.Thread(target=self._watch_loop, daemon=True).start()

        # Clean shutdown if window is closed mid-recording.
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        # Status banner (fills most of the window)
        self._verdict = tk.Label(
            self,
            text="…",
            font=("Helvetica", 36, "bold"),
            bg="white",
            fg=TEXT_GRAY,
            height=2,
        )
        self._verdict.pack(fill="x", padx=20, pady=(40, 8))

        self._subtitle = tk.Label(
            self,
            text="正在等待 Minecraft 启动…",
            font=("Helvetica", 13),
            bg="white",
            fg=TEXT_GRAY,
            wraplength=480,
        )
        self._subtitle.pack(pady=(0, 4))

        # Spacer
        tk.Frame(self, bg="white").pack(expand=True, fill="both")

        # Tiny output-dir hint at the bottom
        self._hint = tk.Label(
            self,
            text=f"录制完成后会保存到: {_output_dir()}",
            font=("Helvetica", 9),
            bg="white",
            fg=TEXT_GRAY,
            wraplength=500,
        )
        self._hint.pack(pady=(0, 12))

    # ---- worker --------------------------------------------------------
    def _watch_loop(self) -> None:
        """Poll for MC launch → record → poll for MC exit → finalize."""
        if not _FFMPEG.exists():
            self._set("⚠️ 缺少 ffmpeg", ORANGE,
                      "ffmpeg.exe 没有打包进来，请联系工程师。")
            return

        # Phase 1: wait for MC to start
        self._set("…", TEXT_GRAY, "等待 Minecraft 启动…")
        while not self._stop_event.is_set():
            if _minecraft_running():
                break
            time.sleep(2.0)
        if self._stop_event.is_set():
            return

        # Phase 2: start recording
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        self._output_path = _output_dir() / f"clip-{ts}.mp4"
        try:
            self._start_ffmpeg(self._output_path)
        except Exception as exc:  # noqa: BLE001
            self._set("⚠️ 录制启动失败", ORANGE,
                      f"{type(exc).__name__}: {exc}")
            return

        self._set("● 正在录制", RED,
                  "玩你的 Minecraft 即可，退出游戏会自动停止录制。")

        # Phase 3: wait for MC to exit
        while not self._stop_event.is_set():
            if not _minecraft_running():
                break
            time.sleep(2.0)
        if self._stop_event.is_set():
            self._stop_ffmpeg()
            return

        # Phase 4: finalize
        self._stop_ffmpeg()
        if self._output_path and self._output_path.exists():
            size_mb = self._output_path.stat().st_size / (1024 * 1024)
            self._set("✓ 录制完成", GREEN,
                      f"{self._output_path.name} ({size_mb:.1f} MB) 已保存到 "
                      f"{self._output_path.parent}\\")
        else:
            self._set("⚠️ 录制结束但文件未生成", ORANGE,
                      "请联系工程师并截图本窗口。")

    def _start_ffmpeg(self, out_path: Path) -> None:
        """Spawn ffmpeg with gdigrab to record the Minecraft window.

        gdigrab title="Minecraft*" filter records ONLY the MC window. If
        no exact match, falls back to full desktop capture.
        """
        # H.265 encoded MP4 at 30 fps. -draw_mouse 0 hides cursor in
        # captured frames. -framerate before -i sets the input rate.
        cmd = [
            str(_FFMPEG),
            "-f", "gdigrab",
            "-framerate", "30",
            "-draw_mouse", "0",
            "-i", "desktop",   # full-desktop capture (most robust)
            "-c:v", "libx265",
            "-preset", "ultrafast",
            "-pix_fmt", "yuv420p",
            "-y",  # overwrite if exists
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
        self._stop_event.set()
        self._stop_ffmpeg()
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
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            title="Oyster 录制器 — 启动错误",
            message=(
                "录制器启动失败。\n\n"
                "请截图整个窗口，发给工程师。\n\n"
                f"{type(exc).__name__}: {exc}\n\n"
                f"--- 详细 ---\n{traceback.format_exc()}"
            ),
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
