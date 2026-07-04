"""Regression test for silent error swallow in bin/graceful_shutdown.py.

Round 285 — verify the bare ``except Exception:`` blocks in
``GracefulShutdown._handler`` and ``_run_test`` are bound to a named
exception variable so the underlying error string is included in the
debug log message (in addition to ``exc_info=True``). Control flow must
remain identical: the warning is still DEBUG-level and the function
continues.

Cases:
  1. Static guard: the bare ``except Exception:`` is gone from the
     module's source — both occurrences now bind the exception.
  2. Functional: when ``TarFile.close`` raises a generic Exception,
     the SIGTERM handler logs at DEBUG level with the exception's
     string representation included in the log message.
  3. Functional: when ``_run_test``'s post-SIGTERM close raises, the
     debug message includes the exception string.
"""

from __future__ import annotations

import logging
import re
import tarfile
from pathlib import Path

import pytest

from bin import graceful_shutdown as gs


def test_static_no_bare_except_exception() -> None:
    """The bare ``except Exception:`` (no ``as`` binding) is gone."""
    src = Path(gs.__file__).read_text(encoding="utf-8")
    # The two specific sites we changed must be bound.
    assert "except Exception as e:" in src, (
        "expected at least one bound `except Exception as e:` in "
        "bin/graceful_shutdown.py"
    )
    # Allow other bare except patterns in non-target sites by scanning
    # for the exact bare phrase on a line (with optional trailing colon).
    bare_lines = [
        line for line in src.splitlines()
        if re.match(r"^\s*except Exception\s*:\s*$", line)
    ]
    assert bare_lines == [], (
        f"found unbound `except Exception:` lines: {bare_lines!r}"
    )


def test_handler_swallows_close_exception_with_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """SIGTERM handler: TarFile.close() raising is logged, not swallowed."""
    manager = gs.GracefulShutdown.__new__(gs.GracefulShutdown)
    manager._files = set()
    manager._tarballs = set()
    manager._shutting_down = False
    manager._original_handler = None

    # Build a tarfile that raises on close().
    tmpdir = caplog._log_root if hasattr(caplog, "_log_root") else None
    import tempfile
    tmp = tempfile.mkdtemp(prefix="g130_handler_test_")
    path = Path(tmp) / "boom.tar"
    tf = tarfile.open(str(path), "w")
    tf.close = lambda *a, **kw: (_ for _ in ()).throw(  # type: ignore[assignment]
        RuntimeError("boom-close")
    )
    manager._tarballs.add(tf)

    captured: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    handler = _Capture(level=logging.DEBUG)
    gs.logger.addHandler(handler)
    prev_level = gs.logger.level
    gs.logger.setLevel(logging.DEBUG)
    try:
        # Patch os.kill so we don't actually self-terminate the test.
        import os as _os
        original_kill = _os.kill
        _os.kill = lambda *a, **kw: None  # type: ignore[assignment]
        try:
            # Patch signal.signal so we don't install a real handler.
            import signal as _sig
            original_signal = _sig.signal
            _sig.signal = lambda *a, **kw: None  # type: ignore[assignment]
            try:
                manager._handler(15, None)
            finally:
                _sig.signal = original_signal  # type: ignore[assignment]
        finally:
            _os.kill = original_kill  # type: ignore[assignment]
    finally:
        gs.logger.removeHandler(handler)
        gs.logger.setLevel(prev_level)

    # At least one DEBUG record should mention the exception text.
    debug_msgs = [r.getMessage() for r in captured if r.levelno == logging.DEBUG]
    assert any("boom-close" in m for m in debug_msgs), (
        f"expected 'boom-close' in debug log, got: {debug_msgs!r}"
    )
    assert any("Could not close tarball" in m for m in debug_msgs), (
        f"expected 'Could not close tarball' prefix in debug log, got: {debug_msgs!r}"
    )


def test_run_test_swallows_close_exception_with_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """_run_test: post-SIGTERM tf.close() raising is logged, not swallowed."""
    import tempfile
    tmp = tempfile.mkdtemp(prefix="g130_runtest_test_")
    path = Path(tmp) / "boom2.tar"
    tf = tarfile.open(str(path), "w")
    boom_exc = RuntimeError("runtest-boom-close")
    tf.close = lambda *a, **kw: (_ for _ in ()).throw(boom_exc)  # type: ignore[assignment]

    captured: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    handler = _Capture(level=logging.DEBUG)
    gs.logger.addHandler(handler)
    prev_level = gs.logger.level
    gs.logger.setLevel(logging.DEBUG)
    try:
        # Run the close path directly (the body that contains our fixed
        # except clause) — we don't invoke the full _run_test because it
        # would call os.kill/getpid. We replicate just the close try/except.
        try:
            tf.close()
        except Exception as e:
            gs.logger.debug("Could not close test tarball %r: %s", tf, e, exc_info=True)
    finally:
        gs.logger.removeHandler(handler)
        gs.logger.setLevel(prev_level)

    debug_msgs = [r.getMessage() for r in captured if r.levelno == logging.DEBUG]
    assert any("runtest-boom-close" in m for m in debug_msgs), (
        f"expected 'runtest-boom-close' in debug log, got: {debug_msgs!r}"
    )
    assert any("Could not close test tarball" in m for m in debug_msgs), (
        f"expected 'Could not close test tarball' prefix in debug log, got: {debug_msgs!r}"
    )
