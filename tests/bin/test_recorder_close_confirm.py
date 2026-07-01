#!/usr/bin/env python3
"""Tests for bin/recorder_close_confirm.py — G278 mid-record close confirmation."""

from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import MagicMock, patch


def _install_tk_stubs() -> None:
    """Install stub tkinter modules for headless testing."""
    if "tkinter" in sys.modules and getattr(sys.modules["tkinter"], "_stub", False):
        return

    tk = types.ModuleType("tkinter")
    tk._stub = True

    class _Widget:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def __getattr__(self, _name: str) -> Any:
            return lambda *a, **kw: None

    tk.Tk = type("Tk", (_Widget,), {"__init__": lambda self, *a, **kw: None})
    tk.Frame = tk.Label = tk.Button = tk.Checkbutton = _Widget
    tk.BooleanVar = type(
        "BooleanVar",
        (),
        {"__init__": lambda self, value=False: None, "get": lambda self: False},
    )
    tk.messagebox = types.SimpleNamespace(
        askyesno=lambda title, message, parent=None: True
    )

    ttk = types.ModuleType("tkinter.ttk")
    ttk.Progressbar = _Widget
    tk.ttk = ttk

    sys.modules["tkinter"] = tk
    sys.modules["tkinter.ttk"] = ttk
    sys.modules["tkinter.messagebox"] = tk.messagebox


# Install stubs BEFORE importing the module under test
_install_tk_stubs()

# Now import the module
import bin.recorder_close_confirm as rcc  # noqa: E402


class TestConfirmCloseWhileRecording:
    """Tests for confirm_close_while_recording function."""

    def test_returns_true_when_user_confirms_close(self):
        """User clicking Yes (True) should return True."""
        # Patch at the tkinter.messagebox module level (imported inside function)
        with patch("tkinter.messagebox.askyesno", return_value=True):
            result = rcc.confirm_close_while_recording()
            assert result is True

    def test_returns_false_when_user_cancels_close(self):
        """User clicking No (False) should return False."""
        with patch("tkinter.messagebox.askyesno", return_value=False):
            result = rcc.confirm_close_while_recording()
            assert result is False

    def test_returns_false_on_messagebox_exception(self):
        """Any exception from messagebox should return False (safe default)."""
        with patch(
            "tkinter.messagebox.askyesno",
            side_effect=Exception("Display error"),
        ):
            result = rcc.confirm_close_while_recording()
            assert result is False

    def test_custom_title_and_message(self):
        """Custom title and message should be passed to askyesno."""
        sys.modules["tkinter.messagebox"].askyesno = MagicMock(return_value=True)

        custom_title = "Custom Title"
        custom_message = "Custom Message"
        result = rcc.confirm_close_while_recording(
            title=custom_title, message=custom_message
        )
        sys.modules["tkinter.messagebox"].askyesno.assert_called_once_with(
            custom_title, custom_message, parent=None
        )
        assert result is True

    def test_parent_argument_passed(self):
        """Parent argument should be passed to askyesno."""
        sys.modules["tkinter.messagebox"].askyesno = MagicMock(return_value=True)

        mock_parent = MagicMock()
        result = rcc.confirm_close_while_recording(parent=mock_parent)
        sys.modules["tkinter.messagebox"].askyesno.assert_called_once()
        args, kwargs = sys.modules["tkinter.messagebox"].askyesno.call_args
        assert kwargs.get("parent") is mock_parent
        assert result is True


class TestAttachToRoot:
    """Tests for attach_to_root function."""

    def test_wires_protocol_handler(self):
        """Should register WM_DELETE_WINDOW protocol handler on root."""
        mock_root = MagicMock()
        mock_is_armed = MagicMock(return_value=False)
        mock_on_confirmed = MagicMock()

        rcc.attach_to_root(mock_root, mock_is_armed, mock_on_confirmed)
        mock_root.protocol.assert_called_once()
        call_args = mock_root.protocol.call_args
        assert call_args[0][0] == "WM_DELETE_WINDOW"

    def test_calls_on_confirmed_when_not_armed(self):
        """When not armed, should call on_close_confirmed immediately."""
        mock_root = MagicMock()
        mock_is_armed = MagicMock(return_value=False)
        mock_on_confirmed = MagicMock()

        rcc.attach_to_root(mock_root, mock_is_armed, mock_on_confirmed)

        # Get the handler and call it
        handler = mock_root.protocol.call_args[0][1]
        handler()

        mock_is_armed.assert_called_once()
        mock_on_confirmed.assert_called_once()

    def test_prompts_when_armed_and_user_confirms(self):
        """When armed and user confirms, should call on_close_confirmed."""
        mock_root = MagicMock()
        mock_is_armed = MagicMock(return_value=True)
        mock_on_confirmed = MagicMock()

        with patch.object(rcc, "confirm_close_while_recording", return_value=True):
            rcc.attach_to_root(mock_root, mock_is_armed, mock_on_confirmed)

            handler = mock_root.protocol.call_args[0][1]
            handler()

            mock_is_armed.assert_called_once()
            mock_on_confirmed.assert_called_once()

    def test_does_not_call_on_confirmed_when_armed_and_user_cancels(self):
        """When armed but user cancels, should NOT call on_close_confirmed."""
        mock_root = MagicMock()
        mock_is_armed = MagicMock(return_value=True)
        mock_on_confirmed = MagicMock()

        with patch.object(rcc, "confirm_close_while_recording", return_value=False):
            rcc.attach_to_root(mock_root, mock_is_armed, mock_on_confirmed)

            handler = mock_root.protocol.call_args[0][1]
            handler()

            mock_is_armed.assert_called_once()
            mock_on_confirmed.assert_not_called()


class TestConstants:
    """Tests for module constants."""

    def test_default_title_is_chinese(self):
        """Default title should be Chinese per spec."""
        assert rcc.DEFAULT_TITLE == "正在录制中"

    def test_default_message_is_chinese(self):
        """Default message should be Chinese per spec."""
        assert rcc.DEFAULT_MESSAGE == "正在录制中，确认丢弃？"
