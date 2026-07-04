#!/usr/bin/env python3
"""G278: Mid-record close confirmation dialog.

When the tester closes the recorder window while ``_record_armed`` is true,
the recorder calls :func:`confirm_close_while_recording` before destroying
the Tk root. The user gets a Yes/No prompt; "No" cancels the close.

Solves recorder gap E5 — accidental window close discards the in-flight
clip without warning.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Default Chinese title/body matching the spec verbatim. English fallback
# used when the spec asks for a localized variant.
DEFAULT_TITLE = "正在录制中"
DEFAULT_MESSAGE = "正在录制中，确认丢弃？"


def confirm_close_while_recording(
    parent: Optional[Any] = None,
    title: str = DEFAULT_TITLE,
    message: str = DEFAULT_MESSAGE,
) -> bool:
    """Show a Yes/No dialog asking whether to discard an in-flight recording.

    Returns ``True`` if the user confirms close (discard), ``False`` if
    they cancel. If Tk is unavailable (headless tester / Tk import error)
    we fall back to ``False`` so the recorder errs on the side of NOT
    losing data.
    """
    try:
        from tkinter import messagebox
    except ImportError:
        return False

    try:
        result = messagebox.askyesno(title, message, parent=parent)
    except Exception as e:
        # Tk dialogs can raise a variety of runtime errors when the display
        # is unavailable, the parent has been destroyed, or the toolkit is
        # half-initialised. We err on the side of NOT losing data by
        # returning False, but log the underlying error at debug level so
        # operators can diagnose the headless / uninitialised-Tk case.
        logger.debug(
            "messagebox.askyesno failed in confirm_close_while_recording: %s",
            e, exc_info=True,
        )
        return False
    return bool(result)


def attach_to_root(root: Any, is_armed_callable, on_close_confirmed) -> None:
    """Wire WM_DELETE_WINDOW so the dialog gates window destruction.

    ``is_armed_callable``  -> bool: returns True when a clip is in flight.
    ``on_close_confirmed`` -> callable: invoked when the user picks Yes
                              (typical: ``root.destroy``).
    """
    def _handler() -> None:
        if is_armed_callable():
            if not confirm_close_while_recording(parent=root):
                return
        on_close_confirmed()

    root.protocol("WM_DELETE_WINDOW", _handler)


if __name__ == "__main__":
    # Smoke test: only runs when a display is available.
    import sys
    print("decision=", confirm_close_while_recording())
    sys.exit(0)
