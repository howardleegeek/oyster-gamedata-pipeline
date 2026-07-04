#!/usr/bin/env python3
"""G276: Recorder first-run EULA / data-collection consent dialog.

On first launch the recorder shows a Tk dialog with the data-collection
terms. If the tester clicks **Accept** we persist a ``consent.json`` file
into ``%LOCALAPPDATA%/OysterRecorder/`` (or the platform equivalent)
containing a fresh ``consent_token`` (UUID4) and a UTC ``accepted_at``
timestamp.

The recorder calls :func:`has_valid_consent` before arming a clip; if no
valid consent exists, recording is gated until the dialog is shown again
and the user accepts.

Solves recorder gap G2 — vendor data collection without explicit
opt-in is a privacy hazard.

CLI usage::

    python bin/recorder_eula_first_run.py            # show dialog if needed
    python bin/recorder_eula_first_run.py --check    # exit 0 if consented
    python bin/recorder_eula_first_run.py --reset    # delete prior consent
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

CONSENT_TERMS = (
    "Oyster Recorder collects gameplay video, controller inputs, and depth "
    "estimates from this machine while the recorder is armed. This data is "
    "uploaded to Oyster Labs and used to train AI world models.\n\n"
    "We do NOT capture: passwords, browser tabs, microphone audio, webcam, "
    "or any window outside the active game capture region.\n\n"
    "By clicking Accept you confirm you are 18+, you own the captured "
    "content, and you grant Oyster Labs a license to use it for model "
    "training. You can revoke consent at any time by deleting "
    "consent.json or contacting privacy@oysterlabs.ai."
)

CONSENT_FILENAME = "consent.json"
APP_DIR_NAME = "OysterRecorder"


def _consent_dir() -> Path:
    """Return the per-user app data directory for the recorder."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / APP_DIR_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    # Linux / fallback
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / APP_DIR_NAME
    return Path.home() / ".local" / "share" / APP_DIR_NAME


def consent_path() -> Path:
    """Absolute path to the recorder's consent.json."""
    return _consent_dir() / CONSENT_FILENAME


def has_valid_consent(path: Optional[Path] = None) -> bool:
    """Return True iff a well-formed consent.json with a token exists."""
    target = path or consent_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return False
    token = data.get("consent_token")
    accepted = data.get("accepted_at")
    return bool(token) and bool(accepted)


def write_consent(path: Optional[Path] = None) -> dict:
    """Write a fresh consent record and return it as a dict."""
    target = path or consent_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "consent_token": str(uuid.uuid4()),
        "accepted_at": datetime.now(timezone.utc).isoformat(),
        "version": 1,
    }
    target.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def reset_consent(path: Optional[Path] = None) -> bool:
    """Delete an existing consent file. Returns True if a file was removed."""
    target = path or consent_path()
    try:
        target.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def show_dialog(parent: Optional[Any] = None,
                terms: str = CONSENT_TERMS) -> bool:
    """Render the Tk consent dialog. Returns True iff user accepted."""
    try:
        import tkinter as tk
        from tkinter import scrolledtext
    except ImportError:
        return False

    owner = parent
    root: Optional[tk.Tk] = None
    if owner is None:
        root = tk.Tk()
        owner = root
        root.title("Oyster Recorder - Data Collection Consent")
        root.geometry("560x420")

    decision = {"accepted": False}

    frame = tk.Frame(owner, padx=16, pady=12)
    frame.pack(fill="both", expand=True)

    tk.Label(
        frame,
        text="Oyster Recorder Data Collection",
        font=("Segoe UI", 13, "bold"),
    ).pack(anchor="w")

    body = scrolledtext.ScrolledText(frame, wrap="word", height=14)
    body.insert("1.0", terms)
    body.configure(state="disabled")
    body.pack(fill="both", expand=True, pady=(8, 12))

    btns = tk.Frame(frame)
    btns.pack(fill="x")

    def _accept() -> None:
        decision["accepted"] = True
        if root is not None:
            root.destroy()

    def _decline() -> None:
        decision["accepted"] = False
        if root is not None:
            root.destroy()

    tk.Button(btns, text="Decline", width=12, command=_decline).pack(side="right")
    tk.Button(btns, text="Accept",  width=12, command=_accept).pack(side="right", padx=(0, 8))

    if root is not None:
        try:
            root.mainloop()
        except Exception as e:  # noqa: BLE001
            logger.debug(
                "EULA dialog mainloop crashed; treating as declined: %s",
                e,
                exc_info=True,
            )
            return False

    return bool(decision["accepted"])


def ensure_consent(path: Optional[Path] = None) -> bool:
    """Show dialog if no valid consent yet. Returns True iff consented."""
    if has_valid_consent(path):
        return True
    if not show_dialog():
        return False
    write_consent(path)
    return True


def main(argv: Optional[list] = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Recorder first-run EULA")
    parser.add_argument("--check", action="store_true",
                        help="Exit 0 iff valid consent exists; do not show UI")
    parser.add_argument("--reset", action="store_true",
                        help="Delete existing consent.json and exit")
    parser.add_argument("--path", type=Path, default=None,
                        help="Override consent.json path (testing only)")
    args = parser.parse_args(argv)

    if args.reset:
        removed = reset_consent(args.path)
        print(f"reset removed={removed} path={args.path or consent_path()}")
        return 0
    if args.check:
        ok = has_valid_consent(args.path)
        print(f"consent_valid={ok}")
        return 0 if ok else 1

    accepted = ensure_consent(args.path)
    print(f"accepted={accepted} path={args.path or consent_path()}")
    return 0 if accepted else 2


if __name__ == "__main__":
    sys.exit(main())
