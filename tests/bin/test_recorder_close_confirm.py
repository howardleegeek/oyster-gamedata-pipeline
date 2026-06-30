#!/usr/bin/env python3
"""
Tests for bin/recorder_close_confirm.py — Mid-record close confirmation dialog (G278, E5).

Purpose:
When the tester closes the recorder window while ``_record_armed`` is true,
the recorder calls :func:`confirm_close_while_recording` before destroying
the Tk root. The user gets a Yes/No prompt; "No" cancels the close. This
guards against accidental window close discarding the in-flight clip
without warning.

Test coverage:
- DEFAULT_TITLE / DEFAULT_MESSAGE constants
- confirm_close_while_recording (Yes -> True, No -> False, ImportError -> False,
  messagebox.askyesno exception -> False, custom title/message propagation,
  parent argument forwarding)
- attach_to_root (gates destruction when armed, passes through when unarmed,
  registers WM_DELETE_WINDOW handler, only invokes on_close_confirmed after
  user confirmation, no dialog when unarmed, calls confirm with root as parent)
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

# Import the module under test
sys.path.insert(0, str(Path(__file__).parent.parent))
from bin.recorder_close_confirm import (
    DEFAULT_MESSAGE,
    DEFAULT_TITLE,
    attach_to_root,
    confirm_close_while_recording,
)


def _install_messagebox_stub(askyesno: Any) -> None:
    """Install a stub tkinter.messagebox into sys.modules so the lazy import
    inside confirm_close_while_recording resolves to a controllable object.

    ``askyesno`` is the function object the dialog will call.
    """
    tk = types.ModuleType("tkinter")
    messagebox = types.ModuleType("tkinter.messagebox")
    messagebox.askyesno = askyesno  # type: ignore[attr-defined]
    sys.modules["tkinter"] = tk
    sys.modules["tkinter.messagebox"] = messagebox


def _remove_messagebox_stub() -> None:
    """Remove the stub so subsequent tests see the real (or absent) tkinter."""
    sys.modules.pop("tkinter", None)
    sys.modules.pop("tkinter.messagebox", None)


class TestConstants:
    """Tests for module-level default strings (Chinese copy is locked)."""

    def test_default_title_is_locked_chinese(self):
        assert DEFAULT_TITLE == "正在录制中"

    def test_default_message_is_locked_chinese(self):
        assert DEFAULT_MESSAGE == "正在录制中，确认丢弃？"


class TestConfirmCloseWhileRecording:
    """Tests for confirm_close_while_recording (the dialog logic)."""

    def test_yes_returns_true(self):
        """User clicks Yes -> True (discard clip, allow close)."""
        sentinel = MagicMock(return_value=True)
        _install_messagebox_stub(sentinel)
        try:
            assert confirm_close_while_recording() is True
        finally:
            _remove_messagebox_stub()

    def test_no_returns_false(self):
        """User clicks No -> False (cancel close, keep clip)."""
        sentinel = MagicMock(return_value=False)
        _install_messagebox_stub(sentinel)
        try:
            assert confirm_close_while_recording() is False
        finally:
            _remove_messagebox_stub()

    def test_returns_false_when_tkinter_unavailable(self):
        """If tkinter cannot be imported, fall back to False (don't lose data)."""
        # Force the import to fail by hiding tkinter behind None
        saved_tk = sys.modules.pop("tkinter", None)
        saved_mb = sys.modules.pop("tkinter.messagebox", None)
        sys.modules["tkinter"] = None  # type: ignore[assignment]
        sys.modules["tkinter.messagebox"] = None  # type: ignore[assignment]
        try:
            result = confirm_close_while_recording()
        finally:
            # restore (or remove the None sentinels)
            sys.modules.pop("tkinter", None)
            sys.modules.pop("tkinter.messagebox", None)
            if saved_tk is not None:
                sys.modules["tkinter"] = saved_tk
            if saved_mb is not None:
                sys.modules["tkinter.messagebox"] = saved_mb
        assert result is False

    def test_exception_during_askyesno_returns_false(self):
        """If askyesno raises (no display, X server gone), fail safe -> False."""
        def boom(*_a, **_kw):
            raise RuntimeError("no display")
        _install_messagebox_stub(boom)
        try:
            assert confirm_close_while_recording() is False
        finally:
            _remove_messagebox_stub()

    def test_custom_title_and_message_forwarded(self):
        """Custom title/message override the defaults and reach messagebox.askyesno."""
        sentinel = MagicMock(return_value=True)
        _install_messagebox_stub(sentinel)
        try:
            confirm_close_while_recording(title="Recording in progress", message="Discard?")
        finally:
            _remove_messagebox_stub()
        sentinel.assert_called_once_with(
            "Recording in progress", "Discard?", parent=None
        )

    def test_parent_argument_forwarded(self):
        """If a parent widget is supplied, it's passed through to messagebox."""
        sentinel = MagicMock(return_value=True)
        _install_messagebox_stub(sentinel)
        parent = MagicMock(name="root_window")
        try:
            confirm_close_while_recording(parent=parent)
        finally:
            _remove_messagebox_stub()
        _, kwargs = sentinel.call_args
        assert kwargs.get("parent") is parent

    def test_result_coerced_to_bool(self):
        """Non-bool truthy return from askyesno is coerced to bool (defensive)."""
        # truthy int, not bool
        sentinel = MagicMock(return_value=1)
        _install_messagebox_stub(sentinel)
        try:
            result = confirm_close_while_recording()
        finally:
            _remove_messagebox_stub()
        assert result is True
        assert isinstance(result, bool)


class TestAttachToRoot:
    """Tests for attach_to_root (Wires WM_DELETE_WINDOW handler)."""

    def test_registers_wm_delete_window_protocol(self):
        """The handler must be registered on the root via protocol()."""
        root = MagicMock(name="root")
        attach_to_root(root, is_armed_callable=lambda: False, on_close_confirmed=MagicMock())
        root.protocol.assert_called_once()
        args, _ = root.protocol.call_args
        assert args[0] == "WM_DELETE_WINDOW"
        # second arg must be a callable
        assert callable(args[1])

    def test_unarmed_calls_on_close_confirmed_directly(self):
        """When not armed, the dialog is skipped and close proceeds."""
        root = MagicMock(name="root")
        on_close = MagicMock(name="on_close_confirmed")
        confirm_mock = MagicMock(return_value=True)
        _install_messagebox_stub(confirm_mock)
        try:
            attach_to_root(root, is_armed_callable=lambda: False, on_close_confirmed=on_close)
            # Pull the registered handler and invoke it (simulates window close)
            handler = root.protocol.call_args[0][1]
            handler()
        finally:
            _remove_messagebox_stub()
        # confirm must NOT have been called when unarmed
        confirm_mock.assert_not_called()
        on_close.assert_called_once_with()

    def test_armed_and_user_says_yes_proceeds_with_close(self):
        """When armed and user confirms Yes, on_close_confirmed is invoked."""
        root = MagicMock(name="root")
        on_close = MagicMock(name="on_close_confirmed")
        confirm_mock = MagicMock(return_value=True)
        _install_messagebox_stub(confirm_mock)
        try:
            attach_to_root(root, is_armed_callable=lambda: True, on_close_confirmed=on_close)
            handler = root.protocol.call_args[0][1]
            handler()
        finally:
            _remove_messagebox_stub()
        on_close.assert_called_once_with()

    def test_armed_and_user_says_no_blocks_close(self):
        """When armed and user picks No, on_close_confirmed is NOT called."""
        root = MagicMock(name="root")
        on_close = MagicMock(name="on_close_confirmed")
        confirm_mock = MagicMock(return_value=False)
        _install_messagebox_stub(confirm_mock)
        try:
            attach_to_root(root, is_armed_callable=lambda: True, on_close_confirmed=on_close)
            handler = root.protocol.call_args[0][1]
            handler()
        finally:
            _remove_messagebox_stub()
        on_close.assert_not_called()

    def test_armed_passes_root_as_parent_to_confirm(self):
        """When armed, root is passed as the parent to the confirm dialog."""
        root = MagicMock(name="root")
        on_close = MagicMock(name="on_close_confirmed")
        confirm_mock = MagicMock(return_value=True)
        _install_messagebox_stub(confirm_mock)
        try:
            attach_to_root(root, is_armed_callable=lambda: True, on_close_confirmed=on_close)
            handler = root.protocol.call_args[0][1]
            handler()
        finally:
            _remove_messagebox_stub()
        confirm_mock.assert_called_once()
        _, kwargs = confirm_mock.call_args
        assert kwargs.get("parent") is root

    def test_is_armed_callable_invoked_each_close(self):
        """The is_armed_callable is consulted on every close attempt (state may change)."""
        root = MagicMock(name="root")
        on_close = MagicMock(name="on_close_confirmed")
        is_armed = MagicMock(name="is_armed_callable", return_value=False)
        confirm_mock = MagicMock(return_value=True)
        _install_messagebox_stub(confirm_mock)
        try:
            attach_to_root(root, is_armed_callable=is_armed, on_close_confirmed=on_close)
            handler = root.protocol.call_args[0][1]
            handler()
            handler()
            handler()
        finally:
            _remove_messagebox_stub()
        assert is_armed.call_count == 3
        assert on_close.call_count == 3

    def test_armed_but_tkinter_unavailable_blocks_close(self):
        """If confirm falls back to False (no Tk), on_close_confirmed is NOT called.

        This protects headless testers: with no display, the dialog can't render,
        so the safe-fallback (False) prevents data loss.
        """
        root = MagicMock(name="root")
        on_close = MagicMock(name="on_close_confirmed")
        saved_tk = sys.modules.pop("tkinter", None)
        saved_mb = sys.modules.pop("tkinter.messagebox", None)
        sys.modules["tkinter"] = None  # type: ignore[assignment]
        sys.modules["tkinter.messagebox"] = None  # type: ignore[assignment]
        try:
            attach_to_root(root, is_armed_callable=lambda: True, on_close_confirmed=on_close)
            handler = root.protocol.call_args[0][1]
            handler()
        finally:
            sys.modules.pop("tkinter", None)
            sys.modules.pop("tkinter.messagebox", None)
            if saved_tk is not None:
                sys.modules["tkinter"] = saved_tk
            if saved_mb is not None:
                sys.modules["tkinter.messagebox"] = saved_mb
        on_close.assert_not_called()
