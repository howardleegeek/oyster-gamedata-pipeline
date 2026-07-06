"""Regression tests: bin/telemetry.py must not silently swallow exceptions.

Pre-fix, five call sites caught exceptions with no bound name and no
logging:

  1. ``_read_consent``            (line ~82)  -> returns ``{}``
  2. ``_read_counter_file``       (line ~128) -> returns ``0``
  3. ``_write_counter_file``      (line ~137) -> ``pass``
  4. ``_has_uploaded_today``      (line ~220) -> returns ``False``
  5. ``_mark_uploaded_today``     (line ~232) -> ``pass``

All five now bind the exception to a named local and emit a
``logger.debug(...)`` call so the silent failure is observable. Control
flow is preserved (best-effort telemetry must not raise).

Self-review: scope = one file (bin/telemetry.py), one logical change
(bind previously-bare excepts to ``exc`` + logger.debug in five call
sites). The bound names are referenced in the format strings, the log
calls are observation-only (no swallow of a re-raise), and the original
fall-through behavior (return {} / 0 / False, or pass) is preserved.
"""

from __future__ import annotations

import ast
import logging
import re
import sys
from pathlib import Path

import pytest

# Add repo root to sys.path so `import bin.telemetry` resolves
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BIN_DIR = REPO_ROOT / "bin"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(BIN_DIR))

import bin.telemetry as telemetry  # noqa: E402

TELEMETRY_SRC = (BIN_DIR / "telemetry.py").read_text(encoding="utf-8")


def _strip_comments_and_docstrings(src: str) -> str:
    """Drop ``#`` comments and triple-quoted docstrings so the static
    checks below don't false-match on prose mentioning bare ``except``."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return src
    # Collect line ranges to blank out
    drop_ranges: list[tuple[int, int]] = []

    def _add(node: ast.AST) -> None:
        drop_ranges.append((node.lineno, node.end_lineno or node.lineno))

    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str):
                _add(node)
    # Build the stripped text
    lines = src.splitlines()
    keep: list[str] = []
    for i, line in enumerate(lines, start=1):
        if any(start <= i <= end for start, end in drop_ranges):
            keep.append("")
        else:
            # Also strip trailing ``#`` comments so "except ...  # noqa" is fine
            stripped = line.split("#", 1)[0]
            keep.append(stripped)
    return "\n".join(keep)


# ---------------------------------------------------------------------------
# 1. Static checks
# ---------------------------------------------------------------------------


def test_module_has_logger() -> None:
    """The module must expose a module-level logger for debug output."""
    assert hasattr(telemetry, "logger"), "module-level logger missing"
    assert isinstance(telemetry.logger, logging.Logger)
    assert telemetry.logger.name == "telemetry" or telemetry.logger.name.endswith(
        ".telemetry"
    ) or telemetry.logger.name == "bin.telemetry"


def test_no_bare_except_or_except_exception() -> None:
    """The five call sites we targeted must be bound to a name.

    We use AST rather than regex so we ignore legitimate unbound
    ``except ImportError:`` (module-level optional import) and
    ``except FileNotFoundError:`` in ``main()`` (deliberate no-op for
    the ``--force`` flag path that has its own consumer-visible print).
    """
    tree = ast.parse(TELEMETRY_SRC)
    unbound: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if node.name is not None:
            continue
        if node.type is None:
            # bare `except:` — should never appear here
            unbound.append((node.lineno, "bare except:"))
            continue
        type_src = ast.unparse(node.type)
        # These two are allowed to remain unbound
        if type_src == "ImportError" or type_src == "FileNotFoundError":
            continue
        # Anything else that catches a multi-failure path is a silent swallow
        # and should have been bound to a name for debug logging.
        unbound.append((node.lineno, f"except {type_src}:"))
    assert not unbound, (
        f"Found {len(unbound)} unbound except clause(s); all best-effort "
        f"swallows should bind to a name for logger.debug visibility: "
        f"{unbound}"
    )


def test_logger_debug_calls_present() -> None:
    """The five surfaced call sites must each emit a logger.debug call.

    We require >=5 logger.debug calls that reference the bound `exc` so
    we know they aren't pre-existing DEBUGs unrelated to this change.
    """
    debug_with_exc = re.findall(
        r"logger\.debug\([^)]*exc[^)]*\)", TELEMETRY_SRC
    )
    assert len(debug_with_exc) >= 5, (
        f"Expected >=5 logger.debug(...) calls that reference the bound "
        f"`exc`, found {len(debug_with_exc)}: {debug_with_exc}"
    )


def test_module_compiles() -> None:
    """The module must compile without SyntaxError."""
    import py_compile

    py_compile.compile(str(BIN_DIR / "telemetry.py"), doraise=True)


# ---------------------------------------------------------------------------
# 2. Runtime checks – trigger each swallow and confirm a DEBUG log fires
# ---------------------------------------------------------------------------


def test_read_consent_swallows_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    """_read_consent should log at DEBUG and return {} on parse failure."""
    bad = REPO_ROOT / "tests" / "fixtures" / "_telemetry_bad_consent.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{ this is not valid json", encoding="utf-8")
    try:
        with caplog.at_level(logging.DEBUG, logger="bin.telemetry"):
            result = telemetry._read_consent(bad)
        assert result == {}
        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("consent read failed" in r.getMessage() for r in debug_records), (
            f"Expected a DEBUG log mentioning 'consent read failed', "
            f"got: {[r.getMessage() for r in debug_records]}"
        )
    finally:
        if bad.exists():
            bad.unlink()


def test_read_counter_file_swallows_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """_read_counter_file should log at DEBUG and return 0 on garbage input."""
    bad = REPO_ROOT / "tests" / "fixtures" / "_telemetry_counter_garbage.txt"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("not-an-integer", encoding="utf-8")
    try:
        with caplog.at_level(logging.DEBUG, logger="bin.telemetry"):
            result = telemetry._read_counter_file(bad)
        assert result == 0
        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("counter read failed" in r.getMessage() for r in debug_records), (
            f"Expected a DEBUG log mentioning 'counter read failed', "
            f"got: {[r.getMessage() for r in debug_records]}"
        )
    finally:
        if bad.exists():
            bad.unlink()


def test_write_counter_file_swallows_and_logs(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    """_write_counter_file should log at DEBUG and not raise on OSError."""
    # Create a file at the target path so that the parent-mkdir will fail.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    target = blocker / "child" / "counter.txt"  # parent is a regular file
    with caplog.at_level(logging.DEBUG, logger="bin.telemetry"):
        # Must not raise
        telemetry._write_counter_file(target, 5)
    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("counter write failed" in r.getMessage() for r in debug_records), (
        f"Expected a DEBUG log mentioning 'counter write failed', "
        f"got: {[r.getMessage() for r in debug_records]}"
    )


def test_has_uploaded_today_swallows_and_logs(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_has_uploaded_today should log at DEBUG and return False on OSError."""
    # Make Path.read_text raise OSError
    def _raise(self: Path, *a: object, **kw: object) -> str:  # noqa: ANN001
        raise OSError("simulated read failure")

    monkeypatch.setattr(Path, "read_text", _raise)
    with caplog.at_level(logging.DEBUG, logger="bin.telemetry"):
        result = telemetry._has_uploaded_today()
    assert result is False
    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("last-upload marker read failed" in r.getMessage() for r in debug_records), (
        f"Expected a DEBUG log mentioning 'last-upload marker read failed', "
        f"got: {[r.getMessage() for r in debug_records]}"
    )


def test_mark_uploaded_today_swallows_and_logs(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_mark_uploaded_today should log at DEBUG and not raise on OSError."""
    def _raise(self: Path, *a: object, **kw: object) -> None:  # noqa: ANN001
        raise OSError("simulated write failure")

    monkeypatch.setattr(Path, "write_text", _raise)
    with caplog.at_level(logging.DEBUG, logger="bin.telemetry"):
        # Must not raise
        telemetry._mark_uploaded_today()
    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert any(
        "last-upload marker write failed" in r.getMessage() for r in debug_records
    ), (
        f"Expected a DEBUG log mentioning 'last-upload marker write failed', "
        f"got: {[r.getMessage() for r in debug_records]}"
    )
