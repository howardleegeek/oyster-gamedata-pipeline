"""Regression test: bin/oauth_login_server.py must surface silent errors
via logger.debug at the bare `except (ValueError, KeyError): pass` swallow
site in OAuthLoginServer._exchange_code_for_token(). The except block must
bind the exception to a name and call logger.debug, not swallow the
traceback with a bare `except ...: pass`.

This test verifies:
1. The module compiles without syntax errors
2. logging is imported and a module-level logger is defined
3. The target swallow site binds the exception AND calls logger.debug
4. None of the target swallow sites is a bare `except ...: pass`
   (no bound name)

Round 363: Surface silent errors in bin/oauth_login_server.py.
"""

import ast
from pathlib import Path

SRC_PATH = Path("bin/oauth_login_server.py")


def _load_source():
    src = SRC_PATH.read_text()
    ast.parse(src)  # raises SyntaxError on failure
    return src


def test_module_compiles():
    """bin/oauth_login_server.py must be syntactically valid Python."""
    _load_source()  # raises on syntax error


def test_logging_imported_and_logger_defined():
    """The module must import logging and define a module-level logger."""
    src = _load_source()
    assert "import logging" in src, "logging must be imported"
    assert "logger = logging.getLogger(__name__)" in src, (
        "module-level logger must be defined as `logger = logging.getLogger(__name__)`"
    )


def _find_except_in_method(tree, class_name, method_name):
    """Return list of (lineno, handler_node) for ExceptHandlers inside
    ClassName.method_name(). Handles both sync (FunctionDef) and async
    (AsyncFunctionDef) methods."""
    handlers = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for child in node.body:
                if (
                    isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and child.name == method_name
                ):
                    for sub in ast.walk(child):
                        if isinstance(sub, ast.ExceptHandler):
                            handlers.append((sub.lineno, sub))
    return handlers


def test_exchange_code_error_json_except_binds_and_logs():
    """_exchange_code_for_token's (ValueError, KeyError) except must bind
    exception and log at DEBUG — not be a bare `pass`."""
    tree = ast.parse(_load_source())
    handlers = _find_except_in_method(tree, "OAuthLoginServer", "_exchange_code_for_token")
    targets = [
        h
        for ln, h in handlers
        if h.type is not None
        and "ValueError" in ast.unparse(h.type)
        and "KeyError" in ast.unparse(h.type)
    ]
    assert targets, "_exchange_code_for_token's except (ValueError, KeyError) not found"
    h = targets[0]
    assert h.name is not None, (
        "except (ValueError, KeyError) must bind exception to a name, "
        "not be a bare `except ...: pass`"
    )
    body_src = ast.unparse(h)
    assert "logger.debug" in body_src, (
        "except (ValueError, KeyError) must call logger.debug, "
        "not silently swallow with bare `pass`"
    )
    # Ensure no bare `pass` is the only statement
    assert "pass" not in body_src, "except body must not contain a bare `pass` statement"
