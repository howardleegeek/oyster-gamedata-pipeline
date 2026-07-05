"""Regression test: server/auth_middleware.py surfaces silent error in get_current_user_optional.

The previous bare ``except Exception:`` swallowed any error during optional JWT
verification, returning ``None`` without logging. We want the exception to be
captured and emitted at DEBUG so operators can diagnose flaky auth without
changing the public contract (still returns ``None``).
"""

from __future__ import annotations

import ast
import importlib.util
import logging
import pathlib

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
AUTH_MIDDLEWARE_PATH = REPO_ROOT / "server" / "auth_middleware.py"


def _load_module():
    """Load server.auth_middleware by file path to avoid package init side effects."""
    spec = importlib.util.spec_from_file_location(
        "auth_middleware_under_test", AUTH_MIDDLEWARE_PATH
    )
    assert spec is not None and spec.loader is not None, "spec load failed"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_module_compiles():
    """The file should parse and compile cleanly."""
    src = AUTH_MIDDLEWARE_PATH.read_text(encoding="utf-8")
    ast.parse(src)
    compile(src, str(AUTH_MIDDLEWARE_PATH), "exec")


def test_logger_imported():
    """The module must import logging and define a module-level logger."""
    module = _load_module()
    assert hasattr(module, "logger"), "auth_middleware must define a module-level logger"
    assert isinstance(module.logger, logging.Logger)


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


def test_get_current_user_optional_logs_at_debug(monkeypatch):
    """A failure in verify_jwt_token must trigger a logger.debug call."""
    import asyncio
    module = _load_module()

    captured: list[tuple[str, str]] = []
    handler = logging.Handler()
    handler.emit = lambda record: captured.append((record.levelname, record.getMessage()))
    module.logger.addHandler(handler)
    module.logger.setLevel(logging.DEBUG)
    try:
        def _raise(_token):
            raise ValueError("simulated JWT decode failure")

        monkeypatch.setattr(module, "verify_jwt_token", _raise)

        # Build a fake request-like object.
        class _Req:
            headers = {"Authorization": "Bearer abc.def.ghi"}

        result = asyncio.run(module.get_current_user_optional(_Req()))
    finally:
        module.logger.removeHandler(handler)

    assert result is None
    assert any(level == "DEBUG" for level, _ in captured), (
        f"expected a DEBUG log, got {captured!r}"
    )


def test_get_current_user_optional_returns_user_on_success(monkeypatch):
    """When verify_jwt_token returns a payload, the function must propagate it."""
    import asyncio
    module = _load_module()

    payload = {"sub": "user-1", "role": "buyer"}
    monkeypatch.setattr(module, "verify_jwt_token", lambda _t: payload)

    class _Req:
        headers = {"Authorization": "Bearer abc.def.ghi"}

    result = asyncio.run(module.get_current_user_optional(_Req()))
    assert result == payload


def test_get_current_user_optional_returns_none_without_header(monkeypatch):
    """When no Authorization header is present, the function returns None immediately."""
    import asyncio
    module = _load_module()

    called = {"n": 0}
    def _should_not_call(_token):
        called["n"] += 1
        return {"sub": "x"}

    monkeypatch.setattr(module, "verify_jwt_token", _should_not_call)

    class _Req:
        headers = {}

    result = asyncio.run(module.get_current_user_optional(_Req()))
    assert result is None
    assert called["n"] == 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
