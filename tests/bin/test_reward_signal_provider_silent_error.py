"""
Regression tests for silent error swallows in bin/reward_signal_provider.py.

These tests verify that error conditions are logged rather than silently swallowed.

Round 400: Surface silent error in reward_signal_provider.py
load_config_from_file() ImportError handler (the PyYAML-missing fallback).
"""

import ast
import re
from pathlib import Path

SRC_PATH = Path("bin/reward_signal_provider.py")


def _load_tree():
    src = SRC_PATH.read_text()
    ast.parse(src)  # raises SyntaxError on failure
    return src, ast.parse(src)


def test_module_compiles():
    """The source file must parse without SyntaxError."""
    src = SRC_PATH.read_text()
    ast.parse(src)


def test_load_config_from_file_imports_logging():
    """bin/reward_signal_provider.py must import logging and define a module logger."""
    src = SRC_PATH.read_text()
    assert "import logging" in src, (
        "bin/reward_signal_provider.py must import logging "
        "to surface silent error swallows"
    )
    # Look for module-level logger definition
    assert re.search(
        r"^logger\s*=\s*logging\.getLogger\(__name__\)",
        src,
        re.MULTILINE,
    ), "Expected module-level logger = logging.getLogger(__name__)"


def test_load_config_from_file_except_binds_exception():
    """The ImportError handler in load_config_from_file() must bind the exception."""
    _src, tree = _load_tree()
    found = False
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "load_config_from_file"
        ):
            for child in ast.walk(node):
                if (
                    isinstance(child, ast.ExceptHandler)
                    and child.name is not None
                ):
                    assert child.type is not None
                    if isinstance(child.type, ast.Name):
                        assert child.type.id == "ImportError", (
                            f"Expected ImportError, got {child.type.id}"
                        )
                    found = True
    assert found, (
        "load_config_from_file() must bind the ImportError "
        "to a name (e.g., 'except ImportError as exc:')"
    )


def test_load_config_from_file_import_error_logs_at_debug():
    """The ImportError handler must call logger.debug with context."""
    src = SRC_PATH.read_text()
    # The ImportError handler should call logger.debug (comments may precede it)
    pattern = r"except\s+ImportError\s+as\s+\w+:.*?logger\.debug\("
    match = re.search(pattern, src, re.DOTALL)
    assert match, (
        "ImportError handler must call logger.debug() with context "
        "(e.g., 'logger.debug(\"...\", path, exc)')"
    )


def test_load_config_from_file_no_bare_except_pass():
    """No bare 'except ImportError: pass' or 'except ImportError:' remains."""
    _src, tree = _load_tree()
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type is not None and isinstance(node.type, ast.Name):
                if node.type.id == "ImportError":
                    # Handler must bind the exception name
                    assert node.name is not None, (
                        "Bare 'except ImportError:' (no 'as exc') "
                        "silently swallows the error — must bind to a name "
                        "and log."
                    )


def test_load_config_from_file_still_falls_back_to_json():
    """The PyYAML-missing fallback path (json.loads) must remain intact."""
    src = SRC_PATH.read_text()
    # After the ImportError handler, the json.loads(content) fallback must still
    # execute. The control-flow contract: YAML branch missing → JSON fallback.
    pattern = (
        r"except\s+ImportError\s+as\s+\w+:.*?json\.loads\(content\)"
    )
    match = re.search(pattern, src, re.DOTALL)
    assert match, (
        "load_config_from_file() must still fall back to json.loads(content) "
        "when PyYAML is missing — control flow preserved."
    )


def test_logger_named_after_module():
    """The module-level logger must be named after the module (__name__)."""
    src = SRC_PATH.read_text()
    assert re.search(
        r"^logger\s*=\s*logging\.getLogger\(__name__\)",
        src,
        re.MULTILINE,
    )


def test_logger_uses_lazy_percent_formatting():
    """logger.debug must use lazy %s formatting (not f-string)."""
    src = SRC_PATH.read_text()
    # Look for logger.debug(...) calls — they should not be f-strings
    for m in re.finditer(r"logger\.debug\((.*?)\)", src, re.DOTALL):
        call = m.group(1)
        # Strip leading whitespace/newlines for the prefix check
        prefix = call.lstrip()
        assert not prefix.startswith('f"') and not prefix.startswith("f'"), (
            f"logger.debug must use lazy %s formatting, not f-string. "
            f"Saw: logger.debug({prefix[:40]}...)"
        )
