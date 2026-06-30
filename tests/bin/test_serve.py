#!/usr/bin/env python3
"""Tests for bin/serve.py — CLI launcher for the oyster-agent-runner HTTP API.

Purpose:
``bin/serve.py`` is a tiny CLI shim that parses ``--host`` / ``--port`` /
``--reload`` / ``--log-level``, gates launch on the ``OYSTER_API_TOKEN``
env var, and hands off to :func:`uvicorn.run`. It also refuses to launch
if ``uvicorn`` is not importable, and only then imports
``oyster_agent_runner.server.create_app``.

Coverage:
- _parse_args: defaults (host=127.0.0.1, port=8089, reload=False,
  log-level=info); custom values propagated; --reload is a flag; --port
  is int-coerced; unknown flag → SystemExit; --help → SystemExit; invalid
  log-level choice → SystemExit.
- main: missing OYSTER_API_TOKEN → exit 2 + ERROR message on stderr, no
  uvicorn import attempted; with token set + uvicorn stubbed missing →
  exit 2 + uvicorn-install ERROR; happy path with token + uvicorn stubbed
  present → calls uvicorn.run with the right kwargs, exits 0, and
  imports ``oyster_agent_runner.server.create_app`` exactly once;
  CLI flags (host/port/reload/log-level) reach uvicorn.run unmodified.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add bin/ to sys.path so the module is importable as a top-level name
_BIN_DIR = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(_BIN_DIR))

import serve as m  # noqa: E402

# ---------------------------------------------------------------------------
# _parse_args
# ---------------------------------------------------------------------------


class TestParseArgs:
    """_parse_args returns a Namespace with the documented defaults."""

    def test_defaults(self):
        """No args → host=127.0.0.1, port=8089, reload=False, log-level=info."""
        args = m._parse_args([])
        assert args.host == "127.0.0.1"
        assert args.port == 8089
        args.reload is False
        assert args.log_level == "info"

    def test_custom_host(self):
        args = m._parse_args(["--host", "0.0.0.0"])
        assert args.host == "0.0.0.0"
        # other fields keep their defaults
        assert args.port == 8089

    def test_custom_port(self):
        args = m._parse_args(["--port", "9090"])
        assert args.port == 9090
        assert isinstance(args.port, int)

    def test_reload_flag(self):
        """--reload is a store_true flag (presence toggles, absence is False)."""
        assert m._parse_args([]).reload is False
        assert m._parse_args(["--reload"]).reload is True

    def test_log_level_choices(self):
        """All six documented log levels are accepted."""
        for level in ("critical", "error", "warning", "info", "debug", "trace"):
            args = m._parse_args(["--log-level", level])
            assert args.log_level == level

    def test_invalid_log_level_rejected(self):
        """An unknown log level triggers argparse SystemExit (rc=2)."""
        with pytest.raises(SystemExit):
            m._parse_args(["--log-level", "verbose"])

    def test_help_exits(self):
        """--help triggers argparse SystemExit (rc=0)."""
        with pytest.raises(SystemExit) as ei:
            m._parse_args(["--help"])
        # argparse exits with 0 on --help
        assert ei.value.code == 0

    def test_unknown_flag_rejected(self):
        """An unknown flag triggers argparse SystemExit (rc=2)."""
        with pytest.raises(SystemExit):
            m._parse_args(["--no-such-flag"])

    def test_argv_none_uses_sys_argv(self):
        """Passing argv=None falls through to argparse (uses sys.argv)."""
        # We can't easily test the sys.argv path without mutating it; instead
        # verify passing an explicit list is independent of sys.argv.
        with patch.object(sys, "argv", ["serve.py", "--port", "7777"]):
            args = m._parse_args(None)
        assert args.port == 7777


# ---------------------------------------------------------------------------
# main — token gate
# ---------------------------------------------------------------------------


class TestMainTokenGate:
    """main() refuses to launch without OYSTER_API_TOKEN (exit code 2)."""

    def test_missing_token_exits_2(self, monkeypatch: pytest.MonkeyPatch, capsys):
        """No OYSTER_API_TOKEN → exit 2 + ERROR on stderr."""
        monkeypatch.delenv("OYSTER_API_TOKEN", raising=False)
        # Sentinel: if uvicorn were touched, this would be called and the test
        # would fail. It must not be — token gate comes first.
        with patch("serve.uvicorn", new=MagicMock(), create=True) as uv:
            rc = m.main([])
        assert rc == 2
        uv.assert_not_called()
        err = capsys.readouterr().err
        assert "OYSTER_API_TOKEN" in err
        assert "ERROR" in err

    def test_empty_token_treated_as_missing(self, monkeypatch: pytest.MonkeyPatch, capsys):
        """An empty OYSTER_API_TOKEN is treated as missing (falsy)."""
        monkeypatch.setenv("OYSTER_API_TOKEN", "")
        with patch("serve.uvicorn", new=MagicMock(), create=True) as uv:
            rc = m.main([])
        assert rc == 2
        uv.assert_not_called()
        assert "OYSTER_API_TOKEN" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# main — uvicorn import gate
# ---------------------------------------------------------------------------


class TestMainUvicornGate:
    """With a token set, main() must ImportError-handle uvicorn absence."""

    def test_uvicorn_import_error_exits_2(self, monkeypatch: pytest.MonkeyPatch, capsys):
        """If the uvicorn import fails, main() exits 2 with a helpful message.

        The SUT does ``import uvicorn`` inside main() under a
        ``try: ... except ImportError:`` block. We force the import to raise
        by patching :func:`builtins.__import__` to raise ImportError when
        the ``uvicorn`` module name is requested. ``create_app`` must NOT
        be called in this branch.
        """
        monkeypatch.setenv("OYSTER_API_TOKEN", "dummy")

        real_import = __import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "uvicorn" or name.startswith("uvicorn."):
                raise ImportError("simulated: uvicorn not installed")
            return real_import(name, globals, locals, fromlist, level)

        create_app_called = MagicMock()
        with (
            patch("builtins.__import__", side_effect=fake_import),
            patch(
                "oyster_agent_runner.server.create_app",
                create_app_called,
                create=True,
            ),
        ):
            rc = m.main([])

        assert rc == 2
        err = capsys.readouterr().err
        assert "uvicorn" in err
        assert "ERROR" in err
        create_app_called.assert_not_called()


# ---------------------------------------------------------------------------
# main — happy path
# ---------------------------------------------------------------------------


def _install_uvicorn_stub(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Make ``import uvicorn`` resolve to a MagicMock with a ``.run`` attr.

    The SUT does ``import uvicorn`` then calls ``uvicorn.run(...)``. We don't
    want to actually run a server. Patching ``sys.modules['uvicorn']``
    short-circuits the import.
    """
    uv = MagicMock(name="uvicorn_stub")
    uv.run = MagicMock(name="uvicorn.run", return_value=None)
    monkeypatch.setitem(sys.modules, "uvicorn", uv)
    return uv


def _patch_create_app(monkeypatch: pytest.MonkeyPatch, return_value=None) -> MagicMock:
    """Force ``oyster_agent_runner.server.create_app`` to return a sentinel.

    Reload the server module first so the patch takes effect even when the
    SUT imports it lazily via ``from oyster_agent_runner.server import
    create_app`` (which binds the name at import time).
    """
    server_mod = importlib.import_module("oyster_agent_runner.server")
    importlib.reload(server_mod)
    sentinel = MagicMock(name="app", return_value=return_value)
    monkeypatch.setattr(server_mod, "create_app", sentinel)
    return sentinel


class TestMainHappyPath:
    """With token + uvicorn available, main() hands off to uvicorn.run."""

    def test_uvicorn_run_called_with_defaults(self, monkeypatch: pytest.MonkeyPatch, capsys):
        """No CLI flags → uvicorn.run(app, host=127.0.0.1, port=8089, ...)."""
        monkeypatch.setenv("OYSTER_API_TOKEN", "dummy")
        uv = _install_uvicorn_stub(monkeypatch)
        create_app = _patch_create_app(monkeypatch, return_value=MagicMock(name="app"))

        rc = m.main([])

        assert rc == 0
        create_app.assert_called_once_with()
        uv.run.assert_called_once()
        _, kwargs = uv.run.call_args
        # positional app is the same object create_app returned
        assert uv.run.call_args.args[0] is create_app.return_value
        assert kwargs["host"] == "127.0.0.1"
        assert kwargs["port"] == 8089
        assert kwargs["reload"] is False
        assert kwargs["log_level"] == "info"
        # no error on stderr
        assert "ERROR" not in capsys.readouterr().err

    def test_uvicorn_run_receives_custom_flags(self, monkeypatch: pytest.MonkeyPatch):
        """--host / --port / --reload / --log-level are forwarded verbatim."""
        monkeypatch.setenv("OYSTER_API_TOKEN", "dummy")
        uv = _install_uvicorn_stub(monkeypatch)
        _patch_create_app(monkeypatch, return_value=MagicMock(name="app"))

        rc = m.main(
            [
                "--host",
                "0.0.0.0",
                "--port",
                "9999",
                "--reload",
                "--log-level",
                "debug",
            ]
        )

        assert rc == 0
        _, kwargs = uv.run.call_args
        assert kwargs["host"] == "0.0.0.0"
        assert kwargs["port"] == 9999
        assert kwargs["reload"] is True
        assert kwargs["log_level"] == "debug"

    def test_create_app_called_only_when_token_and_uvicorn_present(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """``create_app`` is only imported/called in the happy-path branch.

        Both prior gates (missing token, uvicorn ImportError) must skip
        create_app; this test pins the happy-path branch does call it once.
        """
        monkeypatch.setenv("OYSTER_API_TOKEN", "dummy")
        _install_uvicorn_stub(monkeypatch)
        create_app = _patch_create_app(monkeypatch, return_value=MagicMock(name="app"))

        m.main([])

        create_app.assert_called_once_with()

    def test_uvicorn_import_error_does_not_call_create_app(
        self, monkeypatch: pytest.MonkeyPatch, capsys
    ):
        """If uvicorn import fails, create_app must NOT be called.

        Guards against a refactor that would accidentally re-order the
        import-gate check to before the uvicorn gate.
        """
        monkeypatch.setenv("OYSTER_API_TOKEN", "dummy")
        real_import = __import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "uvicorn" or name.startswith("uvicorn."):
                raise ImportError("simulated: uvicorn not installed")
            return real_import(name, globals, locals, fromlist, level)

        create_app_called = MagicMock()
        with (
            patch("builtins.__import__", side_effect=fake_import),
            patch(
                "oyster_agent_runner.server.create_app",
                create_app_called,
                create=True,
            ),
        ):
            rc = m.main([])

        assert rc == 2
        create_app_called.assert_not_called()
        assert "uvicorn" in capsys.readouterr().err
