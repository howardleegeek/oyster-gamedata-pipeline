#!/usr/bin/env python3
"""
Regression test: bin/diag_bundle_collector.py must surface silent errors via
logger.debug at the 4 swallow sites (read /proc/meminfo, run_cmd_safe subprocess
failure, copy log file, copy manifest file). Each except block must bind the
exception to a name and call logger.debug, not swallow the traceback with a
bare `except SomeError: pass`.

This test verifies:
1. The module compiles without syntax errors
2. logging is imported and a module-level logger is defined
3. The /proc/meminfo read except binds the exception AND calls logger.debug
4. The run_cmd_safe subprocess except binds the exception AND calls logger.debug
5. The shutil.copy2 log except binds the exception AND calls logger.debug
6. The shutil.copy2 manifest except binds the exception AND calls logger.debug
7. None of the 4 swallow sites is a bare `except ...: pass` (no bound name)

Round 354: Surface silent errors in bin/diag_bundle_collector.py.
"""

import ast
from pathlib import Path

SRC_PATH = Path("bin/diag_bundle_collector.py")


def _load_source():
    src = SRC_PATH.read_text()
    ast.parse(src)  # raises SyntaxError on failure
    return src


def test_module_compiles():
    """bin/diag_bundle_collector.py must be syntactically valid Python."""
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


def test_meminfo_except_binds_and_logs():
    """get_system_info's /proc/meminfo except must bind exception and log at DEBUG."""
    tree = ast.parse(_load_source())
    handlers = _find_except_in_func(tree, "get_system_info")
    assert handlers, "get_system_info has no except blocks"
    # Find the one referencing /proc/meminfo
    matching = [h for ln, h in handlers if "/proc/meminfo" in ast.unparse(h)]
    assert matching, "/proc/meminfo except block not found"
    h = matching[0]
    assert h.name is not None, "meminfo except must bind exception to a name"
    body_src = ast.unparse(h)
    assert "logger.debug" in body_src, (
        "meminfo except must call logger.debug, not bare `pass`"
    )


def test_run_cmd_safe_except_binds_and_logs():
    """run_cmd_safe's subprocess except must bind exception and log at DEBUG."""
    tree = ast.parse(_load_source())
    handlers = _find_except_in_func(tree, "run_cmd_safe")
    assert handlers, "run_cmd_safe has no except blocks"
    _ln, h = handlers[0]
    assert h.name is not None, "run_cmd_safe except must bind exception to a name"
    body_src = ast.unparse(h)
    assert "logger.debug" in body_src, (
        "run_cmd_safe except must call logger.debug, not bare `return None`"
    )


def test_collect_bundle_log_copy_except_binds_and_logs():
    """collect_bundle's log shutil.copy2 except must bind exception and log at DEBUG."""
    tree = ast.parse(_load_source())
    handlers = _find_except_in_func(tree, "collect_bundle")
    assert handlers, "collect_bundle has no except blocks"
    # Find the one referencing /logs_dir copy
    log_handlers = [h for ln, h in handlers if "lf, exc" in ast.unparse(h) or " lf," in ast.unparse(h) or "lf " in ast.unparse(h)]
    assert log_handlers, "log copy except block not found"
    h = log_handlers[0]
    assert h.name is not None, "log copy except must bind exception to a name"
    body_src = ast.unparse(h)
    assert "logger.debug" in body_src, (
        "log copy except must call logger.debug, not bare `pass`"
    )


def test_collect_bundle_manifest_copy_except_binds_and_logs():
    """collect_bundle's manifest shutil.copy2 except must bind exception and log at DEBUG."""
    tree = ast.parse(_load_source())
    handlers = _find_except_in_func(tree, "collect_bundle")
    assert handlers, "collect_bundle has no except blocks"
    # Find the one referencing /manifests copy
    # Find the one referencing manifests copy - look for 'mf' (manifest file) in the handler
    manifest_handlers = [h for ln, h in handlers if "mf" in ast.unparse(h)]
    assert manifest_handlers, "manifest copy except block not found"
    h = manifest_handlers[0]
    assert h.name is not None, "manifest copy except must bind exception to a name"
    body_src = ast.unparse(h)
    assert "logger.debug" in body_src, (
        "manifest copy except must call logger.debug, not bare `pass`"
    )


def test_no_bare_except_pass_silent_swallows():
    """None of the 4 swallow sites may be a bare `except X: pass` pattern."""
    src = _load_source()
    # 4 targeted lines must all bind 'as exc' (or another name) AND call logger.debug
    # Simple check: no `except ...:\n        pass` or `except ...:\n            pass` near logger.debug lines
    bad_swallows = []
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            # 4 swallow sites target OSError families. Skip the argparse error handler.
            if not node.name:
                bad_swallows.append((node.lineno, ast.unparse(node)))
                continue
            body_src = ast.unparse(node)
            # only check sites that previously had `pass` or `return None` as the body
            if "logger.debug" not in body_src and ("pass" in body_src or "return None" in body_src):
                bad_swallows.append((node.lineno, ast.unparse(node)))
    assert not bad_swallows, (
        "Found unbound-silent-swallows at: "
        + ", ".join(f"L{ln}: {s}" for ln, s in bad_swallows)
    )
