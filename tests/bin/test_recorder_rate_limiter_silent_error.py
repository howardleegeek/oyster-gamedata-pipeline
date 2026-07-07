"""Regression test: bin/recorder_rate_limiter.py must surface silent errors
via logger.debug at the swallow sites. Each except block must bind the
exception to a name and call logger.debug, not swallow the traceback with
a bare `except SomeError: pass`.

This test verifies:
1. The module compiles without syntax errors
2. logging is imported and a module-level logger is defined
3. The swallow sites each bind the exception AND call logger.debug
4. None of the target swallow sites is a bare `except ...: pass`
   (no bound name)

Round 365: Surface silent errors in bin/recorder_rate_limiter.py.
"""

import ast
from pathlib import Path

SRC_PATH = Path("bin/recorder_rate_limiter.py")


def _load_source():
    src = SRC_PATH.read_text()
    ast.parse(src)  # raises SyntaxError on failure
    return src


def test_module_compiles():
    """bin/recorder_rate_limiter.py must be syntactically valid Python."""
    _load_source()  # raises on syntax error


def test_logging_imported_and_logger_defined():
    """The module must import logging and define a module-level logger."""
    src = _load_source()
    assert "import logging" in src, "logging must be imported"
    assert "logger = logging.getLogger(__name__)" in src, (
        "module-level logger must be defined as "
        "`logger = logging.getLogger(__name__)`"
    )


def _find_except_in_func(tree, func_name):
    """Return list of (lineno, handler_node) for ExceptHandlers inside func."""
    handlers = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            for child in ast.walk(node):
                if isinstance(child, ast.ExceptHandler):
                    handlers.append((child.lineno, child))
    return handlers


def test_load_config_except_binds_and_logs():
    """load_config's except must bind exception and log at DEBUG."""
    src = _load_source()
    # Verify load_config has bound exception + logger.debug
    assert "except (json.JSONDecodeError, IOError) as exc:" in src
    assert "logger.debug" in src
    # Verify not bare pass
    tree = ast.parse(src)
    handlers = _find_except_in_func(tree, "load_config")
    assert handlers, "load_config must have exception handlers"
    for ln, h in handlers:
        if h.type is not None:
            assert h.name is not None, f"load_config line {ln}: except must bind exception"
            body_src = ast.unparse(h)
            assert "logger.debug" in body_src, f"load_config line {ln}: must call logger.debug"


def test_count_sessions_today_excepts_bind_and_log():
    """count_sessions_today's except blocks must bind exception and log at DEBUG."""
    tree = ast.parse(_load_source())
    handlers = _find_except_in_func(tree, "count_sessions_today")
    assert handlers, "count_sessions_today must have exception handlers"
    for ln, h in handlers:
        if h.type is not None:  # Skip bare except
            assert h.name is not None, f"count_sessions_today line {ln}: except must bind exception"
            body_src = ast.unparse(h)
            assert "logger.debug" in body_src, f"count_sessions_today line {ln}: must call logger.debug"


def test_sum_pending_uploads_gb_excepts_bind_and_log():
    """sum_pending_uploads_gb's except blocks must bind exception and log at DEBUG."""
    tree = ast.parse(_load_source())
    handlers = _find_except_in_func(tree, "sum_pending_uploads_gb")
    assert handlers, "sum_pending_uploads_gb must have exception handlers"
    for ln, h in handlers:
        if h.type is not None:  # Skip bare except
            assert h.name is not None, f"sum_pending_uploads_gb line {ln}: except must bind exception"
            body_src = ast.unparse(h)
            assert "logger.debug" in body_src, f"sum_pending_uploads_gb line {ln}: must call logger.debug"


def test_can_record_now_except_binds_and_logs():
    """can_record_now's disk_space except must bind exception and log at DEBUG."""
    tree = ast.parse(_load_source())
    handlers = _find_except_in_func(tree, "can_record_now")
    assert handlers, "can_record_now must have exception handlers"
    for ln, h in handlers:
        if h.type is not None:
            assert h.name is not None, f"can_record_now line {ln}: except must bind exception"
            body_src = ast.unparse(h)
            assert "logger.debug" in body_src, f"can_record_now line {ln}: must call logger.debug"


def test_reset_daily_counter_except_binds_and_logs():
    """reset_daily_counter's except must bind exception and log at DEBUG."""
    tree = ast.parse(_load_source())
    handlers = _find_except_in_func(tree, "reset_daily_counter")
    assert handlers, "reset_daily_counter must have exception handlers"
    for ln, h in handlers:
        if h.type is not None:
            assert h.name is not None, f"reset_daily_counter line {ln}: except must bind exception"
            body_src = ast.unparse(h)
            assert "logger.debug" in body_src, f"reset_daily_counter line {ln}: must call logger.debug"
