#!/usr/bin/env python3
"""
Regression test: bin/paper_health_check.py must surface silent errors via
logger.exception at the check_server() except handler.

The original handler did:

    except Exception as e:
        print(f"Error: {e}")
        return 1

That swallows the traceback and writes to stdout (not stderr), so the
underlying connection/protocol failure is invisible to log aggregation
and any human looking at the CLI gets a one-line "Error: ..." with no
diagnostics.

After the fix the handler must:
  1. Call ``logger.exception(...)`` (or ``logger.error(..., exc_info=True)``)
     so the traceback is preserved in the log channel.
  2. Still print a human-readable error line and return 1 (control flow
     preserved — the CLI behaviour is unchanged).

This test verifies:
  1. The module compiles without syntax errors.
  2. ``logging`` is imported and a module-level ``logger`` is defined.
  3. The ``check_server`` except handler calls ``logger.exception`` (or
     ``logger.error`` with ``exc_info=True``) so the traceback is logged.
  4. The except handler still preserves the human-facing ``print(...)``
     line and ``return 1`` (control flow intact).
  5. The exception is bound to a name (no bare ``except Exception:``).

Round 390: Surface silent error in bin/paper_health_check.py check_server().
"""

import ast
from pathlib import Path

import pytest

SRC_PATH = Path("bin/paper_health_check.py")


def _load_source() -> str:
    src = SRC_PATH.read_text()
    ast.parse(src)  # raises SyntaxError on failure
    return src


def test_module_compiles():
    """bin/paper_health_check.py must be syntactically valid Python."""
    _load_source()  # raises on syntax error


def test_logging_imported_and_logger_defined():
    """The module must import logging and define a module-level logger."""
    src = _load_source()
    assert "import logging" in src, "logging must be imported"
    assert "logger = logging.getLogger(__name__)" in src, (
        "module-level logger must be defined as "
        "`logger = logging.getLogger(__name__)`"
    )


def _find_check_server_body(tree: ast.Module) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "check_server":
            return node
    return None


def _handler_for_exception(body: list[ast.stmt], type_name: str) -> ast.ExceptHandler | None:
    """Walk into nested try/except blocks; return the first handler matching ``type_name``."""
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if isinstance(node, ast.ExceptHandler) and node.type is not None:
            try:
                src = ast.unparse(node.type)
            except Exception:
                continue
            if type_name in src:
                return node
    return None


def test_check_server_handler_binds_exception():
    """The check_server except handler must bind the exception to a name (no bare except)."""
    tree = ast.parse(_load_source())
    fn = _find_check_server_body(tree)
    assert fn is not None, "check_server function must be defined"
    handler = _handler_for_exception(fn.body, "Exception")
    assert handler is not None, (
        "check_server must have an `except Exception ...:` handler for "
        "the socket / protocol failure path"
    )
    assert handler.name is not None, (
        "except handler must bind the exception to a name (e.g. "
        "`except Exception as e:`) so it can be logged with the traceback"
    )


def test_check_server_handler_logs_exception():
    """The check_server except handler must call logger.exception / logger.error(... exc_info=True)."""
    src = _load_source()
    tree = ast.parse(src)
    fn = _find_check_server_body(tree)
    assert fn is not None, "check_server function must be defined"
    handler = _handler_for_exception(fn.body, "Exception")
    assert handler is not None, "expected an `except Exception` handler in check_server"
    handler_src = ast.unparse(handler)

    # The fix must call logger.exception(...) OR logger.error(..., exc_info=True)
    # so the traceback is surfaced to the log channel.
    has_exception_log = "logger.exception" in handler_src
    has_error_with_exc_info = (
        "logger.error" in handler_src and "exc_info=True" in handler_src
    )
    assert has_exception_log or has_error_with_exc_info, (
        "check_server's except handler must call logger.exception(...) or "
        "logger.error(..., exc_info=True) so the traceback is captured.\n"
        f"Handler body:\n{handler_src}"
    )


def test_check_server_handler_preserves_print_and_return():
    """The CLI print(...) and return 1 must be preserved so behavior is unchanged."""
    src = _load_source()
    tree = ast.parse(src)
    fn = _find_check_server_body(tree)
    assert fn is not None, "check_server function must be defined"
    handler = _handler_for_exception(fn.body, "Exception")
    assert handler is not None, "expected an `except Exception` handler in check_server"
    handler_src = ast.unparse(handler)

    assert "print" in handler_src, (
        "The human-facing print(...) line must be preserved so the CLI "
        "still surfaces the error to operators running the probe by hand."
    )
    assert "return 1" in handler_src, (
        "The handler must still return 1 so the process exit code is "
        "preserved for shell pipelines / monitoring."
    )


def test_check_server_invoke_logs_and_preserves_return_code(caplog):
    """End-to-end: invoking check_server with an unreachable host logs and returns 1."""
    import logging

    from bin.paper_health_check import check_server

    with caplog.at_level(logging.ERROR, logger="bin.paper_health_check"):
        rc = check_server("127.0.0.1", 1)  # port 1 is reserved, no server there

    assert rc == 1, "check_server must return 1 on connection failure"
    # caplog records the exception; logger.exception emits an ERROR record
    # with exc_info attached, so the message should be present in the records.
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert error_records, (
        "logger.exception(...) must emit an ERROR-level record so the "
        "traceback is captured in the log stream"
    )
    # And the message should mention the host:port context.
    assert any(
        "127.0.0.1" in r.getMessage() and "1" in r.getMessage()
        for r in error_records
    ), "log message should include the host:port being probed for diagnosability"
