"""
Regression tests for silent error swallows in bin/dashboard_app.py.

These tests verify that error conditions are logged rather than silently swallowed.
"""

import ast
import importlib
import logging
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


class TestDashboardAppSilentError:
    """Tests for silent error handling in dashboard_app.py."""

    def test_no_bare_pass_in_import_openpyxl(self):
        """Verify _import_openpyxl doesn't have bare 'pass' in ImportError handler."""
        source_path = (
            Path(__file__).parent.parent.parent / "bin" / "dashboard_app.py"
        )
        source = source_path.read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef)
                and node.name == "_import_openpyxl"
            ):
                for child in ast.walk(node):
                    if isinstance(child, ast.ExceptHandler):
                        if len(child.body) == 1 and isinstance(
                            child.body[0], ast.Pass
                        ):
                            pytest.fail(
                                "Found bare pass in except handler for "
                                f"{ast.unparse(child.type)}. Should use "
                                "logger.debug() to bind the exception."
                            )

    def test_openpyxl_import_failure_logs_at_debug(self, caplog):
        """Verify ImportError in _import_openpyxl logs at debug level."""
        # Force reimport so the module-level cache is empty
        sys.modules.pop("bin.dashboard_app", None)

        with patch.dict(sys.modules, {"openpyxl": None}):
            with caplog.at_level(logging.DEBUG, logger="bin.dashboard_app"):
                import bin.dashboard_app as dash

                importlib.reload(dash)
                result = dash._import_openpyxl()
                # openpyxl is unavailable (we patched it to None, so `import openpyxl`
                # will raise ImportError). The lazy helper must return None and
                # log a debug message binding the exception.
                assert result is None
                # At least one debug record should mention openpyxl
                debug_msgs = [
                    r.getMessage()
                    for r in caplog.records
                    if r.levelno == logging.DEBUG
                ]
                assert any("openpyxl" in m for m in debug_msgs), (
                    f"Expected a debug log mentioning 'openpyxl', got: {debug_msgs}"
                )
