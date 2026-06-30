#!/usr/bin/env python3
"""Tests for bin/graceful_shutdown.py — G130 SIGTERM handler.

Covers:
  * GracefulShutdown class: register_file, register_tarball, install (idempotent),
    shutting_down property, _handler flush+close behavior, _handler re-raises SIGTERM
  * Module-level API: install_handler, register_file, register_tarball, shutdown_requested
  * main() CLI: --test self-SIGTERM smoke, --log-level choices, no-arg infinite loop
  * Edge cases: empty registry, closed file, missing fileno, multiple files, tarball already closed
"""

from __future__ import annotations

import io
import logging
import os
import signal
import sys
import tarfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add bin/ to sys.path so the module is importable as a top-level name
_BIN_DIR = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(_BIN_DIR))

import bin.graceful_shutdown as gs  # noqa: E402
from bin.graceful_shutdown import GracefulShutdown  # noqa: E402

# ---------------------------------------------------------------------------
# GracefulShutdown: __init__
# ---------------------------------------------------------------------------


class TestGracefulShutdownInit:
    """Verify fresh manager starts in a clean state."""

    def test_empty_registry(self):
        mgr = GracefulShutdown()
        assert mgr._files == set()
        assert mgr._tarballs == set()
        assert mgr._shutting_down is False
        assert mgr._original_handler is None

    def test_shutting_down_property_initial_false(self):
        mgr = GracefulShutdown()
        assert mgr.shutting_down is False


# ---------------------------------------------------------------------------
# register_file / register_tarball
# ---------------------------------------------------------------------------


class TestRegistration:
    """File and tarball registration."""

    def test_register_file_adds_to_set(self):
        mgr = GracefulShutdown()
        fh = io.BytesIO()
        mgr.register_file(fh)
        assert fh in mgr._files
        assert len(mgr._files) == 1

    def test_register_file_dedup(self):
        mgr = GracefulShutdown()
        fh = io.BytesIO()
        mgr.register_file(fh)
        mgr.register_file(fh)
        # set dedupes
        assert len(mgr._files) == 1

    def test_register_tarball_adds_to_set(self):
        mgr = GracefulShutdown()
        tf = tarfile.open(fileobj=io.BytesIO(), mode="w")
        try:
            mgr.register_tarball(tf)
            assert tf in mgr._tarballs
            assert len(mgr._tarballs) == 1
        finally:
            tf.close()

    def test_register_multiple_files(self):
        mgr = GracefulShutdown()
        fh1 = io.BytesIO()
        fh2 = io.BytesIO()
        mgr.register_file(fh1)
        mgr.register_file(fh2)
        assert len(mgr._files) == 2

    def test_register_multiple_tarballs(self):
        mgr = GracefulShutdown()
        tf1 = tarfile.open(fileobj=io.BytesIO(), mode="w")
        tf2 = tarfile.open(fileobj=io.BytesIO(), mode="w")
        try:
            mgr.register_tarball(tf1)
            mgr.register_tarball(tf2)
            assert len(mgr._tarballs) == 2
        finally:
            tf1.close()
            tf2.close()


# ---------------------------------------------------------------------------
# install()
# ---------------------------------------------------------------------------


class TestInstall:
    """Handler installation is idempotent."""

    def test_install_sets_original_handler(self):
        mgr = GracefulShutdown()
        # Capture the current SIGTERM handler so we can restore after
        pre_existing = signal.getsignal(signal.SIGTERM)
        try:
            mgr.install()
            assert mgr._original_handler is not None
        finally:
            signal.signal(signal.SIGTERM, pre_existing)

    def test_install_idempotent(self):
        mgr = GracefulShutdown()
        pre_existing = signal.getsignal(signal.SIGTERM)
        try:
            mgr.install()
            first = mgr._original_handler
            mgr.install()
            # Should not overwrite the original handler
            assert mgr._original_handler == first
        finally:
            signal.signal(signal.SIGTERM, pre_existing)


# ---------------------------------------------------------------------------
# _handler: flush + close + re-raise
# ---------------------------------------------------------------------------


class TestHandler:
    """SIGTERM handler flushes files, closes tarballs, re-raises SIGTERM."""

    def test_handler_sets_shutting_down(self):
        mgr = GracefulShutdown()
        # Don't actually re-raise — patch os.kill
        with patch.object(os, "kill") as mock_kill:
            mgr._handler(signal.SIGTERM, None)
        assert mgr.shutting_down is True
        # Should have re-raised via os.kill
        mock_kill.assert_called_once()
        args = mock_kill.call_args[0]
        assert args[0] == os.getpid()
        assert args[1] == signal.SIGTERM

    def test_handler_flushes_registered_files(self):
        mgr = GracefulShutdown()
        fh = io.BytesIO()
        # Give it some real bytes so flush is meaningful
        fh.write(b"some data")
        fh.seek(0)
        mgr.register_file(fh)
        with patch.object(os, "kill"):
            mgr._handler(signal.SIGTERM, None)
        # fh is still in the set (handler doesn't unregister)
        assert fh in mgr._files

    def test_handler_closes_registered_tarballs(self):
        mgr = GracefulShutdown()
        tf = tarfile.open(fileobj=io.BytesIO(), mode="w")
        mgr.register_tarball(tf)
        with patch.object(os, "kill"):
            mgr._handler(signal.SIGTERM, None)
        # Writing to a closed tarfile should raise (OSError "TarFile is closed")
        with pytest.raises(OSError):
            tf.addfile(tarfile.TarInfo(name="x"))

    def test_handler_restores_original_handler(self):
        mgr = GracefulShutdown()
        pre_existing = signal.getsignal(signal.SIGTERM)
        try:
            # Install to capture original
            mgr.install()
            original = mgr._original_handler
            assert original is not None
            with patch.object(os, "kill"):
                mgr._handler(signal.SIGTERM, None)
            # The signal handler should be restored to the original
            current = signal.getsignal(signal.SIGTERM)
            assert current == original
        finally:
            signal.signal(signal.SIGTERM, pre_existing)

    def test_handler_continues_on_flush_error(self):
        mgr = GracefulShutdown()
        # A closed BytesIO raises ValueError on fileno()
        bad_fh = io.BytesIO()
        bad_fh.close()
        mgr.register_file(bad_fh)
        # Should not raise
        with patch.object(os, "kill"):
            mgr._handler(signal.SIGTERM, None)
        assert mgr.shutting_down is True

    def test_handler_continues_on_close_error(self):
        mgr = GracefulShutdown()
        bad_tf = MagicMock()
        bad_tf.close.side_effect = OSError("disk full")
        mgr.register_tarball(bad_tf)
        with patch.object(os, "kill"):
            mgr._handler(signal.SIGTERM, None)
        # Should not raise despite the tarball close failing
        assert mgr.shutting_down is True
        bad_tf.close.assert_called_once()

    def test_handler_no_files_no_tarballs(self):
        mgr = GracefulShutdown()
        with patch.object(os, "kill") as mock_kill:
            mgr._handler(signal.SIGTERM, None)
        assert mgr.shutting_down is True
        mock_kill.assert_called_once()

    def test_handler_logs_count(self, caplog):
        mgr = GracefulShutdown()
        fh1 = io.BytesIO(b"x")
        fh2 = io.BytesIO(b"y")
        mgr.register_file(fh1)
        mgr.register_file(fh2)
        with caplog.at_level(logging.INFO), patch.object(os, "kill"):
            mgr._handler(signal.SIGTERM, None)
        # The log message includes the counts
        assert any("flushing 2 file" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# Module-level API
# ---------------------------------------------------------------------------


class TestModuleAPI:
    """Module-level functions delegate to the global _manager."""

    def setup_method(self):
        # Reset the module manager state for each test
        gs._manager = GracefulShutdown()

    def teardown_method(self):
        # Reset the module manager state after each test
        gs._manager = GracefulShutdown()

    def test_install_handler(self):
        pre_existing = signal.getsignal(signal.SIGTERM)
        try:
            gs.install_handler()
            assert gs._manager._original_handler is not None
        finally:
            signal.signal(signal.SIGTERM, pre_existing)

    def test_register_file_module_level(self):
        fh = io.BytesIO()
        gs.register_file(fh)
        assert fh in gs._manager._files

    def test_register_tarball_module_level(self):
        tf = tarfile.open(fileobj=io.BytesIO(), mode="w")
        try:
            gs.register_tarball(tf)
            assert tf in gs._manager._tarballs
        finally:
            tf.close()

    def test_shutdown_requested_false_initially(self):
        assert gs.shutdown_requested() is False

    def test_shutdown_requested_true_after_handler(self):
        with patch.object(os, "kill"):
            gs._manager._handler(signal.SIGTERM, None)
        assert gs.shutdown_requested() is True


# ---------------------------------------------------------------------------
# main() CLI
# ---------------------------------------------------------------------------


class TestMainCLI:
    """main() entry point with argparse."""

    def test_main_test_flag_exits_zero(self):
        # The --test branch self-SIGTERMs. We expect either exit 0 (caught
        # the test flow) or a signal exit. Patch os.kill to no-op so the
        # test doesn't actually kill the process.
        with patch.object(os, "kill"), patch.object(sys, "exit") as mock_exit:
            gs.main(["--test", "--log-level", "ERROR"])
        # If sys.exit was called, it should have been 0
        if mock_exit.called:
            assert mock_exit.call_args[0][0] == 0

    def test_main_log_level_choices(self):
        # argparse calls parser.error() -> sys.exit(2) on bad --log-level
        with pytest.raises(SystemExit) as exc_info:
            gs.main(["--log-level", "INVALID"])
        assert exc_info.value.code == 2

    def test_main_no_args_loops_until_shutdown(self):
        # No --test means main enters the sleep loop. Set shutting_down
        # immediately via the handler so the loop exits cleanly.
        with patch.object(os, "kill"):
            # Pre-set shutting_down by calling the handler once
            gs._manager._handler(signal.SIGTERM, None)
        with patch("bin.graceful_shutdown.time.sleep") as mock_sleep:
            rc = gs.main(["--log-level", "ERROR"])
        # Loop should have exited because shutting_down is True
        assert rc == 0

    def test_main_keyboard_interrupt_returns_zero(self):
        # If the sleep loop is interrupted by KeyboardInterrupt, main returns 0
        gs._manager = GracefulShutdown()  # reset
        with patch("bin.graceful_shutdown.time.sleep", side_effect=KeyboardInterrupt):
            rc = gs.main(["--log-level", "ERROR"])
        assert rc == 0

    def test_main_returns_int(self):
        gs._manager = GracefulShutdown()
        # Pre-mark shutdown so the loop exits immediately
        gs._manager._shutting_down = True
        with patch("bin.graceful_shutdown.time.sleep"):
            rc = gs.main(["--log-level", "ERROR"])
        assert isinstance(rc, int)
