#!/usr/bin/env python3
"""
Regression test: bin/recorder_watchdog.py _is_pid_alive() silent error.

Verifies that the bare `except OSError:` has been replaced with
`except OSError as exc:` + debug logging.
"""

import ast
import inspect
import os
import sys

import pytest


def test_no_bare_except_oserror_in_is_process_alive():
    """Verify no bare 'except OSError:' exists in is_process_alive."""
    import bin.recorder_watchdog as mod

    source = inspect.getsource(mod.is_process_alive)
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:  # bare except
                pytest.fail("Bare except found in is_process_alive")
            # Check for bare OSError (not bound)
            if isinstance(node.type, ast.Name) and node.type.id == "OSError":
                if node.name is None:
                    pytest.fail("Bare 'except OSError:' found in is_process_alive")


def test_exception_bound_in_is_process_alive():
    """Verify exception is bound to a variable."""
    import bin.recorder_watchdog as mod

    source = inspect.getsource(mod.is_process_alive)
    assert "except OSError as exc:" in source, "Exception should be bound to 'exc'"


def test_debug_log_present_in_is_process_alive():
    """Verify debug logging is present for the OSError case."""
    import bin.recorder_watchdog as mod

    source = inspect.getsource(mod.is_process_alive)
    assert "log.debug" in source, "Debug logging should be present"


def test_module_compiles():
    """Verify the module compiles without errors."""
    import bin.recorder_watchdog as mod

    assert mod is not None
