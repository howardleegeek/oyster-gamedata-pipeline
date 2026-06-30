#!/usr/bin/env python3
"""Tests for bin/auto_install_error_handler.py — G234 global Python error hooks.

Covers:
- _get_temp_dir: returns a unique Path under tempfile.gettempdir with prefix
- _format_exception: returns non-empty traceback text for a real exception
- is_installed: True after _install_hooks, False after _uninstall_hooks
- _install_hooks: idempotent (returns False on second call), installs
  a non-sentinel excepthook
- _uninstall_hooks: returns False when not installed; True when installed;
  restores the prior excepthook captured at install time
- main: --check returns 1 when not installed, 0 when installed; --install
  returns 0; --uninstall returns 0 whether or not hooks were installed
  (no-op when not); default (no flags) installs hooks and prints the
  "default mode" message
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Add bin/ to sys.path so the module is importable as a top-level name
_BIN_DIR = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(_BIN_DIR))

from bin.auto_install_error_handler import (  # noqa: E402
    _format_exception,
    _get_temp_dir,
    _install_hooks,
    _uninstall_hooks,
    is_installed,
    main,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_handler(monkeypatch):
    """Provide a clean install-state for the duration of one test.

    The module auto-installs on import, so we always uninstall at start and
    restore the real original excepthook (sys.__excepthook__) at the end.
    This keeps tests independent and doesn't leak hooks between tests.
    """
    # Uninstall the import-time auto-install so each test starts clean.
    _uninstall_hooks()
    # Save the true system excepthook (post-uninstall) so we can restore it
    # at the end of the test even if the test installs a new hook.
    true_original = sys.excepthook
    try:
        yield
    finally:
        _uninstall_hooks()
        sys.excepthook = true_original


# ---------------------------------------------------------------------------
# _get_temp_dir
# ---------------------------------------------------------------------------


class TestGetTempDir:
    """Tests for the temp-dir helper."""

    def test_returns_path(self):
        p = _get_temp_dir()
        assert isinstance(p, Path)
        # Path should exist (mkdtemp creates it)
        assert p.exists()
        assert p.is_dir()

    def test_prefix_matches(self):
        p = _get_temp_dir()
        assert p.name.startswith("g234_errors_")

    def test_unique_per_call(self):
        """Two consecutive calls should not return the same directory."""
        a = _get_temp_dir()
        b = _get_temp_dir()
        assert a != b


# ---------------------------------------------------------------------------
# _format_exception
# ---------------------------------------------------------------------------


class TestFormatException:
    """Tests for the traceback formatter."""

    def test_returns_string(self):
        try:
            raise ValueError("boom")
        except ValueError:
            import sys as _sys

            tb = _sys.exc_info()[2]
            out = _format_exception(ValueError, ValueError("boom"), tb)
        assert isinstance(out, str)
        assert "ValueError" in out
        assert "boom" in out


# ---------------------------------------------------------------------------
# Hook install / uninstall / is_installed
# ---------------------------------------------------------------------------


class TestHookLifecycle:
    """Tests for the install/uninstall state machine."""

    def test_uninstall_when_not_installed_returns_false(self, fresh_handler):
        # After fresh_handler fixture, hooks are NOT installed.
        assert is_installed() is False
        assert _uninstall_hooks() is False

    def test_install_returns_true_first_time(self, fresh_handler):
        assert _install_hooks() is True
        assert is_installed() is True

    def test_install_is_idempotent(self, fresh_handler):
        assert _install_hooks() is True
        # Second call: already installed, must return False (not raise).
        assert _install_hooks() is False
        assert is_installed() is True

    def test_uninstall_returns_true_after_install(self, fresh_handler):
        _install_hooks()
        assert _uninstall_hooks() is True
        assert is_installed() is False

    def test_uninstall_restores_original_excepthook(self, fresh_handler):
        sentinel = lambda *a, **k: None  # noqa: E731
        sys.excepthook = sentinel
        _install_hooks()
        # After install, excepthook should NOT be the sentinel anymore.
        assert sys.excepthook is not sentinel
        _uninstall_hooks()
        # After uninstall, excepthook should be the sentinel (the "original"
        # captured at install time).
        assert sys.excepthook is sentinel


# ---------------------------------------------------------------------------
# main (CLI)
# ---------------------------------------------------------------------------


class TestMain:
    """Tests for the CLI entry point."""

    def test_check_returns_1_when_not_installed(self, fresh_handler, capsys):
        rc = main(["--check"])
        out = capsys.readouterr().out
        assert rc == 1
        assert "installed: False" in out

    def test_check_returns_0_when_installed(self, fresh_handler, capsys):
        _install_hooks()
        rc = main(["--check"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "installed: True" in out

    def test_install_succeeds(self, fresh_handler, capsys):
        rc = main(["--install"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "installed" in out
        assert is_installed() is True

    def test_install_when_already_installed_succeeds(self, fresh_handler, capsys):
        _install_hooks()
        rc = main(["--install"])
        out = capsys.readouterr().out
        # Either "already installed" or "installed" is acceptable, but
        # the call must succeed.
        assert rc == 0
        assert "installed" in out

    def test_uninstall_succeeds_after_install(self, fresh_handler, capsys):
        _install_hooks()
        rc = main(["--uninstall"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "uninstalled" in out
        assert is_installed() is False

    def test_uninstall_when_not_installed(self, fresh_handler, capsys):
        rc = main(["--uninstall"])
        out = capsys.readouterr().out
        # When not installed, the CLI prints "were not installed" and
        # still returns 0 (matches the code path: success, no-op).
        assert rc == 0
        assert "not installed" in out

    def test_default_no_args_runs_install(self, fresh_handler, capsys):
        # With no args, main() falls through to the default install branch
        # and prints the "default mode" message.
        rc = main([])
        out = capsys.readouterr().out
        assert rc == 0
        assert "default mode" in out
        assert is_installed() is True
