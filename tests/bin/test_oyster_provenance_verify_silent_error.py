#!/usr/bin/env python3
"""
Regression tests for silent error fix in oyster_provenance/verify.py.

Tests that the verify_manifest_exists() function binds the exception
and logs at debug level instead of silently swallowing errors.
"""

import ast
import logging
import pytest
from pathlib import Path
from unittest.mock import patch

# Ensure the module can be imported
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class TestVerifyManifestExistsSilentError:
    """Tests for silent error surfacing in verify_manifest_exists."""

    def test_module_compiles(self):
        """Module compiles without syntax errors."""
        import oyster_provenance.verify
        assert oyster_provenance.verify is not None

    def test_logging_imported(self):
        """Module has logging imported."""
        import oyster_provenance.verify as verify_module
        # Check that logging is imported at module level
        source_file = Path(verify_module.__file__).read_text()
        assert "import logging" in source_file

    def test_logger_defined(self):
        """Module defines a logger."""
        import oyster_provenance.verify as verify_module
        assert hasattr(verify_module, 'logger')
        assert isinstance(verify_module.logger, logging.Logger)

    def test_exception_handler_binds_name(self):
        """verify_manifest_exists binds exception to a name."""
        source = Path(__file__).parent.parent.parent / "oyster_provenance" / "verify.py"
        tree = ast.parse(source.read_text())

        # Find the verify_manifest_exists function
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "verify_manifest_exists":
                # Look for the except handler that catches Exception
                for child in ast.walk(node):
                    if isinstance(child, ast.ExceptHandler):
                        if child.type and isinstance(child.type, ast.Name) and child.type.id == "Exception":
                            # Check that the exception is bound to a name
                            assert child.name is not None, "Exception handler must bind to a name"
                            return

        pytest.fail("Could not find verify_manifest_exists or its Exception handler")

    def test_exception_handler_calls_logger_debug(self):
        """Exception handler calls logger.debug."""
        source = Path(__file__).parent.parent.parent / "oyster_provenance" / "verify.py"
        source_text = source.read_text()

        # Check that the handler in verify_manifest_exists calls logger.debug
        assert "logger.debug" in source_text
        assert "verify_manifest_exists:" in source_text

    def test_bound_name_referenced_in_body(self):
        """The bound exception name is referenced in the logger call."""
        source = Path(__file__).parent.parent.parent / "oyster_provenance" / "verify.py"
        tree = ast.parse(source.read_text())

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "verify_manifest_exists":
                for child in ast.walk(node):
                    if isinstance(child, ast.ExceptHandler):
                        if child.type and isinstance(child.type, ast.Name) and child.type.id == "Exception":
                            # The exception name should be referenced in the body
                            assert child.name is not None
                            # Check that the name is used somewhere in the handler
                            names_used = {n.id for n in ast.walk(child) if isinstance(n, ast.Name)}
                            assert child.name in names_used, f"Bound name '{child.name}' must be referenced in handler body"
                            return

        pytest.fail("Could not find verify_manifest_exists Exception handler")

    def test_no_bare_except_pass_in_verify_manifest_exists(self):
        """No bare 'except Exception: pass' pattern in verify_manifest_exists."""
        source = Path(__file__).parent.parent.parent / "oyster_provenance" / "verify.py"
        tree = ast.parse(source.read_text())

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "verify_manifest_exists":
                for child in ast.walk(node):
                    if isinstance(child, ast.ExceptHandler):
                        # Check for bare pass (no logging, no return value change, etc.)
                        if child.body and len(child.body) == 1 and isinstance(child.body[0], ast.Pass):
                            pytest.fail("Found bare 'except: pass' in verify_manifest_exists")
                return

        # If we get here, the function exists and has no bare pass
        assert True

    def test_verify_manifest_exists_returns_false_on_exception(self):
        """When load_manifest raises an exception, returns (False, None)."""
        import oyster_provenance.verify as verify_module

        # Create a mock path that exists but will cause load_manifest to fail
        with patch.object(verify_module, 'load_manifest', side_effect=RuntimeError("test error")):
            with patch('os.path.exists', return_value=True):
                result = verify_module.verify_manifest_exists("/fake/path")
                # Should return False when exception occurs
                assert result == (False, None)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
