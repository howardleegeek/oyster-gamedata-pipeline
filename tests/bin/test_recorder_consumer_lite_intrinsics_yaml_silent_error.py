"""Regression test: _package_tarball() intrinsics.yaml YAML dump fallback should
surface the cause of the yaml failure via logger.debug() instead of silently
swallowing it.

This test verifies:
1. Module has a logger imported and defined (module-level)
2. The YAML dump try/except block in _package_tarball binds the exception
3. The except block logs at DEBUG level with context
4. The plain-text fallback path is still invoked on failure
5. Module compiles without syntax errors

Round 332: Surface silent error in _package_tarball() intrinsics.yaml YAML dump.
"""

import ast
from pathlib import Path


def _read_source() -> str:
    return (Path(__file__).parent.parent.parent / "bin" / "recorder_consumer_lite.py").read_text(
        encoding="utf-8"
    )


def _find_function(tree: ast.AST, name: str) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def test_module_has_logger():
    """Verify module imports logging and defines logger = logging.getLogger(__name__)."""
    src = _read_source()
    assert "import logging" in src, "Module must import logging"
    assert "logger = logging.getLogger(__name__)" in src, (
        "Module must define logger = logging.getLogger(__name__)"
    )


def test_intrinsics_yaml_excepts_bind_exception():
    """The YAML dump try/except inside _package_tarball must bind the exception."""
    src = _read_source()
    tree = ast.parse(src)
    func = _find_function(tree, "_package_tarball")
    assert func is not None, "_package_tarball function not found"

    # Find the yaml.safe_dump try block. The first try in _package_tarball
    # whose body contains "import yaml" is the YAML dump block.
    target_try: ast.Try | None = None
    for node in ast.walk(func):
        if isinstance(node, ast.Try):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Import):
                    for alias in sub.names:
                        if alias.name == "yaml":
                            target_try = node
                            break
                if target_try:
                    break
            if target_try:
                break
    assert target_try is not None, "Could not locate yaml.safe_dump try block"

    # Every except handler in this try must bind the exception (have a name).
    for handler in target_try.handlers:
        assert handler.name is not None, (
            f"Bare `except Exception:` at line {handler.lineno} — should bind to a name "
            f"so logger can surface the cause"
        )


def test_intrinsics_yaml_logs_at_debug_on_failure():
    """The YAML dump except block must call logger.debug with the bound exception."""
    src = _read_source()
    tree = ast.parse(src)
    func = _find_function(tree, "_package_tarball")
    assert func is not None, "_package_tarball function not found"

    target_try: ast.Try | None = None
    for node in ast.walk(func):
        if isinstance(node, ast.Try):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Import):
                    for alias in sub.names:
                        if alias.name == "yaml":
                            target_try = node
                            break
                if target_try:
                    break
            if target_try:
                break
    assert target_try is not None, "Could not locate yaml.safe_dump try block"

    # At least one except handler should call logger.debug with the bound exc.
    found_debug_with_exc = False
    for handler in target_try.handlers:
        for sub in ast.walk(handler):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                if sub.func.attr == "debug" and isinstance(sub.func.value, ast.Name):
                    if sub.func.value.id == "logger":
                        # Check args reference the bound exception name
                        if handler.name:
                            for arg in sub.args:
                                if isinstance(arg, ast.Name) and arg.id == handler.name:
                                    found_debug_with_exc = True
                                    break
                                if isinstance(arg, ast.JoinedStr):
                                    # f-string with the bound name
                                    for value in arg.values:
                                        if (
                                            isinstance(value, ast.FormattedValue)
                                            and isinstance(value.value, ast.Name)
                                            and value.value.id == handler.name
                                        ):
                                            found_debug_with_exc = True
                                            break
    assert found_debug_with_exc, (
        "YAML dump except handler should call logger.debug(...) and reference the bound exception"
    )


def test_intrinsics_yaml_keeps_fallback_path():
    """The plain-text fallback path must still be invoked on YAML failure."""
    src = _read_source()
    tree = ast.parse(src)
    func = _find_function(tree, "_package_tarball")
    assert func is not None, "_package_tarball function not found"

    target_try: ast.Try | None = None
    for node in ast.walk(func):
        if isinstance(node, ast.Try):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Import):
                    for alias in sub.names:
                        if alias.name == "yaml":
                            target_try = node
                            break
                if target_try:
                    break
            if target_try:
                break
    assert target_try is not None, "Could not locate yaml.safe_dump try block"

    # At least one except handler must still call _atomic_write_text (the fallback).
    found_fallback = False
    for handler in target_try.handlers:
        for sub in ast.walk(handler):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                if sub.func.id == "_atomic_write_text":
                    found_fallback = True
                    break
    assert found_fallback, "Plain-text _atomic_write_text fallback must remain in except handler"


def test_module_compiles():
    """Module should compile without syntax errors."""
    import py_compile

    py_compile.compile("bin/recorder_consumer_lite.py", doraise=True)
