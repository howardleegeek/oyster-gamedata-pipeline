"""Regression test: surface silent error in install_fabric_loader.

The bare ``except Exception as e:`` block around the fabric-installer
``subprocess.run`` call (formerly only bound ``e`` and returned a
caller-facing reason string) now also emits a DEBUG log record on the
module logger so the failure is observable to operators. Control flow is
unchanged: the installer still returns ``(False, "...crashed: {e}")``,
which the upstream caller treats as "no mod, fall back to placeholder
camera fields".

This test asserts:
  1. AST: every ``except Exception:`` in ``install_fabric_loader`` binds
     the exception as ``e`` (no silent bare-except).
  2. The module exposes a module-level ``logger``.
  3. When ``subprocess.run`` raises a generic exception, the function
     returns ``(False, "...crashed: ...")`` AND a DEBUG log record is
     emitted on the module logger with ``exc_info``.
  4. ``subprocess.TimeoutExpired`` still returns the timeout reason
     string (narrow except preserved).
  5. Happy path: when ``subprocess.run`` returns rc=0, the function
     returns ``(True, "fabric loader installed")`` — no regression.
"""

from __future__ import annotations

import ast
import importlib.util
import logging
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SRC = _REPO_ROOT / "bin" / "install_fabric_loader.py"

# Load module via importlib so we don't trigger PyInstaller-related
# import side effects in the package __init__.
_spec = importlib.util.spec_from_file_location("install_fabric_loader", _SRC)
ifl = importlib.util.module_from_spec(_spec)
sys.modules["install_fabric_loader"] = ifl
_spec.loader.exec_module(ifl)


def test_no_bare_except_in_install_fabric_loader() -> None:
    """All `except Exception:` handlers in install_fabric_loader must bind `e`."""
    src = _SRC.read_text()
    tree = ast.parse(src)
    fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "install_fabric_loader":
            fn = node
            break
    assert fn is not None, "install_fabric_loader function not found"
    for handler in fn.body:
        if isinstance(handler, ast.Try):
            for h in handler.handlers:
                if h.type is None:
                    continue
                if isinstance(h.type, ast.Name) and h.type.id == "Exception":
                    assert h.name is not None, (
                        "bare `except Exception:` found at line "
                        f"{h.lineno} — must bind the exception as `e`"
                    )


def test_module_exposes_logger() -> None:
    """The module must define a module-level `logger` for diagnostics."""
    assert hasattr(ifl, "logger"), "install_fabric_loader must expose a module logger"
    assert isinstance(ifl.logger, logging.Logger)
    assert ifl.logger.name == "install_fabric_loader"


def test_subprocess_crash_emits_debug_log(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A non-timeout crash in subprocess.run emits DEBUG log + returns (False, ...)."""
    fake_jar = _REPO_ROOT / "bin" / "install_fabric_loader.py"  # exists on disk
    assert fake_jar.is_file()

    def _fake_run(*args, **kwargs):  # noqa: ANN001, ANN002
        raise RuntimeError("boom: installer exploded")

    monkeypatch.setattr(ifl.subprocess, "run", _fake_run)

    with caplog.at_level(logging.DEBUG, logger="install_fabric_loader"):
        ok, reason = ifl.install_fabric_loader(
            mc_dir=_REPO_ROOT,  # unused; we crash before reading it
            fabric_installer_jar=fake_jar,
        )

    assert ok is False
    assert "crashed" in reason
    assert "boom" in reason

    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert debug_records, "expected at least one DEBUG log record"
    debug_msg = " ".join(r.getMessage() for r in debug_records)
    assert "fabric installer crashed" in debug_msg
    # exc_info carries the original traceback — proves the exception is bound
    assert any(r.exc_info is not None for r in debug_records), (
        "expected exc_info on at least one DEBUG record"
    )


def test_timeout_still_returns_timeout_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """subprocess.TimeoutExpired still returns the timeout reason (narrow except)."""
    fake_jar = _REPO_ROOT / "bin" / "install_fabric_loader.py"
    assert fake_jar.is_file()

    def _fake_run(*args, **kwargs):  # noqa: ANN001, ANN002
        raise subprocess.TimeoutExpired(cmd=["java"], timeout=120)

    monkeypatch.setattr(ifl.subprocess, "run", _fake_run)

    ok, reason = ifl.install_fabric_loader(
        mc_dir=_REPO_ROOT,
        fabric_installer_jar=fake_jar,
    )

    assert ok is False
    assert "timed out" in reason


def test_happy_path_returns_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """A zero-returncode subprocess.run returns (True, 'fabric loader installed')."""
    fake_jar = _REPO_ROOT / "bin" / "install_fabric_loader.py"
    assert fake_jar.is_file()

    class _FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(*args, **kwargs):  # noqa: ANN001, ANN002
        return _FakeResult()

    monkeypatch.setattr(ifl.subprocess, "run", _fake_run)

    ok, reason = ifl.install_fabric_loader(
        mc_dir=_REPO_ROOT,
        fabric_installer_jar=fake_jar,
    )

    assert ok is True
    assert reason == "fabric loader installed"
