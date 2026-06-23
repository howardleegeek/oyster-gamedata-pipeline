#!/usr/bin/env python3
"""
bin/qa_validator_gui.py — Oyster Clip Validator (v0.2.0 SIMPLIFIED)

Howard 2026-05-05: "测试人员 就是需要GUI" + "弄的是不是太复杂了"

Minimum-viable GUI for non-programmer testers. ONE button, ONE big result.
No status bar, no re-run button, no per-criterion list by default.

Workflow (single click):
  1. Tester sees a window with one big button: "选择 clip 文件"
  2. Tester clicks it, picks a clip-*.tar.gz from the file dialog
  3. Validator auto-extracts + auto-runs lint (no separate Run button)
  4. Window now shows ONLY: huge "✓ 通过 24/24" green or "✗ 失败 N/24" red
  5. A small "查看详情" link below toggles the technical breakdown
     (kept hidden by default so testers don't see jargon)

Built into a single Windows .exe by .github/workflows/build-qa-validator-exe.yml
using PyInstaller --onefile --windowed on a windows-latest runner.
Backed by bin/lint_v3_prd_grounded.py (G165 — 24-criteria PRD-grounded lint).
"""
from __future__ import annotations

import sys
import tarfile
import tempfile
import threading
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext

# When PyInstaller-frozen, bundled modules live in sys._MEIPASS. When run from
# source, the bin/ dir holds lint_v3_prd_grounded.py next to this file.
if getattr(sys, "frozen", False):
    _BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", "."))
else:
    _BUNDLE_ROOT = Path(__file__).resolve().parent

if str(_BUNDLE_ROOT) not in sys.path:
    sys.path.insert(0, str(_BUNDLE_ROOT))

import lint_v3_prd_grounded as lint_mod  # noqa: E402  (path mutation above)

GREEN = "#2e7d32"
RED = "#c62828"
ORANGE = "#ef6c00"
GRAY = "#cfd8dc"
TEXT_GRAY = "#546e7a"


class ValidatorApp(tk.Tk):
    """Single-window minimal GUI: 1 button → 1 huge result."""

    def __init__(self) -> None:
        super().__init__()
        self.title("Oyster Clip 验证器")
        self.geometry("520x420")
        self.minsize(440, 360)
        self.configure(bg="white")
        self._details_visible = False
        self._build()

    # ---- UI construction ------------------------------------------------
    def _build(self) -> None:
        # The ONE button (huge, centered)
        self._button = tk.Button(
            self,
            text="选择 clip 文件…",
            font=("Helvetica", 18, "bold"),
            bg="#1976d2",
            fg="white",
            activebackground="#1565c0",
            activeforeground="white",
            bd=0,
            padx=24,
            pady=14,
            cursor="hand2",
            command=self._pick_and_run,
        )
        self._button.pack(pady=(40, 24))

        # The ONE big result label (default: gray placeholder)
        self._verdict = tk.Label(
            self,
            text="",
            font=("Helvetica", 48, "bold"),
            bg="white",
            fg=TEXT_GRAY,
            height=2,
        )
        self._verdict.pack(fill="x", padx=20)

        # Subtitle (sub-line under verdict, eg the file name)
        self._subtitle = tk.Label(
            self,
            text="点击上面按钮，选你录的 clip 文件即可。",
            font=("Helvetica", 11),
            bg="white",
            fg=TEXT_GRAY,
            wraplength=480,
        )
        self._subtitle.pack(pady=(4, 0))

        # Spacer
        tk.Frame(self, bg="white").pack(expand=True, fill="both")

        # Tiny "查看详情" toggle at the bottom (hidden detail panel)
        self._toggle_btn = tk.Button(
            self,
            text="查看详情 ▾",
            font=("Helvetica", 10, "underline"),
            bg="white",
            fg="#1976d2",
            bd=0,
            cursor="hand2",
            command=self._toggle_details,
        )
        self._toggle_btn.pack(pady=(0, 8))

        # The detail panel itself (created but hidden)
        self._detail_frame = tk.Frame(self, bg="white")
        self._detail = scrolledtext.ScrolledText(
            self._detail_frame,
            height=10,
            font=("Courier", 9),
            wrap="word",
        )
        self._detail.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        # NOT packed onto root yet — _toggle_details handles visibility.

    # ---- actions --------------------------------------------------------
    def _pick_and_run(self) -> None:
        chosen = filedialog.askopenfilename(
            title="选择一个 clip 文件",
            filetypes=[
                ("Clip 文件 (.tar.gz)", "*.tar.gz *.tgz"),
                ("所有文件", "*.*"),
            ],
        )
        if not chosen:
            return
        path = Path(chosen)
        # Auto-run on selection — no separate Run button.
        self._set_verdict("…", TEXT_GRAY)
        self._subtitle.config(text=f"正在验证 {path.name} …")
        self._button.config(state="disabled")
        threading.Thread(target=self._run, args=(path,), daemon=True).start()

    def _run(self, clip: Path) -> None:
        try:
            with tempfile.TemporaryDirectory() as td:
                workdir = Path(td)
                if str(clip).endswith(".tar.gz") or str(clip).endswith(".tgz"):
                    with tarfile.open(clip) as tf:
                        tf.extractall(workdir)
                    inner = [p for p in workdir.iterdir() if p.is_dir()]
                    target = inner[0] if len(inner) == 1 else workdir
                else:
                    target = clip if clip.is_dir() else workdir
                report = lint_mod.run_all_checks(target)
                self._render(report, clip.name)
        except Exception as exc:  # noqa: BLE001
            self._render_error(exc, clip.name)
        finally:
            self._button.config(state="normal")

    def _render(self, report, clip_name: str) -> None:
        if report.failed_count == 0:
            self._set_verdict(
                f"✓ 通过\n{report.passed_count}/{report.total_checks}", GREEN
            )
            self._subtitle.config(
                text=f"{clip_name} 符合买方规格，可以交付。", fg=GREEN
            )
        else:
            self._set_verdict(
                f"✗ 失败\n{report.failed_count}/{report.total_checks}", RED
            )
            self._subtitle.config(
                text=(
                    f"{clip_name} 有 {report.failed_count} 项不符合规格 — "
                    f"点下面的「查看详情」截图给工程师。"
                ),
                fg=RED,
            )
        # Populate detail panel (hidden by default)
        self._detail.delete("1.0", "end")
        for r in report.results:
            mark = "✓" if r.passed else "✗"
            self._detail.insert(
                "end", f"{mark}  [{r.criterion_id:2}] {r.name}: {r.message}\n"
            )

    def _render_error(self, exc: Exception, clip_name: str) -> None:
        self._set_verdict("⚠️ 出错", ORANGE)
        self._subtitle.config(
            text=f"验证 {clip_name} 时出错。点「查看详情」截图给工程师。",
            fg=ORANGE,
        )
        self._detail.delete("1.0", "end")
        self._detail.insert("end", f"{type(exc).__name__}: {exc}\n\n")
        self._detail.insert("end", traceback.format_exc())

    def _set_verdict(self, text: str, color: str) -> None:
        self._verdict.config(text=text, fg=color)
        self.update_idletasks()

    def _toggle_details(self) -> None:
        if self._details_visible:
            self._detail_frame.pack_forget()
            self._toggle_btn.config(text="查看详情 ▾")
            self._details_visible = False
            # Shrink the window a bit when hidden
            self.geometry("520x420")
        else:
            self._detail_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
            self._toggle_btn.config(text="收起详情 ▴")
            self._details_visible = True
            # Grow to show the detail panel
            self.geometry("520x640")


def _emergency_error_box(exc: BaseException) -> None:
    """Last-resort error reporter when --windowed swallows stderr.

    Without this, a startup crash inside ValidatorApp.__init__ (Tkinter DLL
    missing, lint module import failure, etc.) produces a silent 0.5s flash
    with no log file. Show a Tk messagebox so non-programmer testers can
    screenshot the traceback for engineer triage.
    """
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            title="Oyster Clip 验证器 — 启动错误",
            message=(
                "验证器启动失败。\n\n"
                "请截图整个窗口，发给工程师排查。\n\n"
                f"{type(exc).__name__}: {exc}\n\n"
                f"--- 详细 ---\n{traceback.format_exc()}"
            ),
        )
        root.destroy()
    except Exception:
        log = Path.home() / "OysterClipValidator-error.log"
        try:
            with log.open("a", encoding="utf-8") as fh:
                fh.write(f"\n=== startup error ===\n{traceback.format_exc()}\n")
        except Exception:
            pass


def main() -> int:
    try:
        app = ValidatorApp()
        app.mainloop()
        return 0
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001
        _emergency_error_box(exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
