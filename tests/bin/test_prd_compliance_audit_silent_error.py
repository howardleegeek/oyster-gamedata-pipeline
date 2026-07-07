#!/usr/bin/env python3
"""Regression test: prd_compliance_audit.py should surface silent errors in H8 fallback parsing."""
import ast
import pytest
import sys
from pathlib import Path

# Import the module to verify it compiles
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import bin.prd_compliance_audit as prd_compliance_audit


class TestPrdComplianceAuditSilentError:
    """Verify silent error fixes in prd_compliance_audit.py."""

    def test_module_compiles(self):
        """Module should compile without syntax errors."""
        assert prd_compliance_audit is not None

    def test_logger_defined(self):
        """Module should have logger defined."""
        assert hasattr(prd_compliance_audit, "logger")
        assert prd_compliance_audit.logger.name == "bin.prd_compliance_audit"

    def test_no_bare_except_pass_in_target_sites(self):
        """Target sites should not have bare except: pass."""
        source_file = Path(__file__).parent.parent.parent / "bin" / "prd_compliance_audit.py"
        source = source_file.read_text()
        tree = ast.parse(source)

        # Find the _evaluate_h8 function
        target_function = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_evaluate_h8":
                target_function = node
                break

        assert target_function is not None, "_evaluate_h8 function not found"

        # Get line numbers for the fallback parsing block (lines ~265-275)
        # Look for except handlers with ValueError in this function
        for node in ast.walk(target_function):
            if isinstance(node, ast.ExceptHandler):
                # Check if this is a ValueError handler in the fallback block
                if node.type and isinstance(node.type, ast.Name) and node.type.id == "ValueError":
                    # Check if the body is just 'pass'
                    if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                        pytest.fail(
                            f"Found bare 'except ValueError: pass' at line {node.lineno}. "
                            "Should bind exception and log it."
                        )

    def test_except_binds_exception_and_logs(self):
        """Target except handlers should bind exception and call logger.debug."""
        source_file = Path(__file__).parent.parent.parent / "bin" / "prd_compliance_audit.py"
        source = source_file.read_text()
        tree = ast.parse(source)

        # Find the _evaluate_h8 function
        target_function = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_evaluate_h8":
                target_function = node
                break

        assert target_function is not None

        # Find except handlers with ValueError
        valueerror_handlers = []
        for node in ast.walk(target_function):
            if isinstance(node, ast.ExceptHandler):
                if node.type and isinstance(node.type, ast.Name) and node.type.id == "ValueError":
                    valueerror_handlers.append(node)

        assert len(valueerror_handlers) >= 2, "Expected at least 2 ValueError handlers in fallback block"

        # Each should bind exception name
        for handler in valueerror_handlers:
            assert handler.name is not None, (
                f"Except handler at line {handler.lineno} should bind exception (use 'as e')"
            )

            # Should call logger.debug somewhere in the handler
            has_logger_call = False
            for body_node in ast.walk(handler):
                if isinstance(body_node, ast.Call):
                    if isinstance(body_node.func, ast.Attribute):
                        if body_node.func.attr == "debug":
                            has_logger_call = True
                            break
            assert has_logger_call, (
                f"Except handler at line {handler.lineno} should call logger.debug"
            )
