#!/usr/bin/env python3
"""
Regression test: bin/v4_buyer_signed/verifier.py must surface silent errors
via logger.debug at the ``load_buyer_reference`` JSONDecodeError/OSError
swallow (line ~91). The handler must bind the exception to a name and
call logger.debug, not bare ``except (json.JSONDecodeError, OSError):``.

This test verifies:
1. The module compiles without syntax errors.
2. logging is imported and a module-level logger is defined.
3. The JSONDecodeError/OSError handler in load_buyer_reference binds
   the exception AND calls logger.debug instead of bare `pass`/return.
4. The control flow is preserved: function still returns None on
   failure (verified by invoking it with corrupt JSON).
5. The bound exception name is actually referenced in the logger call.

Round 393: Surface silent error in bin/v4_buyer_signed/verifier.py
load_buyer_reference() corrupt-JSON / read-failure handler.
"""

import ast
import tempfile
from pathlib import Path
from unittest import mock

SRC_PATH = Path("bin/v4_buyer_signed/verifier.py")


def _load_source():
    src = SRC_PATH.read_text()
    ast.parse(src)  # raises SyntaxError on failure
    return src


def test_module_compiles():
    """bin/v4_buyer_signed/verifier.py must be syntactically valid Python."""
    _load_source()


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


def test_load_buyer_reference_handler_binds_and_logs():
    """The JSONDecodeError/OSError handler in load_buyer_reference must
    bind the exception to a name and call logger.debug."""
    tree = ast.parse(_load_source())
    handlers = _find_except_in_func(tree, "load_buyer_reference")
    assert handlers, "load_buyer_reference has no except blocks"
    # Find the JSONDecodeError/OSError handler
    matching = [
        h for ln, h in handlers
        if "JSONDecodeError" in ast.unparse(h) and "OSError" in ast.unparse(h)
    ]
    assert matching, "JSONDecodeError/OSError except block not found"
    h = matching[0]
    assert h.name is not None, (
        "JSONDecodeError/OSError except must bind the exception to a name "
        "(e.g. `as exc`)"
    )
    body_src = ast.unparse(h)
    assert "logger.debug" in body_src, (
        "JSONDecodeError/OSError except must call logger.debug, not bare "
        "`pass` / `return None`"
    )


def test_load_buyer_reference_bound_name_referenced_in_body():
    """The bound exception name must appear in the handler body so the
    actual error is included in the debug log line."""
    tree = ast.parse(_load_source())
    for ln, h in _find_except_in_func(tree, "load_buyer_reference"):
        if "JSONDecodeError" not in ast.unparse(h):
            continue
        assert h.name is not None
        body_src = ast.unparse(h)
        assert h.name in body_src, (
            f"bound exception name `{h.name}` not referenced in handler body"
        )


def test_corrupt_json_still_returns_none_with_debug_log():
    """End-to-end: passing corrupt JSON to load_buyer_reference must
    return None (control flow preserved) AND emit a logger.debug
    record that includes the file path and exception."""
    from bin.v4_buyer_signed import verifier as v

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        f.write("{ this is not valid json")
        bad_path = Path(f.name)

    with mock.patch.object(v.logger, "debug") as mock_debug:
        result = v.load_buyer_reference(bad_path)

    assert result is None, (
        f"control flow broken: load_buyer_reference must still return None "
        f"on JSONDecodeError, got {result!r}"
    )
    assert mock_debug.called, (
        "logger.debug must be called when load_buyer_reference fails to parse"
    )
    # The log line must include the path and exception info so debugging
    # is actually possible.
    call_args = mock_debug.call_args
    rendered = (call_args.args[0] % call_args.args[1:]) if len(call_args.args) > 1 else call_args.args[0]
    assert str(bad_path) in rendered, (
        f"debug log must include the offending path; got: {rendered!r}"
    )


def test_oserror_still_returns_none_with_debug_log():
    """End-to-end: simulating an OSError during read_text must still
    return None and emit a logger.debug record."""
    from bin.v4_buyer_signed import verifier as v

    # Create a real file so the `p.exists()` / `p.is_file()` guard passes;
    # then patch read_text to raise OSError.
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        f.write("{}")
        real_path = Path(f.name)

    with mock.patch.object(v.logger, "debug") as mock_debug:
        with mock.patch.object(
            Path, "read_text", side_effect=OSError("simulated EBUSY")
        ):
            result = v.load_buyer_reference(real_path)

    assert result is None, "OSError path must still return None"
    assert mock_debug.called, "logger.debug must be called on OSError"
    call_args = mock_debug.call_args
    rendered = (call_args.args[0] % call_args.args[1:]) if len(call_args.args) > 1 else call_args.args[0]
    assert "simulated EBUSY" in rendered, (
        f"debug log must include the OSError message; got: {rendered!r}"
    )
