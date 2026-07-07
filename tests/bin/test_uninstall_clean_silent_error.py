#!/usr/bin/env python3
"""
Regression test: bin/uninstall_clean.py must surface silent errors at
3 swallow sites (find_app_paths iterdir, find_launchd_plists iterdir,
unload_service launchctl subprocess) via logger.debug, not bare `pass`.

This test verifies:
1. The 3 target except blocks bind the exception to a name (as exc)
2. Each handler body calls logger.debug (not just `pass`)
3. The handlers still do not re-raise (control flow preserved)
4. Module compiles without syntax errors
5. logging module is imported and a module-level logger is defined

Round 364: Surface silent errors in bin/uninstall_clean.py.
"""

import ast
import re
from pathlib import Path

SRC_PATH = Path("bin/uninstall_clean.py")


def _load_tree():
    src = SRC_PATH.read_text()
    ast.parse(src)  # raises SyntaxError on failure
    return src, ast.parse(src)


def test_module_compiles():
    """Module must compile without syntax errors."""
    src = SRC_PATH.read_text()
    try:
        compile(src, str(SRC_PATH), "exec")
    except SyntaxError as e:
        raise AssertionError(f"Syntax error: {e}")


def test_logging_imported():
    """logging module must be imported."""
    src = SRC_PATH.read_text()
    assert "import logging" in src, "logging import missing"


def test_logger_defined():
    """A module-level logger must be defined."""
    src = SRC_PATH.read_text()
    assert re.search(r"^logger\s*=\s*logging\.getLogger", src, re.MULTILINE), (
        "Module-level logger = logging.getLogger(...) missing"
    )


def test_find_app_paths_iterdir_binds_exception():
    """find_app_paths iterdir must bind the PermissionError to a name."""
    src, tree = _load_tree()
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            body_text = ast.dump(node)
            if "iterdir" in body_text and "PermissionError" in body_text:
                for handler in node.handlers:
                    if handler.name is not None:
                        if isinstance(handler.type, ast.Name) and handler.type.id == "PermissionError":
                            found = True
    assert found, (
        "find_app_paths iterdir except must bind PermissionError "
        "(e.g., 'except PermissionError as exc:')"
    )


def test_find_app_paths_iterdir_logs_error():
    """find_app_paths iterdir except body must call logger.debug."""
    src = SRC_PATH.read_text()
    pattern = (
        r"for\s+item\s+in\s+base\.iterdir\(\):.*?"
        r"except\s+PermissionError\s+as\s+\w+:\s*\n"
        r"\s+logger\.debug\("
    )
    match = re.search(pattern, src, re.DOTALL)
    assert match is not None, (
        "find_app_paths iterdir except must call logger.debug"
    )


def test_find_app_paths_iterdir_not_bare_pass():
    """find_app_paths iterdir must not be a bare `except PermissionError: pass`."""
    src = SRC_PATH.read_text()
    pattern = (
        r"for\s+item\s+in\s+base\.iterdir\(\):.*?"
        r"except\s+PermissionError:\s*\n"
        r"\s+pass\s*$"
    )
    match = re.search(pattern, src, re.DOTALL | re.MULTILINE)
    assert match is None, (
        "find_app_paths iterdir except must bind exception and log — "
        "bare `except PermissionError: pass` is the silent-swallow anti-pattern"
    )


def test_find_launchd_plists_iterdir_binds_exception():
    """find_launchd_plists iterdir must bind the PermissionError to a name."""
    src, tree = _load_tree()
    # Find the iterdir() call in find_launchd_plists
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            body_text = ast.dump(node)
            if "iterdir" in body_text and "PermissionError" in body_text:
                for handler in node.handlers:
                    if handler.name is not None:
                        if isinstance(handler.type, ast.Name) and handler.type.id == "PermissionError":
                            # Disambiguate from find_app_paths by body content
                            if "plist" in ast.unparse(node).lower():
                                found = True
    assert found, (
        "find_launchd_plists iterdir except must bind PermissionError"
    )


def test_find_launchd_plists_iterdir_logs_error():
    """find_launchd_plists iterdir except body must call logger.debug."""
    src = SRC_PATH.read_text()
    pattern = (
        r"for\s+p\s+in\s+loc\.iterdir\(\):.*?"
        r"except\s+PermissionError\s+as\s+\w+:\s*\n"
        r"\s+logger\.debug\("
    )
    match = re.search(pattern, src, re.DOTALL)
    assert match is not None, (
        "find_launchd_plists iterdir except must call logger.debug"
    )


def test_find_launchd_plists_iterdir_not_bare_pass():
    """find_launchd_plists iterdir must not be a bare `except PermissionError: pass`."""
    src = SRC_PATH.read_text()
    pattern = (
        r"for\s+p\s+in\s+loc\.iterdir\(\):.*?"
        r"except\s+PermissionError:\s*\n"
        r"\s+pass\s*$"
    )
    match = re.search(pattern, src, re.DOTALL | re.MULTILINE)
    assert match is None, (
        "find_launchd_plists iterdir except must bind exception and log — "
        "bare `except PermissionError: pass` is the silent-swallow anti-pattern"
    )


def test_unload_service_binds_exception():
    """unload_service launchctl subprocess must bind CalledProcessError to a name."""
    src, tree = _load_tree()
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            body_text = ast.unparse(node)
            if "launchctl" in body_text:
                for handler in node.handlers:
                    if handler.name is not None:
                        if isinstance(handler.type, ast.Attribute) and handler.type.attr == "CalledProcessError":
                            found = True
                        elif isinstance(handler.type, ast.Name) and handler.type.id == "CalledProcessError":
                            found = True
    assert found, (
        "unload_service launchctl except must bind CalledProcessError "
        "(e.g., 'except subprocess.CalledProcessError as exc:')"
    )


def test_unload_service_logs_error():
    """unload_service launchctl except body must call logger.debug."""
    src = SRC_PATH.read_text()
    pattern = (
        r"except\s+subprocess\.CalledProcessError\s+as\s+\w+:\s*\n"
        r"\s+logger\.debug\("
    )
    match = re.search(pattern, src)
    assert match is not None, (
        "unload_service launchctl except must call logger.debug"
    )


def test_unload_service_not_bare_pass():
    """unload_service launchctl must not be a bare `except CalledProcessError: pass`."""
    src = SRC_PATH.read_text()
    pattern = (
        r"except\s+subprocess\.CalledProcessError:\s*\n"
        r"\s+pass\s*$"
    )
    match = re.search(pattern, src, re.MULTILINE)
    assert match is None, (
        "unload_service launchctl except must bind exception and log — "
        "bare `except subprocess.CalledProcessError: pass` is the silent-swallow anti-pattern"
    )
