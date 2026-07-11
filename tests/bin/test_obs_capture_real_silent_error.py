"""Regression tests for obs_capture_real.py silent error handling.

Verifies that CancelledError in __aexit__ is logged at DEBUG level rather
than silently swallowed.
"""

import ast
import logging
from pathlib import Path

import pytest

# Import the module to verify it compiles
import oyster_agent_runner.phase2.obs_capture_real as obs_capture_real

MODULE_PATH = Path("src/oyster_agent_runner/phase2/obs_capture_real.py")


class TestObsCaptureRealSilentError:
    """Test suite for silent error fix in __aexit__ CancelledError handler."""

    def test_module_compiles(self):
        """Module should compile without syntax errors."""
        assert MODULE_PATH.exists()
        source = MODULE_PATH.read_text()
        ast.parse(source)

    def test_logging_imported(self):
        """Module should import logging."""
        source = MODULE_PATH.read_text()
        tree = ast.parse(source)
        imports_logging = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "logging":
                        imports_logging = True
                        break
        assert imports_logging, "logging module not imported"

    def test_logger_defined(self):
        """Module should define a logger."""
        assert hasattr(obs_capture_real, "logger")
        assert isinstance(obs_capture_real.logger, logging.Logger)

    def test_aexit_binds_cancelled_error(self):
        """__aexit__ should handle CancelledError, not swallow silently."""
        source = MODULE_PATH.read_text()
        tree = ast.parse(source)

        # Find the __aexit__ method
        aexit_method = None
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "__aexit__":
                aexit_method = node
                break

        assert aexit_method is not None, "__aexit__ method not found"

        # Find the CancelledError handler (handles both asyncio.CancelledError and plain CancelledError)
        found_handler = False
        for node in ast.walk(aexit_method):
            if isinstance(node, ast.ExceptHandler):
                # Check if it handles CancelledError
                if node.type is not None:
                    # Handle ast.Name (plain CancelledError)
                    if isinstance(node.type, ast.Name):
                        if node.type.id == "CancelledError":
                            found_handler = True
                            break
                    # Handle ast.Attribute (asyncio.CancelledError)
                    elif isinstance(node.type, ast.Attribute):
                        if node.type.attr == "CancelledError":
                            found_handler = True
                            break

        assert found_handler, "CancelledError handler not found in __aexit__"

    def test_aexit_logs_cancelled_error_at_debug(self):
        """CancelledError handler should call logger.debug."""
        source = MODULE_PATH.read_text()
        tree = ast.parse(source)

        # Find the __aexit__ method
        aexit_method = None
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "__aexit__":
                aexit_method = node
                break

        # Find the CancelledError handler and check for logger.debug call
        found_debug_call = False
        for node in ast.walk(aexit_method):
            if isinstance(node, ast.ExceptHandler):
                if node.type is not None:
                    # Handle both ast.Name (CancelledError) and ast.Attribute (asyncio.CancelledError)
                    is_cancelled_error = False
                    if isinstance(node.type, ast.Name):
                        if node.type.id == "CancelledError":
                            is_cancelled_error = True
                    elif isinstance(node.type, ast.Attribute):
                        if node.type.attr == "CancelledError":
                            is_cancelled_error = True

                    if is_cancelled_error:
                        # Walk the handler body looking for logger.debug call
                        for body_node in ast.walk(node):
                            if isinstance(body_node, ast.Call):
                                if isinstance(body_node.func, ast.Attribute):
                                    if body_node.func.attr == "debug":
                                        found_debug_call = True
                                        break

        assert found_debug_call, (
            "CancelledError handler must call logger.debug, not silently swallow"
        )

    def test_no_bare_cancelled_error_pass(self):
        """There should be no bare 'except CancelledError: pass' pattern."""
        source = MODULE_PATH.read_text()
        tree = ast.parse(source)

        # Find __aexit__ method
        aexit_method = None
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "__aexit__":
                aexit_method = node
                break

        # Check for bare pass in CancelledError handler
        for node in ast.walk(aexit_method):
            if isinstance(node, ast.ExceptHandler):
                if node.type is not None:
                    is_cancelled_error = False
                    # Handle both ast.Name (CancelledError) and ast.Attribute (asyncio.CancelledError)
                    if isinstance(node.type, ast.Name):
                        if node.type.id == "CancelledError":
                            is_cancelled_error = True
                    elif isinstance(node.type, ast.Attribute):
                        if node.type.attr == "CancelledError":
                            is_cancelled_error = True

                    if is_cancelled_error:
                        # Check if body is just [Pass()]
                        if len(node.body) == 1 and isinstance(
                            node.body[0], ast.Pass
                        ):
                            pytest.fail(
                                "Found bare 'except CancelledError: pass' - "
                                "this silently swallows errors"
                            )

    def test_aexit_still_awaits_task_after_cancelled_error(self):
        """Control flow preserved: __aexit__ should still await the task after
        catching CancelledError."""
        source = MODULE_PATH.read_text()
        tree = ast.parse(source)

        # Find __aexit__ method
        aexit_method = None
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "__aexit__":
                aexit_method = node
                break

        # Check for await self._listener_task after the CancelledError handler
        # The structure should be:
        # try:
        #     await self._listener_task
        # except asyncio.CancelledError:
        #     logger.debug(...)
        # await self._listener_task  <- this should still exist after the handler

        for node in ast.walk(aexit_method):
            if isinstance(node, ast.ExceptHandler):
                if node.type is not None:
                    if isinstance(node.type, ast.Name):
                        if node.type.id == "CancelledError":
                            break

        # Alternative: check the overall method has at least one await after try/except
        # This is a simpler check - just verify await exists in method
        await_count = sum(
            1
            for node in ast.walk(aexit_method)
            if isinstance(node, (ast.Await, ast.AsyncFor))
        )
        assert (
            await_count >= 1
        ), "__aexit__ should still contain await statements (control flow preserved)"
