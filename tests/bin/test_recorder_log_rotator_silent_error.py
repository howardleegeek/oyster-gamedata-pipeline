#!/usr/bin/env python3
"""
Regression test: bin/recorder_log_rotator.py must surface silent errors via
logger.debug at swallow sites. The ``rotate()`` function had three bare
``except OSError: pass`` blocks (oldest.unlink, cascade os.replace, and
active os.replace) that swallowed filesystem errors without any logging.
Now each binds the exception and logs at DEBUG; control flow is preserved
(no `pass` after the log call where the next statement is end-of-handler,
and the final handler still returns False on failure).

This test verifies:
1. The module compiles without syntax errors.
2. The module defines a module-level `logger` (via logging.getLogger).
3. None of the four `except OSError` handlers in this module is a bare
   `except OSError: pass` (they must bind the exception OR log).
4. All three `except OSError` handlers inside `rotate()` bind the exception
   to a name (handler.name is not None).
5. All three `except OSError` handlers inside `rotate()` call logger.debug
   with the bound exception.
"""

import ast
from pathlib import Path

SRC_PATH = Path("bin/recorder_log_rotator.py")


def _load_source():
    src = SRC_PATH.read_text()
    ast.parse(src)  # raises SyntaxError on failure
    return src


def test_module_compiles():
    """bin/recorder_log_rotator.py must be syntactically valid Python."""
    _load_source()


def test_module_uses_logger():
    """The module must define a module-level logger."""
    src = _load_source()
    assert "logger = logging.getLogger" in src, (
        "module-level logger must be defined as `logger = logging.getLogger(...)`"
    )


def _collect_except_handlers(tree, func_name):
    """Return a list of ExceptHandler nodes inside the named function."""
    handlers = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            for child in ast.walk(node):
                if isinstance(child, ast.ExceptHandler):
                    handlers.append(child)
    return handlers


def test_no_bare_except_oserror_pass():
    """No `except OSError: pass` may remain in the module."""
    tree = ast.parse(_load_source())
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if node.type is None:
            continue
        type_str = ast.unparse(node.type)
        if "OSError" not in type_str:
            continue
        # Bare "except OSError: pass" → handler.name is None AND body is
        # exactly [Pass].
        if node.name is None and len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
            offenders.append(
                f"line {node.lineno}: bare 'except OSError: pass' is forbidden"
            )
    assert not offenders, "silent error swallow sites still present: " + "; ".join(
        offenders
    )


def _all_oserror_handlers_bind(tree, func_name):
    """Every except OSError handler in func_name must bind the exception."""
    handlers = _collect_except_handlers(tree, func_name)
    oserror_handlers = [h for h in handlers if h.type and "OSError" in ast.unparse(h.type)]
    assert oserror_handlers, f"no except OSError handlers found in {func_name}()"
    not_bound = [
        f"line {h.lineno}"
        for h in oserror_handlers
        if h.name is None
    ]
    assert not not_bound, (
        f"except OSError in {func_name}() must bind exception: " + ", ".join(not_bound)
    )


def _handler_calls_logger_debug(handler, exc_name):
    """Check that the handler's body contains a logger.debug call referencing exc_name."""
    for node in ast.walk(handler):
        if isinstance(node, ast.Call):
            func = node.func
            # Match logger.debug(...) form
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "debug"
                and isinstance(func.value, ast.Name)
                and func.value.id == "logger"
            ):
                # Check the call args reference exc_name somewhere
                for arg_node in ast.walk(node):
                    if isinstance(arg_node, ast.Name) and arg_node.id == exc_name:
                        return True
    return False


def _all_oserror_handlers_log(tree, func_name):
    """Every except OSError handler in func_name must call logger.debug with the bound exc."""
    handlers = _collect_except_handlers(tree, func_name)
    oserror_handlers = [h for h in handlers if h.type and "OSError" in ast.unparse(h.type)]
    missing_log = []
    for h in oserror_handlers:
        if h.name is None:
            continue  # already flagged by bind test
        if not _handler_calls_logger_debug(h, h.name):
            missing_log.append(
                f"line {h.lineno}: handler must call logger.debug with {h.name}"
            )
    assert not missing_log, (
        f"except OSError in {func_name}() must log at DEBUG: " + "; ".join(missing_log)
    )


def test_rotate_excepts_bind():
    """rotate()'s except OSError handlers must bind the exception."""
    tree = ast.parse(_load_source())
    _all_oserror_handlers_bind(tree, "rotate")


def test_rotate_excepts_log_debug():
    """rotate()'s except OSError handlers must call logger.debug."""
    tree = ast.parse(_load_source())
    _all_oserror_handlers_log(tree, "rotate")
