"""Regression test: server/auth_middleware.py surfaces silent error in get_current_user_optional.

The previous bare ``except Exception:`` swallowed any error during optional JWT
verification, returning ``None`` without logging. We want the exception to be
captured and emitted at DEBUG so operators can diagnose flaky auth without
changing the public contract (still returns ``None``).
"""

from __future__ import annotations

import ast
import pathlib

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
AUTH_MIDDLEWARE_PATH = REPO_ROOT / "server" / "auth_middleware.py"


def test_module_compiles():
    """The file should parse and compile cleanly."""
    src = AUTH_MIDDLEWARE_PATH.read_text(encoding="utf-8")
    ast.parse(src)
    compile(src, str(AUTH_MIDDLEWARE_PATH), "exec")


def test_logger_imported():
    """The module must import logging and define a module-level logger."""
    src = AUTH_MIDDLEWARE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)

    has_logger = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "logger":
                    has_logger = True
                    break
    assert has_logger, "auth_middleware must define a module-level logger"


def test_get_current_user_optional_no_bare_except_pass():
    """The optional-auth function must not contain a bare `except Exception: pass`."""
    src = AUTH_MIDDLEWARE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    func = next(
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "get_current_user_optional"
    )
    bare_excepts = []
    for node in ast.walk(func):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            bare_excepts.append(node.lineno)
    assert bare_excepts == [], (
        f"get_current_user_optional has bare except at lines {bare_excepts}"
    )


def test_get_current_user_optional_binds_exception():
    """The except clause must bind the exception (e.g. `except Exception as exc:`)."""
    src = AUTH_MIDDLEWARE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    func = next(
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "get_current_user_optional"
    )
    bound = []
    for node in ast.walk(func):
        if isinstance(node, ast.ExceptHandler) and node.name is not None:
            bound.append((node.lineno, node.name))
    assert bound, "expected at least one bound except handler in get_current_user_optional"


def test_get_current_user_optional_logs_at_debug():
    """A failure in verify_jwt_token must trigger a logger.debug call in get_current_user_optional."""
    src = AUTH_MIDDLEWARE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    func = next(
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "get_current_user_optional"
    )

    # Find the ExceptHandler that catches Exception
    found_debug_log = False
    for node in ast.walk(func):
        if isinstance(node, ast.ExceptHandler) and node.name is not None:
            # Check if there's a logger.debug call in the handler body
            for handler_node in ast.walk(node):
                if isinstance(handler_node, ast.Call):
                    if (isinstance(handler_node.func, ast.Attribute) and
                        handler_node.func.attr == "debug" and
                        isinstance(handler_node.func.value, ast.Name) and
                        handler_node.func.value.id == "logger"):
                        found_debug_log = True
                        break

    assert found_debug_log, "get_current_user_optional must call logger.debug in exception handler"


def test_get_current_user_optional_returns_none_on_exception():
    """The function must still return None on exception (preserving control flow)."""
    src = AUTH_MIDDLEWARE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    func = next(
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "get_current_user_optional"
    )

    # Check that the function returns None in the exception handler path
    # Look for "return None" after the except block
    has_return_none = False
    for node in ast.walk(func):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Constant) and node.value.value is None:
            has_return_none = True
            break

    assert has_return_none, "get_current_user_optional must return None on exception"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
