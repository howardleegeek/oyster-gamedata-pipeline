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

    def test_audit_group_v_yavg_parse_logs_at_debug(self):
        """audit_group_v_real_footage YAVG parse should bind exc and call logger.debug.

        The YAVG parse block in audit_group_v_real_footage (B8) used to
        swallow (ValueError, IndexError) silently. If ffmpeg ever emits
        a malformed line (e.g. truncated stderr, "YAVG=NaN" on a buggy
        build), the audit must surface the failure rather than dropping
        the data point without a trace.
        """
        source_file = Path(__file__).parent.parent.parent / "bin" / "prd_compliance_audit.py"
        source = source_file.read_text()
        tree = ast.parse(source)

        # Find audit_group_v_real_footage
        target_function = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "audit_group_v_real_footage":
                target_function = node
                break
        assert target_function is not None, "audit_group_v_real_footage function not found"

        # Find the (ValueError, IndexError) handler in the YAVG parse block
        yavg_handler = None
        for node in ast.walk(target_function):
            if isinstance(node, ast.ExceptHandler) and node.type and isinstance(node.type, ast.Tuple):
                type_names = [elt.id for elt in node.type.elts if isinstance(elt, ast.Name)]
                if "ValueError" in type_names and "IndexError" in type_names:
                    yavg_handler = node
                    break
        assert yavg_handler is not None, "(ValueError, IndexError) handler not found in audit_group_v_real_footage"

        # Should bind the exception
        assert yavg_handler.name is not None, (
            f"YAVG parse except handler at line {yavg_handler.lineno} should bind exception"
        )

        # Should NOT be a bare pass
        if len(yavg_handler.body) == 1 and isinstance(yavg_handler.body[0], ast.Pass):
            pytest.fail(
                f"YAVG parse handler at line {yavg_handler.lineno} is a bare 'pass' — should call logger.debug"
            )

        # Should call logger.debug
        has_logger_call = False
        for body_node in ast.walk(yavg_handler):
            if isinstance(body_node, ast.Call) and isinstance(body_node.func, ast.Attribute):
                if body_node.func.attr == "debug":
                    has_logger_call = True
                    break
        assert has_logger_call, (
            f"YAVG parse handler at line {yavg_handler.lineno} should call logger.debug"
        )

    def test_audit_group_v_yavg_preserves_datalog_loop(self):
        """The YAVG parse handler must not break out of the for-loop.

        The fix binds `as exc` and calls logger.debug, but must NOT
        'break' or 'return' from the enclosing function — a single
        malformed YAVG line should be dropped, not abort the whole
        signal_2 verdict computation.
        """
        source_file = Path(__file__).parent.parent.parent / "bin" / "prd_compliance_audit.py"
        source = source_file.read_text()
        tree = ast.parse(source)

        target_function = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "audit_group_v_real_footage":
                target_function = node
                break
        assert target_function is not None

        yavg_handler = None
        for node in ast.walk(target_function):
            if isinstance(node, ast.ExceptHandler) and node.type and isinstance(node.type, ast.Tuple):
                type_names = [elt.id for elt in node.type.elts if isinstance(elt, ast.Name)]
                if "ValueError" in type_names and "IndexError" in type_names:
                    yavg_handler = node
                    break
        assert yavg_handler is not None

        for body_node in yavg_handler.body:
            assert not isinstance(body_node, (ast.Break, ast.Return)), (
                f"YAVG handler at line {yavg_handler.lineno} must not break/return; "
                "should drop the data point and continue the loop"
            )
