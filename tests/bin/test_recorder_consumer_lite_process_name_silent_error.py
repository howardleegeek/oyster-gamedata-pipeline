#!/usr/bin/env python3
"""
Regression tests for silent error swallows in bin/recorder_consumer_lite.py 
function _windows_process_name_for_pid.

This function has two bare except blocks that were silently swallowing errors.
We bind the exceptions and log via _trace() for visibility.

Round 322: Fix silent errors in _windows_process_name_for_pid
"""

import ast
from pathlib import Path

import pytest


class TestRecorderConsumerLiteProcessNameSilentError:
    """Tests for silent error handling in _windows_process_name_for_pid()."""

    def _read_source(self) -> str:
        return (
            Path(__file__).parent.parent.parent
            / "bin"
            / "recorder_consumer_lite.py"
        ).read_text()

    def test_no_bare_except_in_process_name_for_pid(self):
        """No bare ``except Exception:`` (without ``as`` binding) in _windows_process_name_for_pid."""
        source = self._read_source()
        tree = ast.parse(source)

        # Find the _windows_process_name_for_pid function
        found_function = False
        bare_excepts = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_windows_process_name_for_pid":
                found_function = True
                # Check only this function's body
                for subnode in ast.walk(node):
                    if isinstance(subnode, ast.Try):
                        for handler in subnode.handlers:
                            if handler.type is not None:
                                type_src = ast.unparse(handler.type)
                                if "Exception" in type_src and handler.name is None:
                                    bare_excepts.append(handler.lineno)

        assert found_function, "_windows_process_name_for_pid function not found"
        assert len(bare_excepts) == 0, (
            f"Found {len(bare_excepts)} bare 'except Exception:' "
            f"(no 'as' binding) at lines {bare_excepts} in _windows_process_name_for_pid. "
            f"Bind the exception and log it via _trace(...)."
        )

    def test_trace_function_available(self):
        """Module must have _trace function for debug logging."""
        source = self._read_source()
        assert "def _trace(" in source, "_trace function must be defined"

    def test_process_name_for_pid_logs_tasklist_failure(self):
        """When tasklist subprocess fails, exception should be logged."""
        source = self._read_source()
        # Look for the _trace call in _windows_process_name_for_pid
        assert "_trace(f\"_windows_process_name_for_pid: tasklist failed:" in source, (
            "logger.debug should be used to log tasklist failure"
        )

    def test_process_name_for_pid_logs_parse_failure(self):
        """When line parsing fails, exception should be logged."""
        source = self._read_source()
        # Look for the _trace call for parse failure
        assert "_trace(f\"_windows_process_name_for_pid: parse line failed:" in source, (
            "logger.debug should be used to log parse line failure"
        )

    def test_module_compiles(self):
        """Module must compile without errors."""
        import py_compile
        py_compile.compile(
            "bin/recorder_consumer_lite.py",
            doraise=True,
        )
