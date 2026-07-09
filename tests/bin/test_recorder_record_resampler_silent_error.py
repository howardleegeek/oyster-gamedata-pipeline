"""Regression tests: bin/recorder_record_resampler.py must surface silent errors.

The 30 Hz resampler (G271, W31) historically swallowed three categories of
parse failure inside CLI/event ingestion paths:

  1. ``_normalise_event`` — non-numeric ``t`` field in a JSON event.
  2. ``_apply_event`` (hotbar branch) — non-integer ``slot`` field.
  3. ``_main`` (stdin loop) — malformed JSON line.

In all three, the bare ``except ...: return/continue`` paths were
extended to bind the exception as ``exc`` and call ``logger.debug`` with
contextual fields, so the dropped input is visible at DEBUG level
without changing the existing drop semantics (the events are still
discarded — they were never going to be useful in the frame-aligned
output).

Control flow is preserved: every catch site still drops the bad record
(return None / return / continue), so callers and CLI exit codes are
unaffected. The DEBUG-level messages are silent at the default WARNING
log level.
"""

from __future__ import annotations

import ast
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any, Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BIN_PATH = REPO_ROOT / "bin" / "recorder_record_resampler.py"


def _load_module() -> Any:
    """Load the bin/ script as a module without polluting sys.path."""
    spec = importlib.util.spec_from_file_location(
        "recorder_record_resampler_under_test", BIN_PATH
    )
    assert spec and spec.loader, f"could not load spec for {BIN_PATH}"
    module = importlib.util.module_from_spec(spec)
    # Module must be in sys.modules for @dataclass introspection to work.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_module_compiles() -> None:
    """Source must be syntactically valid Python."""
    ast.parse(BIN_PATH.read_text())


def test_logging_imported_and_logger_defined() -> None:
    """The module must import ``logging`` and define a module-level ``logger``."""
    src = BIN_PATH.read_text()
    assert "import logging" in src, "expected 'import logging' at module top"
    assert "logger = logging.getLogger(__name__)" in src, (
        "expected module-level logger = logging.getLogger(__name__)"
    )

    mod = _load_module()
    assert hasattr(mod, "logger"), "module missing 'logger' attribute"
    assert isinstance(mod.logger, logging.Logger)
    # The logger name is derived from __name__; the spec name we used to
    # load it (`recorder_record_resampler_under_test`) is reflected here.
    # We only require that the basename of the file is present.
    assert "recorder_record_resampler" in mod.logger.name, (
        f"unexpected logger name: {mod.logger.name!r}"
    )


def test_normalise_event_bad_t_binds_exception_and_logs() -> None:
    """_normalise_event must bind the exception as ``exc`` and call logger.debug."""
    src = BIN_PATH.read_text()
    tree = ast.parse(src)
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_normalise_event"
    )
    handler = _find_handler_for_exceptions(fn, ("TypeError", "ValueError"))
    assert handler is not None, (
        "_normalise_event: no handler for (TypeError, ValueError) found"
    )
    assert "as exc" in ast.unparse(handler), (
        f"_normalise_event: expected 'except ... as exc:', got "
        f"{ast.unparse(handler).splitlines()[0]}"
    )
    assert _handler_calls_logger_debug(handler, "recorder_record_resampler"), (
        "_normalise_event: expected a logger.debug call in the (TypeError, ValueError) handler"
    )


def test_normalise_event_bad_t_still_drops_event() -> None:
    """Control flow preserved: a non-numeric t must still produce None."""
    mod = _load_module()
    with_caplog_debug(mod)
    out = mod._normalise_event({"t": "not_a_number", "type": "key_down"})
    assert out is None
    out2 = mod._normalise_event({"t": 0.5, "type": "key_down"})
    assert out2 is not None and out2["t"] == 0.5


def test_apply_event_hotbar_bad_slot_binds_exception_and_logs() -> None:
    """_apply_event (hotbar branch) must bind exc and call logger.debug."""
    src = BIN_PATH.read_text()
    tree = ast.parse(src)
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_apply_event"
    )
    handler = _find_handler_for_exceptions(fn, ("TypeError", "ValueError"))
    assert handler is not None, (
        "_apply_event: no handler for (TypeError, ValueError) found"
    )
    assert "as exc" in ast.unparse(handler), (
        f"_apply_event: expected 'except ... as exc:', got "
        f"{ast.unparse(handler).splitlines()[0]}"
    )
    assert _handler_calls_logger_debug(handler, "recorder_record_resampler"), (
        "_apply_event: expected a logger.debug call in the (TypeError, ValueError) handler"
    )


def test_apply_event_hotbar_bad_slot_does_not_mutate_active_slot() -> None:
    """Control flow preserved: bad slot must leave active_slot untouched."""
    mod = _load_module()
    state = mod._ResamplerState()
    with_caplog_debug(mod)
    mod._apply_event(state, {"t": 0.5, "type": "hotbar", "slot": "bad"})
    assert state.active_slot == 0
    mod._apply_event(state, {"t": 0.6, "type": "hotbar", "slot": 4})
    assert state.active_slot == 4


def test_main_json_decode_binds_exception_and_logs() -> None:
    """_main stdin loop must bind exc and call logger.debug for json.JSONDecodeError."""
    src = BIN_PATH.read_text()
    tree = ast.parse(src)
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_main"
    )
    handler = _find_handler_for_exceptions(fn, ("json.JSONDecodeError",))
    assert handler is not None, (
        "_main: no handler for json.JSONDecodeError found"
    )
    assert "as exc" in ast.unparse(handler), (
        f"_main: expected 'except json.JSONDecodeError as exc:', got "
        f"{ast.unparse(handler).splitlines()[0]}"
    )
    assert _handler_calls_logger_debug(handler, "recorder_record_resampler"), (
        "_main: expected a logger.debug call in the json.JSONDecodeError handler"
    )


def test_no_bare_except_pass_anywhere() -> None:
    """AST scan: no handler should have a body of bare ``pass``."""
    src = BIN_PATH.read_text()
    tree = ast.parse(src)
    bad: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.body and len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                # Allowed: ImportError-style optional dependency checks that
                # already return None / fall through. We only flag the 3
                # sites that were fixed in this round.
                line = node.body[0].lineno
                if "as " not in ast.unparse(node):
                    bad.append(
                        f"line {line}: bare 'except ...: pass' without 'as exc' — {ast.unparse(node).splitlines()[0]}"
                    )
    assert not bad, "found bare except:pass:\n" + "\n".join(bad)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_handler_for_exceptions(
    fn: ast.FunctionDef, exc_names: tuple[str, ...]
) -> Optional[ast.ExceptHandler]:
    """Locate an except handler in *fn* matching any of *exc_names*."""
    for node in ast.walk(fn):
        if not isinstance(node, ast.ExceptHandler):
            continue
        unparsed = ast.unparse(node)
        if any(name in unparsed for name in exc_names):
            return node
    return None


def _handler_calls_logger_debug(handler: ast.ExceptHandler, needle: str) -> bool:
    """True iff *handler*'s body contains a ``logger.debug(...)`` call."""
    for sub in ast.walk(handler):
        if isinstance(sub, ast.Call):
            func = sub.func
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "logger"
                and func.attr == "debug"
            ):
                # Confirm the message string contains the module needle.
                if sub.args and isinstance(sub.args[0], ast.Constant):
                    if needle in str(sub.args[0].value):
                        return True
    return False


@pytest.fixture
def caplog_debug() -> None:
    """No-op fixture placeholder; we manually attach a handler in with_caplog_debug."""
    pass


def with_caplog_debug(mod: Any) -> None:
    """Attach a capture handler to the module logger for the duration of a test.

    Test must call this *before* the code under test. The handler is added
    at level DEBUG so the dropped-event paths emit visible records.
    """
    captured: list[logging.LogRecord] = []

    class _ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
            captured.append(record)

    cap = _ListHandler(level=logging.DEBUG)
    mod.logger.addHandler(cap)
    mod.logger.setLevel(logging.DEBUG)
    # Stash on the module so tests can introspect if needed.
    mod._test_captured = captured  # type: ignore[attr-defined]
