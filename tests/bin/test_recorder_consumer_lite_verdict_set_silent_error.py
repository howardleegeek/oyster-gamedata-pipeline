#!/usr/bin/env python3
"""
Regression test: recorder_consumer_lite.RecorderApp._set() (the thread-safe
verdict/subtitle updater) should surface the RuntimeError raised when
Tkinter is closed mid-shutdown, not swallow it silently.

This test verifies:
1. _set() method exists in the module
2. The inner apply() / self.after(0, apply) is wrapped in a try/except
3. The except handler binds the exception (i.e., `as exc`) — no bare
   `except RuntimeError: pass` pattern.
4. The except handler logs at DEBUG level with context.
5. The bare `except RuntimeError: pass` anti-pattern is not present in _set.
6. Module still compiles without syntax errors.

Round 382: Surface silent error in _set() UI update bare RuntimeError handler.
"""

import ast
import py_compile
from pathlib import Path


def test_module_compiles():
    """Verify module still compiles after the edit."""
    py_compile.compile("bin/recorder_consumer_lite.py", doraise=True)


def test_set_method_exists():
    """Verify _set method exists in the module."""
    src = Path("bin/recorder_consumer_lite.py").read_text()
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_set":
            found = True
            break
    assert found, "_set method must exist on RecorderApp"


def test_set_binds_runtime_error():
    """Verify the except RuntimeError handler in _set binds the exception."""
    src = Path("bin/recorder_consumer_lite.py").read_text()
    tree = ast.parse(src)
    set_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_set":
            set_node = node
            break
    assert set_node is not None

    # Find the ExceptHandler that catches RuntimeError
    runtime_handlers = []
    for node in ast.walk(set_node):
        if isinstance(node, ast.ExceptHandler):
            if node.type is not None and isinstance(node.type, ast.Name):
                if node.type.id == "RuntimeError":
                    runtime_handlers.append(node)
    assert len(runtime_handlers) == 1, (
        f"_set should have exactly one RuntimeError handler, found {len(runtime_handlers)}"
    )
    handler = runtime_handlers[0]
    assert handler.name is not None, (
        "Bare except RuntimeError: must bind the exception (e.g., 'except RuntimeError as exc:')"
    )


def test_set_runtime_error_logs_at_debug():
    """Verify the except RuntimeError handler logs at DEBUG level with context."""
    src = Path("bin/recorder_consumer_lite.py").read_text()
    tree = ast.parse(src)
    set_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_set":
            set_node = node
            break
    assert set_node is not None

    handler = None
    for node in ast.walk(set_node):
        if isinstance(node, ast.ExceptHandler):
            if node.type is not None and isinstance(node.type, ast.Name):
                if node.type.id == "RuntimeError":
                    handler = node
                    break
    assert handler is not None

    # The handler body must contain a logger.debug call.
    # The handler's body is a list of statements. We expect something like:
    #     logger.debug("...: %s", exc)
    has_debug_log = False
    for stmt in handler.body:
        # Check for ast.Expr wrapping a Call like logger.debug(...)
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            call = stmt.value
            if isinstance(call.func, ast.Attribute):
                if call.func.attr == "debug":
                    # Verify the receiver is 'logger' (or has a debug attr)
                    if isinstance(call.func.value, ast.Name):
                        has_debug_log = True
                        break
    assert has_debug_log, (
        "RuntimeError handler in _set must call logger.debug(...) with context"
    )


def test_set_no_bare_runtime_error_pass():
    """AST scan: no `except RuntimeError: pass` in _set."""
    src = Path("bin/recorder_consumer_lite.py").read_text()
    tree = ast.parse(src)
    set_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_set":
            set_node = node
            break
    assert set_node is not None

    for node in ast.walk(set_node):
        if isinstance(node, ast.ExceptHandler):
            if node.type is not None and isinstance(node.type, ast.Name):
                if node.type.id == "RuntimeError":
                    # Must NOT be bare (must bind to a name)
                    if node.name is None:
                        raise AssertionError(
                            "Bare except RuntimeError: must bind exception "
                            "(no bare `except RuntimeError: pass` allowed in _set)"
                        )


def test_set_still_calls_after():
    """Verify _set still calls self.after(0, apply) — control flow preserved."""
    src = Path("bin/recorder_consumer_lite.py").read_text()
    tree = ast.parse(src)
    set_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_set":
            set_node = node
            break
    assert set_node is not None

    has_after_call = False
    for node in ast.walk(set_node):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                if node.func.attr == "after":
                    if isinstance(node.func.value, ast.Name) and node.func.value.id == "self":
                        has_after_call = True
                        break
    assert has_after_call, "_set must still call self.after(0, apply)"
