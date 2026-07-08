#!/usr/bin/env python3
"""
Regression tests for silent error swallows in bin/recover_lite_session.py.

Three sites used to silently swallow ``ValueError`` with a bare
``pass``:

1. ``_parse_recorded_at`` — loop over candidate strptime formats.
2. ``_session_dir_time_utc`` — strptime of regex-extracted 8+6 digits.
3. ``_video_info`` — float parse of ffprobe ``avg_frame_rate`` ``"num/den"``.

All three must now bind the exception to a name and emit a
``logger.debug`` call naming the failure context. The control flow
(``return None`` on first fail, fall through to next format on second,
fall through to ``DEFAULT_FPS`` on third) must remain unchanged.

This test verifies:
1. The module compiles without syntax errors.
2. ``logging`` is imported and a module-level ``logger`` is defined.
3. Each of the three ``except ValueError`` handlers binds the exception
   (``handler.name is not None``) AND calls ``logger.debug``.
4. No bare ``except ...: pass`` swallow remains in the module.
5. Runtime: a malformed ``avg_frame_rate`` string still yields the
   DEFAULT_FPS (control-flow preserved) AND emits a debug log naming
   the failure.

Round 374: Surface silent errors in bin/recover_lite_session.py
_parse_recorded_at / _session_dir_time_utc / _video_info.
"""

import ast
import importlib
import logging
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = REPO_ROOT / "bin" / "recover_lite_session.py"


def _load_source() -> str:
    src = SRC_PATH.read_text(encoding="utf-8")
    ast.parse(src)  # raises SyntaxError on failure
    return src


def test_module_compiles():
    """bin/recover_lite_session.py must be syntactically valid Python."""
    _load_source()


def test_logging_imported_and_logger_defined():
    """The module must import logging and define a module-level logger."""
    src = _load_source()
    assert "import logging" in src, "logging must be imported"
    assert "logger = logging.getLogger(__name__)" in src, (
        "module-level logger must be defined as "
        "`logger = logging.getLogger(__name__)`"
    )


def _except_handlers_in_func(tree, func_name):
    """Return list of (lineno, handler_node) for ExceptHandlers inside func."""
    handlers = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            for child in ast.walk(node):
                if isinstance(child, ast.ExceptHandler):
                    handlers.append((child.lineno, child))
    return handlers


def _first_bare_valueerror_handler(tree, func_name):
    """Return the first bare ``except ValueError`` handler in func, or None."""
    for _, h in _except_handlers_in_func(tree, func_name):
        # Match a ValueError handler with a single variable type
        if h.type is not None and h.name is not None:
            type_src = ast.unparse(h.type)
            if type_src == "ValueError":
                return h
    return None


def test_parse_recorded_at_handler_binds_and_logs():
    """The ``except ValueError`` in ``_parse_recorded_at`` must bind the
    exception and call ``logger.debug``, not bare ``pass``."""
    tree = ast.parse(_load_source())
    h = _first_bare_valueerror_handler(tree, "_parse_recorded_at")
    assert h is not None, (
        "no `except ValueError as <name>` handler found in _parse_recorded_at"
    )
    body_src = ast.unparse(h)
    assert "logger.debug" in body_src, (
        "_parse_recorded_at ValueError handler must call logger.debug"
    )
    assert not (len(h.body) == 1 and isinstance(h.body[0], ast.Pass)), (
        "_parse_recorded_at ValueError handler must not be a bare `pass`"
    )


def test_session_dir_time_utc_handler_binds_and_logs():
    """The ``except ValueError`` in ``_session_dir_time_utc`` must bind
    the exception and call ``logger.debug``, not bare ``pass``."""
    tree = ast.parse(_load_source())
    h = _first_bare_valueerror_handler(tree, "_session_dir_time_utc")
    assert h is not None, (
        "no `except ValueError as <name>` handler found in _session_dir_time_utc"
    )
    body_src = ast.unparse(h)
    assert "logger.debug" in body_src, (
        "_session_dir_time_utc ValueError handler must call logger.debug"
    )
    assert not (len(h.body) == 1 and isinstance(h.body[0], ast.Pass)), (
        "_session_dir_time_utc ValueError handler must not be a bare `pass`"
    )


def test_video_info_handler_binds_and_logs():
    """The ``except ValueError`` in ``_video_info`` (the avg_frame_rate
    parse block) must bind the exception and call ``logger.debug``,
    not bare ``pass``."""
    tree = ast.parse(_load_source())
    h = _first_bare_valueerror_handler(tree, "_video_info")
    assert h is not None, (
        "no `except ValueError as <name>` handler found in _video_info"
    )
    body_src = ast.unparse(h)
    assert "logger.debug" in body_src, (
        "_video_info ValueError handler must call logger.debug"
    )
    assert not (len(h.body) == 1 and isinstance(h.body[0], ast.Pass)), (
        "_video_info ValueError handler must not be a bare `pass`"
    )


def test_no_bare_except_pass_in_module():
    """No ``except ...: pass`` swallow may remain anywhere in the module."""
    tree = ast.parse(_load_source())
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            body_stmts = node.body
            if len(body_stmts) == 1 and isinstance(body_stmts[0], ast.Pass):
                pytest.fail(
                    f"Bare `except ...: pass` found at line {node.lineno}: "
                    f"{ast.unparse(node)}"
                )


def _import_module():
    """Import bin/recover_lite_session.py from the repo root.

    The module top-level reads ``BIN = Path(__file__).resolve().parent`` and
    defines a handful of module-level constants, but it does NOT open files
    or run network calls at import time, so a plain import is safe.
    """
    sys.path.insert(0, str(REPO_ROOT / "bin"))
    try:
        return importlib.import_module("recover_lite_session")
    finally:
        if "recover_lite_session" in sys.modules:
            pass


def test_runtime_logger_emits_debug_on_bad_rate(caplog):
    """A bad avg_frame_rate string ('abc/def') must hit the
    ``_video_info`` ValueError handler, emit a logger.debug message
    naming the parse failure, and fall through to DEFAULT_FPS (30.0).

    We invoke ``_video_info`` against a fake vstream dict that has a
    string ``avg_frame_rate`` which cannot be parsed as two floats.
    The function should return fps == DEFAULT_FPS and a debug record
    containing 'avg_frame_rate' should be emitted.
    """
    mod = _import_module()
    # Must include codec_type='video' so next() finds this stream
    fake_vstream = {"codec_type": "video", "avg_frame_rate": "abc/def"}
    fake_meta = {
        "streams": [fake_vstream],
        "format": {"duration": 10.0},
    }

    # Patch _ffprobe_json to return our fake metadata.
    mod._ffprobe_json = lambda _path: fake_meta  # type: ignore[assignment]

    caplog.set_level(logging.DEBUG, logger="recover_lite_session")
    result = mod._video_info(Path("/tmp/does-not-exist"))
    fps = result["fps"]
    assert fps == mod.DEFAULT_FPS, (
        f"bad avg_frame_rate should fall through to DEFAULT_FPS, got {fps!r}"
    )
    debug_records = [
        r for r in caplog.records
        if r.name == "recover_lite_session" and "avg_frame_rate" in r.getMessage()
    ]
    assert debug_records, (
        "Expected a logger.debug message mentioning 'avg_frame_rate' "
        "when the rate string is unparseable; got records: "
        f"{[(r.name, r.getMessage()) for r in caplog.records]}"
    )
