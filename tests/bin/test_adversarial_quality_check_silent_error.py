#!/usr/bin/env python3
"""
Regression test: bin/adversarial_quality_check.py must surface silent errors
via logger at the JSON parse swallow sites. The except blocks must bind the
exception to a name and call logger, not swallow the traceback.

This test verifies:
1. The module compiles without syntax errors
2. logging is imported and a module-level logger is defined
3. Every json.JSONDecodeError except block binds the exception AND calls logger
4. No bare `except ...: pass` pattern exists in the module

Round 357: Surface silent errors in bin/adversarial_quality_check.py.
The module has two silent-error sites:
  - check_game_state: json.loads(line) inside try/except JSONDecodeError
  - check_action_camera: json.load(ac.open()) with JSONL fallback
"""

import ast
from pathlib import Path

SRC_PATH = Path("bin/adversarial_quality_check.py")


def _load_source():
    src = SRC_PATH.read_text()
    ast.parse(src)  # raises SyntaxError on failure
    return src


def test_module_compiles():
    """bin/adversarial_quality_check.py must be syntactically valid Python."""
    _load_source()  # raises on syntax error


def test_logging_imported_and_logger_defined():
    """The module must import logging and define a module-level logger."""
    src = _load_source()
    assert "import logging" in src, "logging must be imported"
    assert "logger = logging.getLogger(__name__)" in src, (
        "module-level logger must be defined as "
        "`logger = logging.getLogger(__name__)`"
    )


def _find_all_json_decode_except(tree):
    """Find every json.JSONDecodeError ExceptHandler in the tree."""
    handlers = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type:
                type_str = ast.unparse(node.type)
                if "JSONDecodeError" in type_str:
                    handlers.append(node)
    return handlers


def test_all_json_decode_handlers_bind_and_log():
    """Every json.JSONDecodeError except must bind exception and call logger."""
    src = _load_source()
    tree = ast.parse(src)
    handlers = _find_all_json_decode_except(tree)
    assert len(handlers) >= 1, (
        "expected at least one json.JSONDecodeError except handler"
    )
    for handler in handlers:
        assert handler.name is not None, (
            "json.JSONDecodeError except must bind exception to a name"
        )
        body_src = ast.unparse(handler)
        assert "logger." in body_src, (
            "json.JSONDecodeError except must call logger "
            "(debug/info/warning/error), not bare `pass`"
        )


def test_no_bare_except_pass():
    """Verify no bare 'except: pass' pattern exists in the module."""
    src = _load_source()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            # Bare except or except with just pass
            if node.type is None or (
                len(node.body) == 1 and isinstance(node.body[0], ast.Pass)
            ):
                raise AssertionError(
                    "Bare 'except:' or 'except ...: pass' pattern found"
                )
