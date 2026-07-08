#!/usr/bin/env python3
"""
Tests for silent error handling in bin/epal_client_consent_handshake.py.

Ensures that EOFError exceptions during consent prompts are logged, not silently swallowed.
"""

import ast
import logging
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

# Ensure the bin directory is in the path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "bin"))

import epal_client_consent_handshake as consent_module


class TestEofErrorLogging:
    """Test that EOFError exceptions are logged, not silently swallowed."""

    def test_ai_consent_eof_logs_debug(self, caplog):
        """When AI consent input raises EOFError, it should log at DEBUG level."""
        with caplog.at_level(logging.DEBUG):
            with patch("builtins.input", side_effect=EOFError()):
                # Redirect stderr to suppress print output during test
                with patch("sys.stdout", new=StringIO()):
                    ai, rec, opt = consent_module.prompt_consent()

        # Verify defaults are set
        assert ai is False
        assert rec is False
        assert opt == {}

        # Verify logging occurred
        assert any(
            "EOF" in record.message and "AI training consent" in record.message
            for record in caplog.records
        ), "Expected DEBUG log for AI consent EOFError"

    def test_recording_consent_eof_logs_debug(self, caplog):
        """When recording consent input raises EOFError, it should log at DEBUG level."""
        call_count = [0]

        def input_mock(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return "y"  # AI consent - yes
            elif call_count[0] == 2:
                raise EOFError()  # Recording consent - EOF
            return "n"  # Default for any other prompts

        with caplog.at_level(logging.DEBUG):
            with patch("builtins.input", side_effect=input_mock):
                with patch("sys.stdout", new=StringIO()):
                    ai, rec, opt = consent_module.prompt_consent()

        assert ai is True  # First input succeeded
        assert rec is False  # EOFError defaulted to False

        # Verify logging occurred
        assert any(
            "EOF" in record.message and "recording consent" in record.message
            for record in caplog.records
        ), "Expected DEBUG log for recording consent EOFError"

    def test_opt_out_eof_logs_debug(self, caplog):
        """When opt-out input raises EOFError, it should log at DEBUG level."""
        call_count = [0]

        def input_mock(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return "y"  # AI consent - yes
            elif call_count[0] == 2:
                return "y"  # Recording consent - yes
            elif call_count[0] == 3:
                raise EOFError()  # First opt-out - EOF
            return "n"  # Default

        with caplog.at_level(logging.DEBUG):
            with patch("builtins.input", side_effect=input_mock):
                with patch("sys.stdout", new=StringIO()):
                    ai, rec, opt = consent_module.prompt_consent()

        assert ai is True
        assert rec is True

        # Verify logging occurred for opt-out
        assert any(
            "EOF" in record.message and "opt-out" in record.message.lower()
            for record in caplog.records
        ), "Expected DEBUG log for opt-out EOFError"


class TestNoBareExceptPass:
    """Test that there are no bare except with pass in the target module."""

    def test_no_bare_eof_error_pass(self):
        """Verify there are no bare 'except EOFError: pass' patterns."""
        source_path = Path(__file__).parent.parent.parent / "bin" / "epal_client_consent_handshake.py"
        source = source_path.read_text()
        tree = ast.parse(source)

        class BareExceptPassFinder(ast.NodeVisitor):
            def __init__(self):
                self.violations = []

            def visit_ExceptHandler(self, node):
                # Check if this is a bare except (no type) or except EOFError without name
                if node.type is None:
                    if node.body == [ast.Pass()]:
                        self.violations.append("bare except with pass found")
                elif isinstance(node.type, ast.Name) and node.type.id == "EOFError":
                    if node.body == [ast.Pass()]:
                        self.violations.append("except EOFError: pass found")
                self.generic_visit(node)

        finder = BareExceptPassFinder()
        finder.visit(tree)
        assert len(finder.violations) == 0, f"Found violations: {finder.violations}"
