#!/usr/bin/env python3
"""rc17.4-form — Tkinter launcher form for operator metadata.

Provides two entry points:

1. ``show_first_launch_form()`` — one-time setup dialog shown when
   ``operator_config.json`` does not yet exist. Collects:
   - Operator ID (text)
   - Character Name (text)
   - Character Class (dropdown: survival / spectator / creative)
   - Default Route Type (dropdown: 1=normal / 2=special / 3=loop)
   - Notes (text, optional)

   Saves to ``%LOCALAPPDATA%\\GameData Recorder\\operator_config.json``
   (Windows) or ``~/.config/oyster/operator_config.json`` (macOS/Linux).

2. ``prompt_route_type(last_used: str)`` — tiny per-session popup asking
   "This session's route_type?" with default = last used. Returns the
   selected route type string ("1", "2", or "3").

Both functions are safe to call on systems without Tkinter — they return
``None`` and log a warning. They are also safe under PyInstaller
``--noconsole`` (``pythonw.exe``) because they create their own root
window and call ``mainloop()``.

The config path is computed by ``_config_path()`` so callers can override
it for testing via the ``OYSTER_CONFIG_PATH`` env var.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("oyster_launcher_form")

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

CHARACTER_CLASSES = ["survival", "spectator", "creative"]
ROUTE_TYPES = {
    "1": "normal",
    "2": "special",
    "3": "loop",
}
ROUTE_TYPE_LABELS = [
    "1 = normal",
    "2 = special",
    "3 = loop",
]
CONFIG_FILENAME = "operator_config.json"

# --------------------------------------------------------------------------
# Config path helpers
# --------------------------------------------------------------------------


def _config_path() -> Path:
    """Return the path to operator_config.json.

    Override with ``OYSTER_CONFIG_PATH`` env var (useful for testing).
    """
    override = os.environ.get("OYSTER_CONFIG_PATH", "").strip()
    if override:
        return Path(override)

    if os.name == "nt":
        localappdata = os.environ.get("LOCALAPPDATA", "")
        if localappdata:
            return Path(localappdata) / "GameData Recorder" / CONFIG_FILENAME
        # Fallback for weird Windows setups
        return Path.home() / "AppData" / "Local" / "GameData Recorder" / CONFIG_FILENAME

    # macOS / Linux
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "oyster" / CONFIG_FILENAME


def config_exists() -> bool:
    """Return True if operator_config.json already exists."""
    return _config_path().is_file()


def load_config() -> Optional[Dict[str, Any]]:
    """Load and return the operator config dict, or None on failure."""
    p = _config_path()
    if not p.is_file():
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load operator config %s: %s", p, e)
        return None


def save_config(data: Dict[str, Any]) -> bool:
    """Persist operator config to disk. Returns True on success."""
    p = _config_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info("Saved operator config to %s", p)
        return True
    except OSError as e:
        logger.error("Failed to save operator config %s: %s", p, e)
        return False


# --------------------------------------------------------------------------
# Env-var helpers
# --------------------------------------------------------------------------

# Mapping from config keys to env-var names
_ENV_MAP = {
    "operator_id": "OYSTER_OPERATOR_ID",
    "character_name": "OYSTER_CHARACTER_NAME",
    "character_class": "OYSTER_CHARACTER_CLASS",
    "route_type": "OYSTER_ROUTE_TYPE",
    "scene_name": "OYSTER_SCENE_NAME",
    "notes": "OYSTER_NOTES",
}


def apply_config_to_env(config: Dict[str, Any]) -> None:
    """Set OYSTER_* env vars from a config dict. Skips missing/empty keys."""
    for key, env_name in _ENV_MAP.items():
        value = config.get(key, "")
        if value:
            os.environ[env_name] = str(value)
            logger.debug("Set %s=%s", env_name, value)


# --------------------------------------------------------------------------
# Tkinter form — first launch
# --------------------------------------------------------------------------


def show_first_launch_form() -> Optional[Dict[str, Any]]:
    """Show the one-time setup form. Returns config dict or None."""
    # Skip if CI / automation
    if os.environ.get("OYSTER_SKIP_FORM", "").strip().lower() in {
        "1", "true", "yes", "on",
    }:
        logger.info("OYSTER_SKIP_FORM=1 — skipping first-launch form")
        return None

    try:
        import tkinter as tk  # noqa: PLC0415
        from tkinter import ttk  # noqa: PLC0415
    except ImportError:
        logger.warning(
            "Tkinter not available — cannot show first-launch form. "
            "Set OYSTER_* env vars manually."
        )
        return None

    result: Dict[str, Any] = {}
    root = tk.Tk()
    root.title("Oyster Recorder — Operator Setup")
    root.resizable(False, False)
    # Center on screen
    root.update_idletasks()
    w = 380
    h = 310
    x = (root.winfo_screenwidth() - w) // 2
    y = (root.winfo_screenheight() - h) // 2
    root.geometry(f"{w}x{h}+{x}+{y}")

    # Prevent accidental close without saving
    cancelled = [False]

    def on_cancel():
        cancelled[0] = True
        root.destroy()

    def on_submit():
        op_id = entry_op_id.get().strip()
        char_name = entry_char_name.get().strip()
        char_class = combo_class.get()
        route_idx = combo_route.current()
        route_key = str(route_idx + 1)  # 0→1, 1→2, 2→3
        notes = text_notes.get("1.0", "end-1c").strip()

        if not op_id:
            lbl_status.config(text="Operator ID is required.", fg="red")
            return
        if not char_name:
            lbl_status.config(text="Character Name is required.", fg="red")
            return

        result["operator_id"] = op_id
        result["character_name"] = char_name
        result["character_class"] = char_class
        result["route_type"] = route_key
        result["notes"] = notes
        root.destroy()

    # --- Build form ---
    pad = {"padx": 10, "pady": 4}
    label_w = 14

    tk.Label(root, text="Operator Setup (first launch)", font=("TkDefaultFont", 11, "bold")).pack(
        pady=(12, 4)
    )

    frame = tk.Frame(root)
    frame.pack(fill="x", padx=16)

    # Operator ID
    tk.Label(frame, text="Operator ID:", width=label_w, anchor="e").grid(
        row=0, column=0, sticky="e", **pad
    )
    entry_op_id = tk.Entry(frame, width=28)
    entry_op_id.grid(row=0, column=1, sticky="w", **pad)
    entry_op_id.focus_set()

    # Character Name
    tk.Label(frame, text="Character Name:", width=label_w, anchor="e").grid(
        row=1, column=0, sticky="e", **pad
    )
    entry_char_name = tk.Entry(frame, width=28)
    entry_char_name.grid(row=1, column=1, sticky="w", **pad)

    # Character Class
    tk.Label(frame, text="Character Class:", width=label_w, anchor="e").grid(
        row=2, column=0, sticky="e", **pad
    )
    combo_class = ttk.Combobox(frame, values=CHARACTER_CLASSES, width=25, state="readonly")
    combo_class.current(0)
    combo_class.grid(row=2, column=1, sticky="w", **pad)

    # Route Type
    tk.Label(frame, text="Default Route:", width=label_w, anchor="e").grid(
        row=3, column=0, sticky="e", **pad
    )
    combo_route = ttk.Combobox(frame, values=ROUTE_TYPE_LABELS, width=25, state="readonly")
    combo_route.current(0)
    combo_route.grid(row=3, column=1, sticky="w", **pad)

    # Notes
    tk.Label(frame, text="Notes (opt):", width=label_w, anchor="e").grid(
        row=4, column=0, sticky="ne", **pad
    )
    text_notes = tk.Text(frame, width=28, height=3)
    text_notes.grid(row=4, column=1, sticky="w", **pad)

    # Status label
    lbl_status = tk.Label(root, text="", fg="red")
    lbl_status.pack(pady=(4, 0))

    # Buttons
    btn_frame = tk.Frame(root)
    btn_frame.pack(pady=(8, 12))
    tk.Button(btn_frame, text="Save & Continue", command=on_submit, width=16).pack(
        side="left", padx=8
    )
    tk.Button(btn_frame, text="Cancel", command=on_cancel, width=12).pack(
        side="left", padx=8
    )

    # Bind Enter key to submit
    root.bind("<Return>", lambda _e: on_submit())

    root.mainloop()

    if cancelled[0] or not result:
        logger.info("First-launch form cancelled by user")
        return None

    return result


# --------------------------------------------------------------------------
# Tkinter form — per-session route type prompt
# --------------------------------------------------------------------------


def prompt_route_type(last_used: str = "1") -> Optional[str]:
    """Show a tiny popup asking for this session's route_type.

    Args:
        last_used: default route type ("1", "2", or "3").

    Returns:
        Selected route type string, or None if cancelled / Tkinter unavailable.
    """
    if os.environ.get("OYSTER_SKIP_FORM", "").strip().lower() in {
        "1", "true", "yes", "on",
    }:
        return last_used

    try:
        import tkinter as tk  # noqa: PLC0415
        from tkinter import ttk  # noqa: PLC0415
    except ImportError:
        logger.warning(
            "Tkinter not available — cannot prompt route_type. "
            "Using last_used=%s", last_used
        )
        return last_used

    # Clamp default
    if last_used not in ROUTE_TYPES:
        last_used = "1"

    result: list[str] = []
    root = tk.Tk()
    root.title("Oyster — Route Type")
    root.resizable(False, False)
    w = 320
    h = 130
    root.update_idletasks()
    x = (root.winfo_screenwidth() - w) // 2
    y = (root.winfo_screenheight() - h) // 2
    root.geometry(f"{w}x{h}+{x}+{y}")
    # Keep on top
    root.attributes("-topmost", True)

    cancelled = [False]

    def on_cancel():
        cancelled[0] = True
        root.destroy()

    def on_submit():
        idx = combo.current()
        result.append(str(idx + 1))
        root.destroy()

    tk.Label(root, text="This session's route_type?", font=("TkDefaultFont", 10)).pack(
        pady=(10, 4)
    )

    default_idx = int(last_used) - 1
    combo = ttk.Combobox(
        root, values=ROUTE_TYPE_LABELS, width=28, state="readonly"
    )
    combo.current(default_idx)
    combo.pack(pady=4)

    btn_frame = tk.Frame(root)
    btn_frame.pack(pady=(8, 10))
    tk.Button(btn_frame, text="OK", command=on_submit, width=12).pack(
        side="left", padx=8
    )
    tk.Button(btn_frame, text="Cancel", command=on_cancel, width=12).pack(
        side="left", padx=8
    )

    root.bind("<Return>", lambda _e: on_submit())
    root.bind("<Escape>", lambda _e: on_cancel())

    root.mainloop()

    if cancelled[0] or not result:
        logger.info("Route-type prompt cancelled — using last_used=%s", last_used)
        return last_used

    return result[0]
