#!/usr/bin/env python3
"""
Regression test: recorder_consumer_lite.py WebSocket close() silent error surfacing.

Verifies that bare `except Exception:` in WebSocketClient.close() is bound and logged.
This is a targeted regression test for Round 320.

Author: Autonomous Improvement Agent
"""

import ast
import sys


def test_websocket_close_no_bare_except():
    """Verify close() method has no bare except Exception: pass blocks."""
    source_file = "bin/recorder_consumer_lite.py"
    with open(source_file, encoding="utf-8") as f:
        tree = ast.parse(f.read())

    close_method = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "close":
            close_method = node
            break

    assert close_method is not None, "close() method not found in WebSocketClient"

    # Find all except handlers in close()
    bare_except_found = False
    for node in ast.walk(close_method):
        if isinstance(node, ast.ExceptHandler):
            # Bare except (no type) or except Exception: pass
            if node.type is None:
                bare_except_found = True
            elif isinstance(node.type, ast.Name) and node.type.id == "Exception":
                # Check if it's just 'pass' in the body
                if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                    bare_except_found = True

    assert not bare_except_found, "close() has bare except Exception: pass"


def test_websocket_close_logger_imported():
    """Verify logger is imported at module level."""
    source_file = "bin/recorder_consumer_lite.py"
    with open(source_file, encoding="utf-8") as f:
        content = f.read()

    assert "logger = logging.getLogger(__name__)" in content, "Module logger not found"


def test_websocket_close_debug_logs_present():
    """Verify close() method has logger.debug() calls for the exception handlers."""
    source_file = "bin/recorder_consumer_lite.py"
    with open(source_file, encoding="utf-8") as f:
        content = f.read()

    # Find the close() method
    close_start = content.find("def close(self)")
    assert close_start != -1, "close() method not found"

    close_end = content.find("\n    def ", close_start + 1)
    if close_end == -1:
        close_end = len(content)

    close_body = content[close_start:close_end]

    # Should have logger.debug calls with exception binding
    assert "logger.debug" in close_body, "logger.debug not found in close()"
    assert "send frame failed" in close_body, "send frame error log not found"
    assert "socket close failed" in close_body, "socket close error log not found"


def test_websocket_close_module_compiles():
    """Verify the module compiles without syntax errors."""
    import py_compile

    source_file = "bin/recorder_consumer_lite.py"
    try:
        py_compile.compile(source_file, doraise=True)
    except py_compile.PyCompileError as e:
        raise AssertionError(f"Module compile failed: {e}")
