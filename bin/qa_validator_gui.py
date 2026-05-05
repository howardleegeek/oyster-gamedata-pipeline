#!/usr/bin/env python3
"""
bin/qa_validator_gui.py — Oyster Clip Validator (non-programmer-friendly GUI)

For testers who CANNOT use a terminal. Workflow:
  1. Double-click OysterClipValidator.exe
  2. Click "Choose Clip..." → pick a clip-YYYYMMDD-HHMMSS.tar.gz
  3. Watch the big banner: green "✓ 24/24 PASSED" or red "✗ N/24 FAILED"
  4. Detailed per-criterion list below for engineers to triage

Built into a single Windows .exe by .github/workflows/build-qa-validator-exe.yml
using PyInstaller --onefile --windowed on a windows-latest runner. Output is
attached to a GitHub Release so testers can download with one click. No Python,
no pip, no terminal required.

Backed by bin/lint_v3_prd_grounded.py (G165 — 24-criteria PRD-grounded lint).
This is purely a UX wrapper; it does not change lint logic.
"""
from __future__ import annotations

import sys
import tarfile
import tempfile
import threading
import traceback
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext

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
GRAY = "#9e9e9e"


class ValidatorApp(tk.Tk):
    """Single-window Tk app. Pick a clip → extract → lint → render verdict."""

    def __init__(self) -> None:
        super().__init__()
        self.title("Oyster Clip Validator — Buyer-Spec PRD")
        self.geometry("780x600")
        self.minsize(640, 480)
        self._build()
        self._selected: Path | None = None

    # ---- UI construction ------------------------------------------------
    def _build(self) -> None:
        # Top bar: instruction + Choose button
        top = ttk.Frame(self, padding=(14, 12))
        top.pack(fill="x")
        ttk.Label(
            top,
            text="Drop a clip .tar.gz, or click →",
            font=("Helvetica", 13),
        ).pack(side="left")
        ttk.Button(top, text="Choose Clip…", command=self._pick).pack(side="right")

        # Selected-file label
        self._path_var = tk.StringVar(value="(no clip selected)")
        ttk.Label(
            self,
            textvariable=self._path_var,
            foreground=GRAY,
            wraplength=720,
            justify="left",
        ).pack(padx=14, anchor="w")

        # Big verdict banner
        self._verdict_lbl = tk.Label(
            self,
            text="—",
            font=("Helvetica", 36, "bold"),
            bg=GRAY,
            fg="white",
            height=2,
        )
        self._verdict_lbl.pack(fill="x", padx=14, pady=12)

        # Per-criterion detail
        self._detail = scrolledtext.ScrolledText(
            self,
            height=18,
            font=("Courier", 10),
            wrap="word",
        )
        self._detail.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        self._detail.insert(
            "end",
            "Pick a clip to begin. The validator will:\n"
            "  1. Extract the .tar.gz to a temporary folder\n"
            "  2. Run all 24 PRD acceptance-criteria checks\n"
            "  3. Show a green / red banner above and per-check details here\n",
        )

        # Bottom bar: status + Run
        bot = ttk.Frame(self, padding=(14, 8))
        bot.pack(fill="x")
        self._status_var = tk.StringVar(value="Ready.")
        ttk.Label(bot, textvariable=self._status_var).pack(side="left")
        ttk.Button(bot, text="Re-run", command=self._run).pack(side="right")

    # ---- actions --------------------------------------------------------
    def _pick(self) -> None:
        chosen = filedialog.askopenfilename(
            title="Choose a clip tarball",
            filetypes=[
                ("Clip tarball", "*.tar.gz *.tgz"),
                ("Any file", "*.*"),
            ],
        )
        if not chosen:
            return
        self._selected = Path(chosen)
        self._path_var.set(str(self._selected))
        self._run()

    def _run(self) -> None:
        if self._selected is None or not self._selected.exists():
            self._set_verdict("CHOOSE A FILE FIRST", ORANGE)
            return
        threading.Thread(target=self._lint_in_bg, daemon=True).start()

    def _lint_in_bg(self) -> None:
        clip = self._selected
        assert clip is not None
        self._set_status("Extracting clip…")
        self._set_verdict("…", GRAY)
        self._detail.delete("1.0", "end")
        try:
            with tempfile.TemporaryDirectory() as td:
                workdir = Path(td)
                if clip.suffix in (".gz", ".tgz") or "".join(clip.suffixes).endswith(
                    ".tar.gz"
                ):
                    with tarfile.open(clip) as tf:
                        tf.extractall(workdir)
                    # Many tarballs unpack into a single top-level dir; descend if so.
                    inner = [p for p in workdir.iterdir() if p.is_dir()]
                    target = inner[0] if len(inner) == 1 else workdir
                else:
                    target = clip if clip.is_dir() else workdir
                self._set_status("Running 24 PRD checks…")
                report = lint_mod.run_all_checks(target)
                self._render_report(report)
        except Exception:  # noqa: BLE001 — surface to GUI, not stderr
            self._set_verdict("ERROR — see details", ORANGE)
            self._detail.insert("end", traceback.format_exc())
            self._set_status("Failed.")

    def _render_report(self, report: "lint_mod.LintReport") -> None:
        passed = report.passed_count
        total = report.total_checks
        if report.failed_count == 0:
            self._set_verdict(f"✓  {passed}/{total} PASSED", GREEN)
            self._set_status("Buyer-spec compliant.")
        else:
            self._set_verdict(
                f"✗  {report.failed_count}/{total} FAILED", RED
            )
            self._set_status(
                f"{report.failed_count} criterion(s) failed — see details."
            )
        self._detail.delete("1.0", "end")
        for r in report.results:
            mark = "✓" if r.passed else "✗"
            self._detail.insert(
                "end", f"{mark}  [{r.criterion_id:2}] {r.name}: {r.message}\n"
            )

    def _set_verdict(self, text: str, color: str) -> None:
        self._verdict_lbl.config(text=text, bg=color)
        self.update_idletasks()

    def _set_status(self, text: str) -> None:
        self._status_var.set(text)
        self.update_idletasks()


def _emergency_error_box(exc: BaseException) -> None:
    """Last-resort error reporter when --windowed swallows stderr.

    Without this, a startup crash inside ValidatorApp.__init__ (Tkinter DLL
    missing, lint module import failure, etc.) produces a silent 0.5s flash
    with no log file. Show a Tk messagebox so non-programmer testers can
    screenshot the traceback for engineer triage.
    """
    try:
        from tkinter import messagebox
        import tkinter as _tk

        # A dialog needs a root; create + immediately withdraw so no main window.
        root = _tk.Tk()
        root.withdraw()
        messagebox.showerror(
            title="Oyster Clip Validator — Startup Error",
            message=(
                "The validator failed to start.\n\n"
                "Please screenshot this entire dialog and send it to the\n"
                "engineering team for triage.\n\n"
                f"{type(exc).__name__}: {exc}\n\n"
                f"--- Traceback ---\n{traceback.format_exc()}"
            ),
        )
        root.destroy()
    except Exception:  # noqa: BLE001 — even messagebox can fail; best-effort
        # Fallback to a writable log file in the user's home directory so
        # engineering can still recover the error after the fact.
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
    except BaseException as exc:  # noqa: BLE001 — top-level catch-all
        _emergency_error_box(exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
