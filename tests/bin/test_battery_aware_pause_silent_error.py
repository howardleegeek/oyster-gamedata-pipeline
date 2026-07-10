#!/usr/bin/env python3
"""
Regression test: bin/battery_aware_pause.py must surface silent errors via
logger.debug at the 5 swallow sites (psutil probe, pmset probe, sysfs
capacity read, sysfs listdir, config load). Each except block must bind
the exception to a name and call logger.debug, not swallow the traceback
with a bare `except SomeError: pass`.

This test verifies:
1. The module compiles without syntax errors
2. logging is imported and a module-level logger is defined
3. The psutil sensors_battery except binds the exception AND calls logger.debug
4. The _detect_macos pmset except binds the exception AND calls logger.debug
5. The _detect_linux capacity read except binds the exception AND calls logger.debug
6. The _detect_linux listdir except binds the exception AND calls logger.debug
7. The load_config json.load except binds the exception AND calls logger.debug
8. None of the swallow sites is a bare `except ...: pass` (no bound name)

Round 350: Surface silent errors in bin/battery_aware_pause.py.
"""

import ast
import re
from pathlib import Path

SRC_PATH = Path("bin/battery_aware_pause.py")


def _load_source():
    src = SRC_PATH.read_text()
    ast.parse(src)  # raises SyntaxError on failure
    return src


def test_module_compiles():
    """bin/battery_aware_pause.py must be syntactically valid Python."""
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


def test_psutil_except_binds_and_logs():
    """detect_power_source's psutil except must bind exception and log at DEBUG."""
    tree = ast.parse(_load_source())
    handlers = _find_except_in_func(tree, "detect_power_source")
    assert handlers, "detect_power_source has no except blocks"
    # Find the one referencing sensors_battery
    matching = [h for ln, h in handlers if "sensors_battery" in ast.unparse(h)]
    assert matching, "psutil sensors_battery except block not found"
    h = matching[0]
    assert h.name is not None, "psutil except must bind exception to a name"
    body_src = ast.unparse(h)
    assert "logger.debug" in body_src, (
        "psutil except must call logger.debug, not bare `pass`"
    )


def test_pmset_except_binds_and_logs():
    """_detect_macos's pmset except must bind exception and log at DEBUG."""
    src, tree = _load_source(), ast.parse(_load_source())
    handlers = _find_except_in_func(tree, "_detect_macos")
    assert handlers, "_detect_macos has no except blocks"
    ln, h = handlers[0]
    assert h.name is not None, "pmset except must bind exception to a name"
    body_src = ast.unparse(h)
    assert "logger.debug" in body_src, (
        "pmset except must call logger.debug, not bare `pass`"
    )


def test_capacity_read_except_binds_and_logs():
    """_detect_linux capacity read except must bind exception and log at DEBUG."""
    src, tree = _load_source(), ast.parse(_load_source())
    handlers = _find_except_in_func(tree, "_detect_linux")
    assert handlers, "_detect_linux has no except blocks"
    # Find the inner except (ValueError, OSError) around capacity read
    matching = [
        h for ln, h in handlers
        if h.type is not None
        and "OSError" in ast.unparse(h.type)
        and "ValueError" in ast.unparse(h.type)
    ]
    assert matching, "capacity read except block not found"
    h = matching[0]
    assert h.name is not None, "capacity except must bind exception to a name"
    body_src = ast.unparse(h)
    assert "logger.debug" in body_src, (
        "capacity except must call logger.debug, not bare `pass`"
    )


def test_listdir_except_binds_and_logs():
    """_detect_linux listdir except must bind exception and log at DEBUG."""
    src, tree = _load_source(), ast.parse(_load_source())
    handlers = _find_except_in_func(tree, "_detect_linux")
    assert handlers, "_detect_linux has no except blocks"
    # The outer except is bare OSError
    matching = [h for ln, h in handlers if h.type is not None and ast.unparse(h.type) == "OSError"]
    assert matching, "listdir except block (bare OSError) not found"
    h = matching[0]
    assert h.name is not None, "listdir except must bind exception to a name"
    body_src = ast.unparse(h)
    assert "logger.debug" in body_src, (
        "listdir except must call logger.debug, not bare `pass`"
    )


def test_load_config_except_binds_and_logs():
    """load_config's json.load except must bind exception and log at DEBUG."""
    src, tree = _load_source(), ast.parse(_load_source())
    handlers = _find_except_in_func(tree, "load_config")
    assert handlers, "load_config has no except blocks"
    ln, h = handlers[0]
    assert h.name is not None, "load_config except must bind exception to a name"
    body_src = ast.unparse(h)
    assert "logger.debug" in body_src, (
        "load_config except must call logger.debug, not bare `pass`"
    )


def test_no_bare_pass_after_silent_excepts():
    """None of the targeted excepts should be a bare `except ...: pass`."""
    src = _load_source()
    # The 5 swallow sites must NOT match the anti-pattern
    anti_patterns = [
        # psutil sensors_battery
        r"except\s+\(AttributeError,\s*OSError\):\s*\n\s*pass",
        # pmset
        r"except\s+\(subprocess\.TimeoutExpired,\s*OSError,\s*ValueError\):\s*\n\s*pass",
        # capacity read
        r"except\s+\(ValueError,\s*OSError\):\s*\n\s*pass",
        # listdir
        r"except\s+OSError:\s*\n\s*pass",
        # config load
        r"except\s+\(json\.JSONDecodeError,\s*OSError\):\s*\n\s*pass",
    ]
    for pat in anti_patterns:
        match = re.search(pat, src, re.MULTILINE)
        assert match is None, (
            f"Bare `except ...: pass` anti-pattern found, must bind and log: {pat}"
        )
