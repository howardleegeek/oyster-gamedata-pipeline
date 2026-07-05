#!/usr/bin/env python3
"""
Regression tests for dashboard/login_page.py silent error handling.

Ensures that bare `except Exception:` blocks are replaced with proper
exception binding and logging for diagnostics.
"""

import ast
from pathlib import Path

import pytest


def get_login_page_path():
    """Get path to login_page.py."""
    return Path(__file__).parent.parent.parent / "dashboard" / "login_page.py"


class TestLoginPageSilentErrorHandling:
    """Test suite for silent error handling in login_page.py."""

    def setup_method(self):
        """Setup - read the source file."""
        self.source_path = get_login_page_path()
        with open(self.source_path, "r") as f:
            self.source = f.read()
        self.tree = ast.parse(self.source)

    def test_no_bare_except_exception(self):
        """Verify no bare 'except Exception:' without binding."""
        # Find all except handlers
        bare_excepts = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ExceptHandler):
                # Check if it's catching Exception without binding
                if node.type is None:
                    bare_excepts.append(node.lineno)
                elif isinstance(node.type, ast.Name) and node.type.id == "Exception":
                    if node.name is None:
                        bare_excepts.append(node.lineno)
        
        assert len(bare_excepts) == 0, f"Found bare 'except Exception:' at lines: {bare_excepts}"

    def test_logger_imported(self):
        """Verify logger is imported at module level."""
        assert "logger" in self.source, "logger should be imported"
        # Check it's actually used
        assert "logger.debug" in self.source, "logger.debug should be used"

    def test_logout_error_logged(self):
        """Verify logout() function logs on exception."""
        # Check that the logout function has logger.debug for the exception
        assert "Logout request failed" in self.source, "logout should log 'Logout request failed'"

    def test_refresh_token_error_logged(self):
        """Verify refresh_token() function logs on exception."""
        # Check that refresh_token has logger.debug for the exception
        assert "Token refresh failed" in self.source, "refresh_token should log 'Token refresh failed'"

    def test_module_compiles(self):
        """Verify module compiles without errors."""
        # This should not raise
        ast.parse(self.source)
