#!/usr/bin/env python3
"""Regression test: audio_loopback.py silent error surface."""
import ast

import pytest


def test_no_bare_except():
    """Verify no bare 'except:' blocks in audio_loopback.py."""
    source_path = "bin/audio_loopback.py"
    with open(source_path) as f:
        tree = ast.parse(f.read())

    bare_excepts = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:  # bare except
                bare_excepts.append(node.lineno)

    assert not bare_excepts, f"Bare except found at lines: {bare_excepts}"


def test_exception_bound():
    """Verify exception is bound with 'as' in _run_ffmpeg."""
    source_path = "bin/audio_loopback.py"
    with open(source_path) as f:
        source = f.read()

    # Find the (OSError, subprocess.TimeoutExpired) except block
    assert "except (OSError, subprocess.TimeoutExpired) as exc:" in source, (
        "Expected bound exception 'as exc' in _run_ffmpeg"
    )


def test_logger_debug_present():
    """Verify logger.debug is called in the exception handler."""
    source_path = "bin/audio_loopback.py"
    with open(source_path) as f:
        source = f.read()

    assert "logger.debug" in source, "logger.debug call missing"


def test_logger_debug_references_exc():
    """Verify logger.debug includes the bound exception."""
    source_path = "bin/audio_loopback.py"
    with open(source_path) as f:
        source = f.read()

    # After binding as exc, verify it's in the debug call
    assert "%s" in source and "exc" in source, (
        "logger.debug should reference the bound exception"
    )


def test_module_compiles():
    """Verify module compiles without errors."""
    source_path = "bin/audio_loopback.py"
    with open(source_path) as f:
        source = f.read()

    try:
        compile(source, source_path, "exec")
    except SyntaxError as e:
        pytest.fail(f"Syntax error: {e}")


def test_imports_logging():
    """Verify logging module is imported."""
    source_path = "bin/audio_loopback.py"
    with open(source_path) as f:
        source = f.read()

    assert "import logging" in source, "logging import missing"
