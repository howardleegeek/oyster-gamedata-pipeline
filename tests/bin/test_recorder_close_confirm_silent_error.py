"""
Regression tests for silent error swallows in bin/recorder_close_confirm.py.

These tests verify that a failed `messagebox.askyesno` call inside
`confirm_close_while_recording` is logged at debug level (binding the
exception) rather than silently swallowed. The function must still
return ``False`` so the recorder errs on the side of NOT losing data.
"""

import ast
import logging
import sys
from pathlib import Path

import pytest


class TestRecorderCloseConfirmSilentError:
    """Tests for silent error handling in recorder_close_confirm.py."""

    def _read_source(self) -> str:
        return (
            Path(__file__).parent.parent.parent
            / "bin"
            / "recorder_close_confirm.py"
        ).read_text()

    def test_no_bare_except_in_confirm_close(self):
        """The confirm_close_while_recording function must not have a bare
        ``except Exception:`` (no ``as`` binding) that hides the error."""
        source = self._read_source()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef)
                and node.name == "confirm_close_while_recording"
            ):
                for child in ast.walk(node):
                    if isinstance(child, ast.ExceptHandler):
                        if child.type is not None:
                            type_src = ast.unparse(child.type)
                            if "Exception" in type_src and child.name is None:
                                pytest.fail(
                                    "Found bare 'except Exception:' "
                                    "(no 'as' binding) in "
                                    "confirm_close_while_recording. "
                                    "Bind the exception and log it "
                                    "via logger.debug(...)."
                                )

    def test_logger_imported(self):
        """A module-level logger must be defined so the exception can be logged."""
        source = self._read_source()
        assert "import logging" in source, "logging import missing"
        assert "logger = logging.getLogger" in source, (
            "module-level logger definition missing"
        )

    def test_messagebox_failure_logs_at_debug(self, caplog):
        """When messagebox.askyesno raises, the exception is surfaced via
        a debug log AND the function still returns False (data-safe)."""
        # Ensure clean import
        sys.modules.pop("bin.recorder_close_confirm", None)

        # Stub tkinter.messagebox to raise a synthetic error.
        import types

        fake_tkinter = types.ModuleType("tkinter")
        fake_messagebox = types.ModuleType("tkinter.messagebox")

        def _raise(*_a, **_kw):
            raise RuntimeError("synthetic dialog failure")

        fake_messagebox.askyesno = _raise
        fake_tkinter.messagebox = fake_messagebox

        # Inject the fake tkinter modules so the import inside
        # confirm_close_while_recording succeeds.
        sys.modules["tkinter"] = fake_tkinter
        sys.modules["tkinter.messagebox"] = fake_messagebox

        try:
            from bin.recorder_close_confirm import (
                confirm_close_while_recording,
            )

            with caplog.at_level(
                logging.DEBUG, logger="bin.recorder_close_confirm"
            ):
                result = confirm_close_while_recording(
                    title="t", message="m"
                )

            # Control flow: must still return False on error (data-safe).
            assert result is False, (
                "Expected confirm_close_while_recording to return False "
                "when the dialog raises, got: %r" % (result,)
            )

            # The exception must be surfaced via a debug log.
            debug_msgs = [
                r.getMessage()
                for r in caplog.records
                if r.levelno == logging.DEBUG
            ]
            assert any(
                "messagebox.askyesno failed" in m for m in debug_msgs
            ), (
                "Expected a debug log mentioning "
                "'messagebox.askyesno failed', got: %s" % debug_msgs
            )
        finally:
            # Clean up injected modules to avoid leaking state.
            sys.modules.pop("tkinter", None)
            sys.modules.pop("tkinter.messagebox", None)
            sys.modules.pop("bin.recorder_close_confirm", None)

    def test_import_error_still_returns_false(self, caplog):
        """If tkinter is not importable at all, we must still return False
        (data-safe) — and this branch should not be regressed by the new
        debug logging path."""
        sys.modules.pop("bin.recorder_close_confirm", None)
        # Force the inner `from tkinter import messagebox` to fail.
        sys.modules["tkinter"] = None  # type: ignore[assignment]

        try:
            # Re-import the module so it picks up the broken tkinter.
            if "bin.recorder_close_confirm" in sys.modules:
                del sys.modules["bin.recorder_close_confirm"]
            from bin.recorder_close_confirm import (
                confirm_close_while_recording,
            )

            with caplog.at_level(
                logging.DEBUG, logger="bin.recorder_close_confirm"
            ):
                result = confirm_close_while_recording(
                    title="t", message="m"
                )
            assert result is False
        finally:
            sys.modules.pop("tkinter", None)
            sys.modules.pop("bin.recorder_close_confirm", None)
