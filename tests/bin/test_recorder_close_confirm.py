#!/usr/bin/env python3
"""Tests for bin/recorder_close_confirm.py — G278 mid-record close confirmation.

Covers:
- Module constants: DEFAULT_TITLE / DEFAULT_MESSAGE are the verbatim spec
  Chinese strings ("正在录制中" / "正在录制中，确认丢弃？").
- confirm_close_while_recording: when tkinter is missing → returns False
  (ImportError path, errs on side of not losing data);
  when askyesno returns True/False → returns bool(result);
  when askyesno raises an exception → returns False;
  caller-supplied title/message/parent are forwarded to askyesno.
- attach_to_root: when is_armed_callable returns False → on_close_confirmed
  is invoked directly (no dialog); when armed=True and dialog returns True
  → on_close_confirmed invoked; when armed=True and dialog returns False
  → on_close_confirmed NOT invoked; protocol("WM_DELETE_WINDOW", _handler)
  is wired on the root.
"""

from __future__ import annotations

import builtins
import sys
from pathlib import Path
from unittest import mock

import pytest

# Make bin/ importable as a top-level name (mirrors sibling tests).
_BIN_DIR = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(_BIN_DIR))

import recorder_close_confirm as m  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    """Spec-verbatim Chinese strings must not drift."""

    def test_default_title_is_chinese(self) -> None:
        assert m.DEFAULT_TITLE == "正在录制中"

    def test_default_message_is_chinese(self) -> None:
        assert m.DEFAULT_MESSAGE == "正在录制中，确认丢弃？"


# ---------------------------------------------------------------------------
# confirm_close_while_recording — ImportError path
# ---------------------------------------------------------------------------


class TestConfirmCloseImportError:
    """When tkinter is unavailable, must return False (don't lose data)."""

    def test_import_error_returns_false(self) -> None:
        # The function does ``from tkinter import messagebox`` inside the
        # try block. We simulate the failure by patching builtins.__import__
        # to raise ImportError for "tkinter".
        real_import = builtins.__import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "tkinter" or name.startswith("tkinter."):
                raise ImportError("simulated headless")
            return real_import(name, globals, locals, fromlist, level)

        with mock.patch("builtins.__import__", side_effect=fake_import):
            assert m.confirm_close_while_recording() is False


# ---------------------------------------------------------------------------
# confirm_close_while_recording — happy / exception paths via tkinter patch
# ---------------------------------------------------------------------------


def _patch_tkinter_messagebox(askyesno_return=None, askyesno_side_effect=None):
    """Patch the live tkinter.messagebox module's askyesno attribute.

    Returns a context manager that:
    1. Ensures tkinter is importable (it normally is on macOS/Linux test envs).
    2. Swaps tkinter.messagebox.askyesno for a MagicMock returning/raising
       the supplied value.

    Note: We always import fresh to avoid pollution from other tests that may
    have stubbed sys.modules['tkinter.messagebox'] with a fake namespace.
    """
    fake = mock.MagicMock(name="askyesno_mock")
    if askyesno_side_effect is not None:
        fake.side_effect = askyesno_side_effect
    else:
        fake.return_value = askyesno_return

    # Always import fresh to avoid polluted sys.modules stubs from other tests.
    try:
        import tkinter.messagebox as _mb  # type: ignore[import-not-found]
    except Exception:
        # Truly headless: skip the test by returning a no-op patch.
        return None, mock.patch("sys.modules", sys.modules)

    # Verify we got a real module with askyesno (not a polluted stub).
    if not hasattr(_mb, "askyesno"):
        # Polluted stub in sys.modules — force re-import by clearing the cache.
        if "tkinter.messagebox" in sys.modules:
            del sys.modules["tkinter.messagebox"]
        try:
            import tkinter.messagebox as _mb  # type: ignore[import-not-found]
        except Exception:
            return None, mock.patch("sys.modules", sys.modules)
        if not hasattr(_mb, "askyesno"):
            # Still no askyesno — truly headless, skip.
            return None, mock.patch("sys.modules", sys.modules)

    return fake, mock.patch.object(_mb, "askyesno", fake)


class TestConfirmCloseWithTk:
    """Headless mock of tkinter.messagebox.askyesno."""

    def test_askyesno_true_returns_true(self) -> None:
        fake, patcher = _patch_tkinter_messagebox(askyesno_return=True)
        if fake is None:
            pytest.skip("tkinter.messagebox unavailable")
        with patcher:
            assert m.confirm_close_while_recording() is True
        assert fake.call_count == 1

    def test_askyesno_false_returns_false(self) -> None:
        fake, patcher = _patch_tkinter_messagebox(askyesno_return=False)
        if fake is None:
            pytest.skip("tkinter.messagebox unavailable")
        with patcher:
            assert m.confirm_close_while_recording() is False
        assert fake.call_count == 1

    def test_askyesno_exception_returns_false(self) -> None:
        fake, patcher = _patch_tkinter_messagebox(
            askyesno_side_effect=RuntimeError("display gone")
        )
        if fake is None:
            pytest.skip("tkinter.messagebox unavailable")
        with patcher:
            assert m.confirm_close_while_recording() is False

    def test_forwards_title_message_parent(self) -> None:
        fake, patcher = _patch_tkinter_messagebox(askyesno_return=True)
        if fake is None:
            pytest.skip("tkinter.messagebox unavailable")
        parent = mock.MagicMock(name="parent_root")
        with patcher:
            result = m.confirm_close_while_recording(
                parent=parent, title="t", message="m"
            )
        assert result is True
        args, kwargs = fake.call_args
        assert args[0] == "t"
        assert args[1] == "m"
        assert kwargs.get("parent") is parent

    def test_default_args_match_constants(self) -> None:
        fake, patcher = _patch_tkinter_messagebox(askyesno_return=True)
        if fake is None:
            pytest.skip("tkinter.messagebox unavailable")
        with patcher:
            m.confirm_close_while_recording()
        args, _ = fake.call_args
        assert args[0] == m.DEFAULT_TITLE
        assert args[1] == m.DEFAULT_MESSAGE

    def test_returns_exact_bool_from_askyesno(self) -> None:
        fake, patcher = _patch_tkinter_messagebox(askyesno_return=1)
        if fake is None:
            pytest.skip("tkinter.messagebox unavailable")
        with patcher:
            result = m.confirm_close_while_recording()
        assert result is True
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# attach_to_root
# ---------------------------------------------------------------------------


class TestAttachToRoot:
    """WM_DELETE_WINDOW wiring — dialog gates destruction."""

    def test_wires_protocol(self) -> None:
        """protocol("WM_DELETE_WINDOW", handler) is called on the root."""
        root = mock.MagicMock()
        is_armed = mock.MagicMock(return_value=False)
        on_close = mock.MagicMock()
        m.attach_to_root(root, is_armed, on_close)
        assert root.protocol.call_count == 1
        args, _ = root.protocol.call_args
        assert args[0] == "WM_DELETE_WINDOW"
        # The handler we passed should be the one registered.
        handler = args[1]
        assert callable(handler)

    def test_not_armed_skips_dialog_and_calls_on_close(self) -> None:
        """When is_armed is False, on_close_confirmed is invoked directly."""
        root = mock.MagicMock()
        is_armed = mock.MagicMock(return_value=False)
        on_close = mock.MagicMock()
        m.attach_to_root(root, is_armed, on_close)
        # Pull the handler back out and invoke it.
        handler = root.protocol.call_args[0][1]
        handler()
        # No dialog should have been opened.
        on_close.assert_called_once_with()

    def test_armed_confirm_true_calls_on_close(self) -> None:
        """When armed and the user confirms, on_close_confirmed is called."""
        root = mock.MagicMock()
        is_armed = mock.MagicMock(return_value=True)
        on_close = mock.MagicMock()

        with mock.patch.object(
            m, "confirm_close_while_recording", return_value=True
        ) as confirm:
            m.attach_to_root(root, is_armed, on_close)
            handler = root.protocol.call_args[0][1]
            handler()
        confirm.assert_called_once()
        # Parent kwarg should be the root we passed.
        _, kwargs = confirm.call_args
        assert kwargs.get("parent") is root
        on_close.assert_called_once_with()

    def test_armed_confirm_false_blocks_on_close(self) -> None:
        """When armed and the user cancels, on_close_confirmed is NOT called."""
        root = mock.MagicMock()
        is_armed = mock.MagicMock(return_value=True)
        on_close = mock.MagicMock()

        with mock.patch.object(
            m, "confirm_close_while_recording", return_value=False
        ):
            m.attach_to_root(root, is_armed, on_close)
            handler = root.protocol.call_args[0][1]
            handler()
        on_close.assert_not_called()
