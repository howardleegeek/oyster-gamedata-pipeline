"""Regression test: bin/end_to_end_gate_smoke.py must surface silent errors
via logger.debug at the JSON parse swallow site in parse_gate_result().
The except block must bind the exception to a name and call logger.debug,
not swallow the traceback with a bare `except SomeError: pass`.

This test verifies:
1. The module compiles without syntax errors
2. logging is imported and a module-level logger is defined
3. The target swallow site binds the exception AND calls logger.debug
4. None of the target swallow sites is a bare `except ...: pass`
   (no bound name)

Round 362: Surface silent errors in bin/end_to_end_gate_smoke.py.
"""

import ast
from pathlib import Path

SRC_PATH = Path("bin/end_to_end_gate_smoke.py")


def _load_source():
    src = SRC_PATH.read_text()
    ast.parse(src)  # raises SyntaxError on failure
    return src


def test_module_compiles():
    """bin/end_to_end_gate_smoke.py must be syntactically valid Python."""
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


def test_run_gate_json_except_binds_and_logs():
    """_run_gate's JSON except must bind exception and log at DEBUG."""
    tree = ast.parse(_load_source())
    handlers = _find_except_in_func(tree, "_run_gate")
    # Find all excepts that catch (json.JSONDecodeError, ValueError)
    targets = [h for ln, h in handlers
               if h.type is not None
               and "JSONDecodeError" in ast.unparse(h.type)
               and "ValueError" in ast.unparse(h.type)]
    assert targets, "_run_gate's except (JSONDecodeError, ValueError) not found"
    # Both JSON except sites must bind and log
    for i, h in enumerate(targets):
        assert h.name is not None, f"JSON except #{i+1} must bind exception to a name"
        body_src = ast.unparse(h)
        assert "logger.debug" in body_src, (
            f"JSON except #{i+1} must call logger.debug, not bare `pass`"
        )
