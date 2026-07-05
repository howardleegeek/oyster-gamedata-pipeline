#!/usr/bin/env python3
"""Regression tests: buyer_spec_adapter.py should not silently swallow exceptions."""
import ast
import sys
from pathlib import Path

MODULE = "src/oyster_agent_runner/buyer_spec_adapter.py"


# Test 1: No bare except blocks (no except Exception: without 'as NAME')
def test_no_bare_except():
    """Module must not contain bare 'except Exception:' without a name binding."""
    src = Path(MODULE).read_text()
    tree = ast.parse(src)

    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                raise AssertionError(f"Bare except at line {node.lineno}: {ast.unparse(node)}")
            if (
                isinstance(node.type, ast.Name)
                and node.type.id == "Exception"
                and node.name is None
            ):
                raise AssertionError(
                    f"Boundless 'except Exception:' at line {node.lineno}: {ast.unparse(node)}"
                )


# Test 2: logger is imported and bound at module level
def test_logger_imported():
    """Module must bind a module-level logger for debug logging."""
    src = Path(MODULE).read_text()
    tree = ast.parse(src)

    has_logger = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "logger":
                    has_logger = True
                    break
    assert has_logger, "logger must be imported and bound at module level"


# Test 3: C8 import fallback must log on failure
def test_c8_import_fallback_logs_debug():
    """C8 import fallback must log a debug message so missing dep is observable."""
    src = Path(MODULE).read_text()
    # The 'as _c8_import_err' binding proves the except is no longer silent.
    assert "except Exception as _c8_import_err:" in src, (
        "C8 import try/except must bind the exception"
    )
    # And the body must actually emit a log record.
    # Find the ExceptHandler and inspect its body for a logger.debug call.
    tree = ast.parse(src)
    found_log = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.name == "_c8_import_err":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    func = sub.func
                    if (
                        isinstance(func, ast.Attribute)
                        and isinstance(func.value, ast.Name)
                        and func.value.id == "logger"
                        and func.attr == "debug"
                    ):
                        found_log = True
                        break
    assert found_log, "C8 import except body must call logger.debug"


# Test 4: module compiles cleanly
def test_module_compiles():
    """Module must compile without errors."""
    import py_compile

    py_compile.compile(MODULE, doraise=True)


# Test 5: import still works at runtime
def test_module_imports():
    """The module must still import without raising."""
    sys.path.insert(0, "src")
    try:
        import oyster_agent_runner.buyer_spec_adapter  # noqa: F401
    finally:
        sys.path.pop(0)
